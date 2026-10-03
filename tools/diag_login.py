"""登录诊断工具（排查为什么 ensure_login 失败）。

用途
----
`publisher.ensure_login()` 内部吞异常，只知道「失败」但不知道为什么。本工具
按同样的顺序复走一遍，把每一步的状态打印出来，并留下截图 / HTML 证据。

用法
----
    python tools/diag_login.py                       # 无头（与定时任务一致）
    python tools/diag_login.py --headful             # 有头（对比用）
    python tools/diag_login.py --browser chromium    # 换浏览器对比

    # 入口巡检：空 cookie 打开各常见入口，看最终落在哪个登录页（不需要账号密码）
    python tools/diag_login.py --entries
    python tools/diag_login.py --url https://www.lawyers.org.cn/openid/login.jsp

产物：logs/diag_<时间戳>/ 下有 step_*.png / entry_*.png 与 HTML
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("未安装 playwright：pip install -r requirements.txt && playwright install chromium")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

import config  # noqa: E402
import page_selectors as S  # noqa: E402
import storage as storage_mod  # noqa: E402

OUT_ROOT = REPO_ROOT / "logs"


def snapshot(page, outdir: Path, label: str) -> None:
    """截图 + HTML 落盘，出问题时靠它们还原现场"""
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(outdir / f"{label}.png"), full_page=True)
        (outdir / f"{label}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"    （快照失败：{e}）")


def probe_fields(page) -> dict:
    """列出页面上所有可见的 input，看看登录表单到底长什么样"""
    js = """
    () => Array.from(document.querySelectorAll('input,button')).map(el => {
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type'),
        name: el.getAttribute('name'),
        id: el.id,
        cls: (el.className || '').toString().slice(0, 60),
        visible: !!(r.width || r.height),
        text: (el.innerText || '').trim().slice(0, 20),
      };
    }).filter(x => x.visible)
    """
    try:
        return {"fields": page.evaluate(js)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def try_login_page(page, url: str, user: str, pwd: str, outdir: Path,
                   idx: int, timeout_ms: int) -> tuple[bool, str]:
    """在指定登录页尝试登录，返回 (是否进入管理页, 说明)"""
    label = f"step_{idx:02d}_login_{'openid' if 'openid' in url else 'oa'}"
    print(f"\n  [{idx}] 打开登录页 {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:  # noqa: BLE001
        print(f"      goto 失败：{type(e).__name__}: {e}")
        return False, "goto 失败"
    page.wait_for_timeout(1000)
    print(f"      落地 URL：{page.url}")
    snapshot(page, outdir, label)

    try:
        page.wait_for_selector(S.LOGIN_USERNAME, timeout=min(timeout_ms, 15000))
        print("      已等到 #j_username")
    except Exception:
        print("      [!!] 15 秒内没等到 #j_username，打印页面上可见的表单元素：")
        info = probe_fields(page)
        for f in info.get("fields", []):
            print(f"        <{f['tag']}> type={f['type']} name={f['name']} "
                  f"id={f['id']} class={f['cls']} text={f['text']!r}")
        if info.get("error"):
            print(f"        （读取失败：{info['error']}）")

    filled = 0
    for sel in S.LOGIN_USERNAME_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(user)
            print(f"      用户名已填入：{sel}")
            filled += 1
            break
    for sel in S.LOGIN_PASSWORD_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(pwd)
            print("      密码已填入（不回显）")
            filled += 1
            break
    if filled < 2:
        return False, f"未填齐表单（filled={filled}/2）"

    for sel in S.LOGIN_SUBMIT_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            try:
                loc.click(timeout=8000)
            except Exception:
                loc.evaluate("el => el.click()")
            print(f"      已点击提交：{sel}")
            break
    else:
        return False, "未找到可见的提交按钮"

    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    print(f"      提交后 URL：{page.url}")
    snapshot(page, outdir, f"{label}_after_submit")

    # 登录成功后进管理页看看
    try:
        page.goto(S.RECRUIT_MANAGER_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:  # noqa: BLE001
        print(f"      进入管理页失败（站点会中断导航）：{type(e).__name__}")
    page.wait_for_timeout(1500)
    print(f"      管理页 URL：{page.url}")
    snapshot(page, outdir, f"{label}_manager")
    try:
        page.wait_for_selector(S.GRID_ROW, timeout=20000)
        print("      [ok] 列表已渲染，登录有效")
        return True, "登录成功"
    except Exception:
        info = probe_fields(page)
        if page.url and any(m in page.url for m in S.LOGIN_URL_MARKERS):
            return False, "提交后又回到登录页（账号密码可能有误，或需要验证码）"
        return False, f"未渲染列表（可见元素 {len(info.get('fields', []))} 个）"


# 控制台可能是 GBK，不要用 ✓ / ⚠ 这类非 GBK 字符，否则 UnicodeEncodeError
OK = "[ok]"
BAD = "[!!]"


# 常见入口：站点改造时，不同入口可能指向不同的登录页，需要逐一对照
COMMON_ENTRIES = [
    ("官网首页", "https://www.lawyers.org.cn/"),
    ("OA 大厅", "https://oa.lawyers.org.cn/hall/"),
    ("OA 登录页", "https://oa.lawyers.org.cn/login.jsp"),
    ("统一登录 openid", "https://www.lawyers.org.cn/openid/login.jsp"),
    ("passport3", "https://passport3.lawyers.org.cn/login.jsp"),
    ("招聘管理页", "https://recruitment.lawyers.org.cn/manager/index.jsp"),
]


def probe_entry(br, url: str, outdir: Path, idx: int, timeout_ms: int) -> dict:
    """用**干净上下文**（不带任何 cookie）打开一个入口，看它最终停在哪个登录页。

    带登录态访问时页面会直接跳走，看不到登录表单——所以这里必须开新的空上下文。
    只观察，不填表、不提交。
    """
    context = br.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                             viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(timeout_ms)
    info: dict = {"url": url, "error": None}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:  # noqa: BLE001
        # 保留完整原因：net::ERR_XXX / 超时 / 证书错误，靠它才能区分「跳转被中断」和「连不上」
        info["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
    page.wait_for_timeout(1500)
    info["landed"] = page.url
    info["title"] = page.title()
    probe = probe_fields(page)
    info["fields"] = probe.get("fields", [])
    try:
        page.screenshot(path=str(outdir / f"entry_{idx:02d}.png"), full_page=True)
    except Exception:
        pass
    context.close()
    return info


def run_entries(br, outdir: Path, entries: list[tuple[str, str]], timeout_ms: int) -> None:
    """入口巡检：对比各个入口最终落在哪个登录页、表单长什么样"""
    print("\n" + "=" * 64)
    print("入口巡检（空 cookie 访问，只观察不提交）")
    print("=" * 64)
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (name, url) in enumerate(entries, start=1):
        info = probe_entry(br, url, outdir, i, timeout_ms)
        results.append((name, info))
        print(f"\n  [{i}] {name}  {url}")
        if info["error"]:
            print(f"      goto 异常：{info['error']}（站点常在未登录时中断导航，属正常）")
        print(f"      最终停在：{info['landed']}")
        print(f"      标题    ：{info['title']}")
        inputs = [f for f in info["fields"] if f["tag"] == "input"]
        buttons = [f for f in info["fields"] if f["tag"] == "button"]
        if inputs:
            for f in inputs:
                print(f"      表单域  ：<input> type={f['type']} name={f['name']} id={f['id']}")
        else:
            print("      表单域  ：（页面上看不到可见的 input）")
        for f in buttons:
            print(f"      按钮    ：{f['text']!r}")

    print("\n" + "-" * 64)
    print("小结（最终落地的登录页）")
    seen: dict[str, list[str]] = {}
    for name, info in results:
        seen.setdefault(info["landed"], []).append(name)
    for landed, names in seen.items():
        print(f"  {landed}")
        print(f"      <- {', '.join(names)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="登录链路诊断")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--browser", default=None, choices=["chromium", "msedge", "chrome"])
    parser.add_argument("--timeout", type=int, default=45000, help="单次超时毫秒，默认 45000")
    parser.add_argument("--entries", action="store_true",
                        help="只做入口巡检：空 cookie 逐个打开常见入口，看落在哪个登录页")
    parser.add_argument("--url", default=None,
                        help="只巡检这一个入口，例：--url https://www.lawyers.org.cn/")
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv(REPO_ROOT / ".env")

    browser = args.browser or config.env("BROWSER", "msedge" if sys.platform == "win32" else "chromium")
    outdir = OUT_ROOT / f"diag_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 入口巡检：不需要账号密码，也不做任何提交，可以直接跑
    if args.entries or args.url:
        entries = [("指定入口", args.url)] if args.url else COMMON_ENTRIES
        with sync_playwright() as p:
            launch_kwargs = {"headless": not args.headful}
            if browser in ("msedge", "chrome"):
                launch_kwargs["channel"] = browser
            br = p.chromium.launch(**launch_kwargs)
            print(f"浏览器     : {browser}（{'有头' if args.headful else '无头'}）")
            run_entries(br, outdir, entries, args.timeout)
            br.close()
        print(f"\n截图目录：{outdir}")
        return 0

    user = config.env("OA_USER")
    pwd = config.env("OA_PASS")
    print("=" * 64)
    print("登录诊断")
    print("=" * 64)
    print(f"浏览器     : {browser}（{'有头' if args.headful else '无头'}）")
    print(f"OA_USER    : {'已配置' if user else '** 未配置'}")
    print(f"OA_PASS    : {'已配置' if pwd else '** 未配置'}")
    if not (user and pwd):
        print("\n缺 OA_USER / OA_PASS，自动登录无从谈起。请先填 .env。")
        return 1

    store = storage_mod.get_storage()
    print(f"存储后端   : {store.kind}")
    try:
        auth_state = store.load_auth("firm_a")
        print(f"登录态     : {'已存在（%d 字节）' % len(auth_state) if auth_state else '不存在'}")
    except Exception as e:  # noqa: BLE001
        auth_state = None
        print(f"登录态读取失败：{e}")

    with sync_playwright() as p:
        launch_kwargs: dict = {"headless": not args.headful}
        if browser in ("msedge", "chrome"):
            launch_kwargs["channel"] = browser
        br = p.chromium.launch(**launch_kwargs)
        state = None
        if auth_state:
            try:
                state = json.loads(auth_state)
            except Exception:
                state = None
        context = br.new_context(
            storage_state=state, locale="zh-CN",
            timezone_id="Asia/Shanghai", viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout)
        page.on("pageerror", lambda e: print(f"      [页面JS错误] {str(e)[:150]}"))

        print("\n[步骤 0] 复用登录态直接进管理页")
        try:
            page.goto(S.RECRUIT_MANAGER_URL, wait_until="domcontentloaded", timeout=args.timeout)
        except Exception as e:  # noqa: BLE001
            print(f"      goto 异常（未登录时站点会中断导航，属正常）：{type(e).__name__}")
        page.wait_for_timeout(1500)
        print(f"      落地 URL：{page.url}")
        snapshot(page, outdir, "step_00_manager")
        try:
            page.wait_for_selector(S.GRID_ROW, timeout=20000)
            print("      [ok] 登录态仍有效，无需账号密码即可进入")
            print(f"\n证据目录：{outdir}")
            br.close()
            return 0
        except Exception:
            print("      [!!] 登录态无效或已过期，转入账号密码登录")

        for i, url in enumerate((S.OPENID_LOGIN_URL, S.LOGIN_URL), start=1):
            ok, note = try_login_page(page, url, user, pwd, outdir, i, args.timeout)
            print(f"      结论：{note}")
            if ok:
                print(f"\n[ok] {url} 可用")
                try:
                    if config.env_bool("DIAG_SAVE_AUTH", True):
                        store.save_auth("firm_a", json.dumps(context.storage_state(),
                                                             ensure_ascii=False))
                        print("  登录态已回写存储（本机 .auth 或 OSS）")
                except Exception as e:  # noqa: BLE001
                    print(f"  登录态回写失败：{e}")
                print(f"\n证据目录：{outdir}")
                br.close()
                return 0

        print("\n[!!] 两个登录页都没成功。请打开上面的截图确认是否出现验证码 / 账号密码错误提示。")
        print(f"证据目录：{outdir}")
        br.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
