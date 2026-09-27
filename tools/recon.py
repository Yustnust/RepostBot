"""
阶段1 勘察工具：打开东方律师网 OA，人工导航 + 自动导出页面结构。

用途
----
在真实页面上确认「重发」按钮长什么样、列表页能否读到发布时间、有没有 iframe 等。
阶段1 未完成前，不要写核心发布逻辑——否则选择器全是猜的。

用法
----
    python tools/recon.py                  # headful（默认，推荐）
    python tools/recon.py --headless       # 无头模式
    python tools/recon.py --firm firm_a    # 指定律所，登录态存到 .auth/firm_a.json

启动后：
    1. 脚本尝试自动登录（需 OA_USER / OA_PASS，缺失会提示输入）
    2. 无论自动登录是否成功，都会在浏览器里停下等你
    3. 你在浏览器里手动导航到任意页面
    4. 回到控制台按回车 → 导出当前页面的 HTML / 截图 / 表单元素 / 表格

控制台命令：
    回车          导出当前页面结构
    go <url>      跳转并导出
    click <文本>   点击第一个包含该文本的可点击元素，2 秒后导出
    links <文本>   列出包含该文本的所有链接（href + 文本）
    shot          只截图
    url           打印当前 URL
    save          保存登录态
    q             退出
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit(
        "未安装 playwright。请先执行：\n"
        "  pip install -r requirements.txt\n"
        "  playwright install chromium"
    )

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv 非必需
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = REPO_ROOT / "recon_output"
AUTH_DIR = REPO_ROOT / ".auth"

# 实测（2026-09-27）：
#   oa.lawyers.org.cn/login.jsp -> 200 可访问
#   oa.lawyers.org.cn/hall/     -> 登录后落地页，「事务所招聘」入口在此页
#   recruitment.lawyers.org.cn/manage/ -> 404（未登录不可见，需从 OA 内部进入）
#   passport.lawyers.org.cn     -> 不通
# 因此默认从 OA 大厅起步；未登录会自动跳登录页，登录后回到大厅找「事务所招聘」
DEFAULT_START_URL = "https://oa.lawyers.org.cn/hall/"

# 登录表单候选选择器。勘察完成后，把确认可用的回填到 src/selectors.py
USERNAME_CANDIDATES = [
    'input[name="username"]', 'input[name="user"]', 'input[name="loginName"]',
    'input[name="account"]', 'input[name="j_username"]', 'input#username',
    'input#user', 'input[type="text"]',
]
PASSWORD_CANDIDATES = [
    'input[name="password"]', 'input[name="pass"]', 'input[name="j_password"]',
    'input#password', 'input[type="password"]',
]
SUBMIT_CANDIDATES = [
    'button[type="submit"]', 'input[type="submit"]',
    'button:has-text("登录")', 'a:has-text("登录")',
    'input[value="登录"]', 'text=登录',
]


# --------------------------------------------------------------------------
# 页面导出
# --------------------------------------------------------------------------

JS_ELEMENTS = """
() => {
  const out = [];
  document.querySelectorAll('input,select,textarea,button,a').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;          // 跳过不可见元素
    const cls = (el.className && el.className.toString().trim().slice(0, 80)) || '';
    let suggest = '';
    if (el.id) suggest = '#' + el.id;
    else if (el.getAttribute('name')) suggest = el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    else if (cls) suggest = el.tagName.toLowerCase() + '.' + cls.split(/\\s+/).join('.');
    else if ((el.innerText || '').trim()) suggest = 'text=' + el.innerText.trim().slice(0, 20);
    out.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      name: el.getAttribute('name'),
      id: el.id,
      cls: cls,
      placeholder: el.getAttribute('placeholder'),
      value: el.tagName === 'INPUT' ? el.value : undefined,
      href: el.getAttribute('href'),
      onclick: el.getAttribute('onclick'),
      text: (el.innerText || '').trim().slice(0, 60),
      suggest: suggest
    });
  });
  return out;
}
"""

JS_GRID = """
() => {
  const rows = Array.from(document.querySelectorAll('div.x-grid3-row'));
  return rows.map((r, i) => ({
    index: i,
    id: r.id,
    cls: r.className,
    cells: Array.from(r.querySelectorAll('td')).map(td => ({
      cls: td.className,
      text: (td.innerText || '').trim().slice(0, 40)
    }))
  }));
}
"""

JS_TABLES = """
() => Array.from(document.querySelectorAll('table')).map((t, i) => ({
  index: i,
  cls: (t.className || '').toString().slice(0, 60),
  headers: Array.from(t.querySelectorAll('th')).map(th => th.innerText.trim()),
  rows: Array.from(t.querySelectorAll('tr')).slice(0, 60).map(
    tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
  )
}))
"""


def dump_page(page, outdir: Path, label: str) -> None:
    """导出当前页面：截图 + HTML + 表单元素 + 表格 + frame 列表"""
    outdir.mkdir(parents=True, exist_ok=True)

    page.screenshot(path=str(outdir / f"{label}.png"), full_page=True)
    (outdir / f"{label}.html").write_text(page.content(), encoding="utf-8")

    elements = page.evaluate(JS_ELEMENTS)
    (outdir / f"{label}.elements.json").write_text(
        json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [f"# {label}  URL: {page.url}", ""]
    for e in elements:
        parts = [f"<{e['tag']}"]
        if e["type"]:
            parts.append(f" type={e['type']}")
        if e["name"]:
            parts.append(f" name={e['name']}")
        if e["id"]:
            parts.append(f" id={e['id']}")
        if e["placeholder"]:
            parts.append(f' placeholder="{e["placeholder"]}"')
        if e["text"]:
            parts.append(f' text="{e["text"]}"')
        if e["href"]:
            parts.append(f' href="{e["href"]}"')
        parts.append(f"  ==>  {e['suggest']}")
        lines.append(" ".join(parts))
    (outdir / f"{label}.elements.txt").write_text("\n".join(lines), encoding="utf-8")

    tables = page.evaluate(JS_TABLES)
    (outdir / f"{label}.tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    frames = [{"url": f.url, "name": f.name} for f in page.frames]
    (outdir / f"{label}.frames.json").write_text(
        json.dumps(frames, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[已导出] {label}")
    print(f"  URL     : {page.url}")
    print(f"  Title   : {page.title()}")
    print(f"  元素数  : {len(elements)}    表格数: {len(tables)}    frame 数: {len(frames)}")
    print(f"  目录    : {outdir}")

    for t in tables:
        if not t["headers"] and not t["rows"]:
            continue
        print(f"  ── 表 {t['index']} 表头: {t['headers']}")
        for row in t["rows"][:3]:
            print(f"       {row}")
        if len(t["rows"]) > 3:
            print(f"       ... 共 {len(t['rows'])} 行")

    other_frames = [f for f in frames if f["url"] not in ("", "about:blank") and f["url"] != page.url]
    if other_frames:
        print("  ⚠ 页面存在 iframe，选择器需用 frame_locator 处理：")
        for f in other_frames:
            print(f"       name={f['name']}  url={f['url']}")


# --------------------------------------------------------------------------
# 交互动作
# --------------------------------------------------------------------------

def auto_login(page, user: str, pwd: str) -> bool:
    """尽力尝试自动登录，失败不影响后续人工操作"""
    filled = 0
    for sel in USERNAME_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.fill(user)
                filled += 1
                print(f"  用户名已填入：{sel}")
                break
        except Exception:
            continue
    for sel in PASSWORD_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.fill(pwd)
                filled += 1
                print(f"  密码已填入：{sel}")
                break
        except Exception:
            continue
    if filled < 2:
        print("  ⚠ 未能自动定位到账号/密码输入框，请手动输入")
        return False
    for sel in SUBMIT_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                print(f"  已点击提交：{sel}")
                return True
        except Exception:
            continue
    print("  ⚠ 未找到提交按钮，请手动点击登录")
    return False


def action_click(page, text: str, outdir: Path, counter: list) -> None:
    selectors = [
        f'a:has-text("{text}")',
        f'button:has-text("{text}")',
        f'input[value*="{text}"]',
        f'//*[self::a or self::button or self::span or self::div][contains(normalize-space(.), "{text}")]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                page.wait_for_timeout(2000)
                counter[0] += 1
                dump_page(page, outdir, f"step_{counter[0]:02d}_click_{text}")
                return
        except Exception:
            continue
    print(f"  未找到包含「{text}」的可点击元素")


def action_links(page, text: str) -> None:
    js = """
    (t) => Array.from(document.querySelectorAll('a')).filter(
        a => (a.innerText || '').includes(t)
    ).map(a => ({text: (a.innerText||'').trim().slice(0,40), href: a.getAttribute('href'), onclick: a.getAttribute('onclick')}))
    """
    found = page.evaluate(js, text)
    if not found:
        print(f"  没有包含「{text}」的链接")
    for f in found:
        print(f"  {f['text']}  |  href={f['href']}  |  onclick={f['onclick']}")


def refresh_page(holder):
    """若点击后在新标签页打开了页面，自动跟随到最新标签页。

    否则会一直导出最初那个 OA 大厅标签页，看起来就像「点了没反应」。
    用 use <序号> 手动指定后进入锁定模式，不再自动跟随。
    """
    ctx = holder["context"]
    pages = [pg for pg in ctx.pages if not pg.is_closed()]
    if not pages:
        return holder["page"]
    if holder.get("locked"):
        if holder["page"] in pages:
            return holder["page"]
        holder["locked"] = False  # 锁定的页已关闭，恢复自动跟随
    latest = pages[-1]
    if latest != holder["page"]:
        holder["page"] = latest
        print(f"\n[自动跟随] 检测到新标签页，已切换到：{latest.url}")
    return holder["page"]


def action_grid(page, outdir: Path, label_prefix: str = "grid") -> None:
    """导出 ExtJS 网格的行结构（行 id / class / 每个单元格 class 与文本）"""
    data = page.evaluate(JS_GRID)
    name = f"{label_prefix}_{datetime.now().strftime('%H%M%S')}"
    (outdir / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[网格结构] 共 {len(data)} 行，已保存到 {name}.json")
    for r in data[:3]:
        print(f"  行{r['index']}  id={r['id']}")
        print(f"        class={r['cls']}")
        print(f"        单元格: {[c['text'] for c in r['cells']]}")


def action_tabs(holder) -> None:
    ctx = holder["context"]
    for i, pg in enumerate(ctx.pages):
        mark = " *" if pg == holder["page"] else ""
        print(f"  [{i}] {pg.url}{mark}")


def print_help() -> None:
    print("""
命令：
  回车            导出当前页面结构（自动跟随最新标签页）
  go <url>        跳转并导出
  click <文本>     点击第一个包含该文本的可点击元素，2 秒后自动导出
  links <文本>     列出包含该文本的所有链接
  grid            导出表格行结构（ExtJS 网格：行 id/class、单元格 column class）
  tabs            列出所有标签页（* 为当前正在导出的页）
  use <序号>       手动锁定到某个标签页（配合 tabs 使用）
  shot            只截图
  url             打印当前 URL
  save            保存登录态到 .auth/
  help            显示本帮助
  q               退出
""")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="东方律师网 OA 页面勘察工具（阶段1）")
    parser.add_argument("--url", default=os.getenv("OA_START_URL", DEFAULT_START_URL),
                        help="起始 URL，默认 https://oa.lawyers.org.cn/")
    parser.add_argument("--firm", default="default", help="律所标识，用于保存登录态文件名")
    parser.add_argument("--headless", action="store_true", help="无头模式（默认 headful）")
    parser.add_argument("--browser", default="chromium", choices=["chromium", "msedge", "chrome"],
                        help="用哪个浏览器跑：chromium=Playwright 自带（默认）；"
                             "msedge=本机 Microsoft Edge（站点在自带浏览器打不开时改用）；chrome=本机 Chrome")
    parser.add_argument("--console", action="store_true", help="打印页面控制台输出，用于排查加载失败")
    parser.add_argument("--storage", default=None, help="登录态文件路径，默认 .auth/<firm>.json")
    parser.add_argument("--out", default=None, help="输出目录，默认 recon_output/<时间戳>")
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv(REPO_ROOT / ".env")

    outdir = Path(args.out) if args.out else OUTPUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir.mkdir(parents=True, exist_ok=True)

    storage = Path(args.storage) if args.storage else AUTH_DIR / f"{args.firm}.json"
    storage.parent.mkdir(parents=True, exist_ok=True)

    user = os.getenv("OA_USER", "").strip()
    pwd = os.getenv("OA_PASS", "").strip()
    if not user:
        user = input("OA 账号（留空则跳过自动登录）：").strip()
    if user and not pwd:
        pwd = getpass.getpass("OA 密码：")

    with sync_playwright() as p:
        launch_kwargs = {"headless": args.headless, "slow_mo": 100}
        if args.browser == "msedge":
            launch_kwargs["channel"] = "msedge"
        elif args.browser == "chrome":
            launch_kwargs["channel"] = "chrome"
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception as e:
            sys.exit(f"浏览器启动失败（{args.browser}）：{e}\n"
                     f"若使用 msedge/chrome 仍失败，请确认已安装该浏览器，或改回默认 chromium。")
        context = browser.new_context(
            storage_state=str(storage) if storage.exists() else None,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        # 原生 confirm/alert/prompt 弹窗：Playwright 默认自动取消且无任何提示，
        # 这会让「更新排序时间」的确认框看起来像没反应。这里改为记录内容 + 始终取消。
        def handle_dialog(dialog):
            print(f"\n[捕获弹窗] 类型={dialog.type}")
            print(f"[弹窗内容] {dialog.message}")
            print("[处理方式] 勘察模式下自动点「取消」，不会真的提交")
            dialog.dismiss()

        page.on("dialog", handle_dialog)

        def attach(pg):
            """新开的标签页也要挂上监听，否则在新页点按钮依旧静默取消"""
            pg.set_default_timeout(30000)
            pg.on("dialog", handle_dialog)
            if args.console:
                pg.on("console", lambda m: print(f"[console.{m.type}] {m.text[:200]}"))
            pg.on("pageerror", lambda e: print(f"[页面JS错误] {str(e)[:200]}"))

        attach(page)
        context.on("page", attach)

        holder = {"context": context, "page": page}

        print(f"\n打开 {args.url} ...")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  打开失败：{e}\n  可能是网络问题，或需要配置代理。")

        if user and pwd:
            print("尝试自动登录 ...")
            auto_login(page, user, pwd)
            page.wait_for_timeout(2000)

        print("\n" + "=" * 60)
        print("请在浏览器中手动完成登录并导航到「事务所招聘」页面。")
        print("到达目标页面后，回到本控制台按回车，即可导出该页面结构。")
        print("=" * 60)
        print_help()

        counter = [0]
        while True:
            try:
                cmd = input("\n命令> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            low = cmd.lower()
            pg = refresh_page(holder)

            if low in ("q", "quit", "exit"):
                break
            if low == "":
                counter[0] += 1
                print(f"  准备导出的页面：{pg.url}")
                dump_page(pg, outdir, f"step_{counter[0]:02d}")
            elif low == "help":
                print_help()
            elif low == "url":
                print(f"  {pg.url}")
            elif low == "grid":
                action_grid(pg, outdir)
            elif low == "tabs":
                action_tabs(holder)
            elif low.startswith("use "):
                idx = cmd[4:].strip()
                try:
                    target = context.pages[int(idx)]
                except Exception:
                    print("  用法：use <序号>，先用 tabs 查看序号")
                    continue
                holder["page"] = target
                holder["locked"] = True
                print(f"  已锁定到标签页 [{idx}]：{target.url}")
            elif low == "shot":
                counter[0] += 1
                pg.screenshot(path=str(outdir / f"step_{counter[0]:02d}.png"), full_page=True)
                print(f"  已截图：step_{counter[0]:02d}.png")
            elif low == "save":
                context.storage_state(path=str(storage))
                print(f"  登录态已保存：{storage}")
            elif low.startswith("go "):
                target = cmd[3:].strip()
                pg.goto(target, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(1500)
                counter[0] += 1
                dump_page(pg, outdir, f"step_{counter[0]:02d}")
            elif low.startswith("click "):
                action_click(pg, cmd[6:].strip(), outdir, counter)
            elif low.startswith("links "):
                action_links(pg, cmd[6:].strip())
            else:
                print("  未知命令，输入 help 查看用法")

        context.storage_state(path=str(storage))
        browser.close()

    print(f"\n登录态已保存：{storage}")
    print(f"勘察产物目录：{outdir}")
    print("\n下一步：把结果填进 docs/页面结构.md，并据此填充 src/selectors.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
