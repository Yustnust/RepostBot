"""验证「能否走 OA 登录路线」来规避 passport3 的页面变更。

核心问题：招聘后台(recruitment.lawyers.org.cn)的会话，是否能被 OA 的登录态放行？
如果能，定时任务就改走 OA 登录，将来 passport3 怎么改都伤不到我们。

两段验证：
  A) 复用真人已登录的 OA 会话（拷贝 Edge 用户目录，携带真实 cookie）
     —— 直接回答「我登了 OA，能不能进后台 / 有没有 SSO 入口」
  B) 程序化 OA 登录（脚本用账号密码登 OA，再进后台）
     —— 直接回答「定时任务能不能自己走通这条路」

用法：
    python tools/verify_oa_sso.py                 # 无头
    python tools/verify_oa_sso.py --headful       # 有头（想亲眼看）

注意：A 段会拷贝 Edge 用户目录到临时位置，不改动你正在用的 Edge。
若拷贝时 cookie 被锁拿不到，会明确提示，并退而依赖 B 段结论。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
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

OK = "[ok]"
BAD = "[!!]"
OUT_ROOT = REPO_ROOT / "logs"


def _edge_user_data() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    p = Path(local) / "Microsoft" / "Edge" / "User Data"
    return p if p.is_dir() else None


# 登录态只需要 cookie/偏好，这些大目录对验证无意义且极慢，直接跳过
_SKIP_DIRS = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "Service Worker",
    "blob_storage", "optimization_guide_model_store", "OnDeviceHeadSuggestModel",
    "WebStorage", "File System", "Session Storage", "IndexedDB",
    "Extensions", "Extension Rules", "logs", "Crashpad", "GrShaderCache",
    "GrShaderCache", "Subresource Filter", "VideoDecodeStats",
}


def _copy_profile(src: Path) -> Path | None:
    """拷贝 Edge 用户目录到临时位置（携带 cookie）。

    原目录正被 Edge 占用，部分组件目录/文件会被锁（WinError 5）。
    这里用手写递归拷贝，对**任何**目录/文件错误都忽略，确保拷到尽量多的内容；
    并跳过庞大的缓存目录以提速。拷贝后删掉 SingletonLock / SingletonCookie，
    否则 Chromium 会以为已有实例在跑而拒绝启动。
    """
    dst = Path(tempfile.mkdtemp(prefix="edge_profile_"))

    def copy_tree(s: Path, d: Path) -> None:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        try:
            entries = list(os.scandir(s))
        except Exception:
            return
        for e in entries:
            if e.name in _SKIP_DIRS:
                continue
            sp = Path(e.path)
            dp = d / e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    copy_tree(sp, dp)
                else:
                    shutil.copy2(sp, dp)
            except Exception:
                pass

    try:
        copy_tree(src, dst)
    except Exception as e:  # noqa: BLE001
        print(f"    {BAD} 拷贝 Edge 用户目录失败：{e}")
        return None
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        for f in dst.rglob(name):
            try:
                f.unlink()
            except Exception:
                pass
    print(f"    已拷贝 Edge 用户目录 -> {dst}")
    return dst


def check_oa_state(page) -> str:
    """停在 OA 登录页且有 #j_username 即视为未登录；否则视为已登录。"""
    try:
        page.goto(S.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    try:
        if page.locator(S.LOGIN_USERNAME).first.is_visible(timeout=3000):
            return "not_logged_in"
    except Exception:
        pass
    return "logged_in"


def test_recruit_access(page) -> tuple[bool, bool, str]:
    """访问招聘后台，返回 (是否进入列表, 是否被踢回登录页, 落地URL)。"""
    try:
        page.goto(S.RECRUIT_MANAGER_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    url = page.url
    bounced = any(m in url for m in S.LOGIN_URL_MARKERS)
    granted = False
    try:
        page.wait_for_selector(S.GRID_ROW, timeout=15000)
        granted = True
    except Exception:
        granted = False
    return granted, bounced, url


def find_recruit_links(page) -> list[dict]:
    """在 OA 大厅里找指向招聘/人才系统的 SSO 入口。"""
    try:
        page.goto("https://oa.lawyers.org.cn/hall/", wait_until="domcontentloaded",
                  timeout=30000)
    except Exception:
        return []
    page.wait_for_timeout(1500)
    try:
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('a,button')).map(el => ({"
            "t: (el.innerText||'').trim().slice(0,30), h: el.href||''})).filter(x => "
            "/招聘|recruit|人才|律所管理|事务所|业务办理/.test(x.t + x.h))"
        )
    except Exception:
        return []


def phase_a(br, browser: str, headful: bool) -> None:
    print("\n" + "=" * 64)
    print("A) 复用真人已登录的 OA 会话（拷贝 Edge 用户目录）")
    print("=" * 64)
    src = _edge_user_data()
    if not src:
        print(f"  {BAD} 找不到 Edge 用户目录（LOCALAPPDATA 下无 Microsoft/Edge/User Data）")
        return
    print(f"  来源：{src}")
    profile = _copy_profile(src)
    if not profile:
        return

    launch = {"headless": not headful, "user_data_dir": str(profile),
              "locale": "zh-CN", "timezone_id": "Asia/Shanghai"}
    if browser in ("msedge", "chrome"):
        launch["channel"] = browser
    try:
        ctx = br.launch_persistent_context(**launch)
    except Exception as e:  # noqa: BLE001
        print(f"  {BAD} 用拷贝的 Profile 启动浏览器失败：{e}")
        return
    page = ctx.new_page()
    page.set_default_timeout(30000)

    state = check_oa_state(page)
    print(f"  OA 登录态 ：{state}")
    if state == "not_logged_in":
        print(f"  {BAD} 拷贝到的会话并未保持 OA 登录（cookie 可能在拷贝时被锁住）。")
        print("        建议：关闭 Edge 后重跑本脚本，或直接看 B 段结论。")
        ctx.browser.close()
        return

    granted, bounced, url = test_recruit_access(page)
    print(f"  访问招聘后台 -> 落地：{url}")
    if granted:
        print(f"  {OK} OA 会话可直接进入招聘后台（SSO 生效）！")
    elif bounced:
        print(f"  {BAD} 被踢回登录页 —— OA 会话**不覆盖**招聘后台子域")
    else:
        print(f"  [?] 既没进列表也没明显踢回，需人工看截图（logs/ 下）")

    links = find_recruit_links(page)
    if links:
        print(f"  OA 大厅里发现的候选 SSO 入口（{len(links)} 个）：")
        for l in links[:10]:
            print(f"      {l['t']!r}  ->  {l['h']}")
    else:
        print("  OA 大厅未找到明显的招聘/人才系统入口链接")
    ctx.close()


def phase_b(br, browser: str, headful: bool, user: str, pwd: str) -> None:
    print("\n" + "=" * 64)
    print("B) 程序化 OA 登录（脚本用账号密码登 OA，再进后台）")
    print("=" * 64)
    if not (user and pwd):
        print(f"  {BAD} 未配置 OA_USER / OA_PASS，无法程序化登录")
        return
    launch = {"headless": not headful}
    if browser in ("msedge", "chrome"):
        launch["channel"] = browser
    ctx = br.launch(**launch).new_context(locale="zh-CN",
                                          timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(30000)

    print(f"  打开 OA 登录页 {S.LOGIN_URL}")
    opened = False
    for attempt in (1, 2):
        try:
            page.goto(S.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            opened = True
            break
        except Exception as e:  # noqa: BLE001
            print(f"  [!] 第 {attempt} 次打开 OA 登录页超时，重试...")
    if not opened:
        print(f"  {BAD} 打不开 OA 登录页（两次均超时，可能站点偶发中断或网络问题）")
        ctx.browser.close()
        return
    page.wait_for_timeout(1500)
    print(f"  落地：{page.url}")

    filled = 0
    for sel in (S.LOGIN_USERNAME, 'input[name="j_username"]', 'input[type="text"]'):
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(user)
            filled += 1
            print(f"  用户名填入：{sel}")
            break
    for sel in (S.LOGIN_PASSWORD, 'input[name="j_password"]', 'input[type="password"]'):
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(pwd)
            filled += 1
            print("  密码填入（不回显）")
            break
    if filled < 2:
        print(f"  {BAD} OA 登录表单没填齐（filled={filled}/2），OA 登录页可能也改了")
        ctx.browser.close()
        return

    for sel in (S.LOGIN_SUBMIT, 'button[type="submit"]', 'input[type="submit"]'):
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            try:
                loc.click(timeout=8000)
            except Exception:
                loc.evaluate("el => el.click()")
            print(f"  已点击提交：{sel}")
            break
    page.wait_for_timeout(2500)
    print(f"  OA 登录后落地：{page.url}")

    granted, bounced, url = test_recruit_access(page)
    print(f"  访问招聘后台 -> 落地：{url}")
    if granted:
        print(f"  {OK} 脚本登 OA 后可进入招聘后台 —— 这条路自动化可行！")
    elif bounced:
        print(f"  {BAD} 被踢回登录页 —— 即使登了 OA，招聘后台仍要单独登录")
    else:
        print(f"  [?] 不确定，需人工看截图")

    ctx.browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 OA 登录能否打通招聘后台")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--browser", default=None, choices=["chromium", "msedge", "chrome"])
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv(REPO_ROOT / ".env")

    browser = args.browser or config.env("BROWSER",
                                         "msedge" if sys.platform == "win32" else "chromium")
    user = config.env("OA_USER")
    pwd = config.env("OA_PASS")
    print("=" * 64)
    print("OA 登录路线验证")
    print("=" * 64)
    print(f"浏览器     : {browser}（{'有头' if args.headful else '无头'}）")
    print(f"OA_USER    : {'已配置' if user else '** 未配置'}")
    print(f"OA_PASS    : {'已配置' if pwd else '** 未配置'}")

    with sync_playwright() as p:
        br = p.chromium
        # A 段复用真人会话；B 段程序化登录
        phase_a(br, browser, args.headful)
        phase_b(br, browser, args.headful, user, pwd)

    print("\n" + "=" * 64)
    print("结论速读")
    print("=" * 64)
    print("  - A 段进得去后台 => 真人视角 OA 登录可用，且存在 SSO 放行")
    print("  - B 段进得去后台 => 定时任务可改走 OA 登录路线，规避 passport3 变更")
    print("  - 两段都进不去 => 招聘后台与 OA 是独立认证域，OA 路线走不通")
    print(f"  截图若需要：{OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
