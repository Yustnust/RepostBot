"""接口探针：从页面 JS 里找「更新排序时间」背后的真实接口。

背景
----
2026-10-03 抓包发现：招聘列表是走 XHR 拿的——

    POST https://recruitment.lawyers.org.cn/service/webdata/
    handler=sba.recruitmentlist&pageSize=20&companyId=$CurrentUserCompanyId

那么「更新排序时间」大概率也是同一个 service 的另一个 handler。找到它，
就能把部署从 1.5GB 的 Playwright 镜像精简成几 MB 的几行 HTTP 调用。

本工具**只读取页面 JS，绝不点击任何按钮**（点击会真的消耗 20 天一次的机会）。

用法
----
    python tools/probe_api.py                     # 无头
    python tools/probe_api.py --keywords 更新排序时间

产物：logs/api_probe_<时间戳>/script_*.js 与 report.txt
"""

from __future__ import annotations

import argparse
import json
import re
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

DEFAULT_KEYWORDS = [
    "recruitmentlist", "webdata", "sba.", "sortDate", "sortTime", "sort_date",
    "updatesort", "updateSort", "刷新排序", "排序时间", "置顶", "20天", "renewpublish",
]
# handler=xxx.yyy 这种调用形态，是本次找的目标
HANDLER_RE = re.compile(r"handler\s*[=:]\s*[\"']?([\w.]+)", re.I)


def collect(page) -> dict:
    """列出页面引用的脚本地址与内联脚本片段"""
    js = """
    () => ({
      scripts: Array.from(document.querySelectorAll('script[src]')).map(s => s.src),
      inline: Array.from(document.querySelectorAll('script:not([src])'))
                   .map(s => (s.textContent || '').slice(0, 20000))
                   .filter(t => t.trim()),
      handlers_in_html: (document.documentElement.innerHTML || '').slice(0, 400000),
    })
    """
    return page.evaluate(js)


def scan_text(text: str, keywords: list[str], hits: dict[str, list[str]]) -> None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(k.lower() in low for k in keywords):
            hits.setdefault("lines", []).append(line.strip()[:300])
        for m in HANDLER_RE.finditer(line):
            hits.setdefault("handlers", []).append(m.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description="招聘后台接口探针（只读，不点击按钮）")
    parser.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS,
                        help="要在 JS 里搜索的关键词")
    parser.add_argument("--url", default=None,
                        help="要探测的页面，默认招聘管理页；"
                             "例：--url https://passport3.lawyers.org.cn/login.jsp")
    parser.add_argument("--browser", default=None,
                        choices=["chromium", "msedge", "chrome"])
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv(REPO_ROOT / ".env")

    browser = args.browser or config.env("BROWSER", "msedge" if sys.platform == "win32" else "chromium")
    outdir = REPO_ROOT / "logs" / f"api_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    outdir.mkdir(parents=True, exist_ok=True)

    store = storage_mod.get_storage()
    auth_state = store.load_auth(config.env("FIRM_ID", "firm_a"))
    if not auth_state:
        print("没有登录态，先登录：python tools/diag_login.py")
        return 1

    hits: dict[str, list[str]] = {}
    with sync_playwright() as p:
        launch_kwargs: dict = {"headless": not args.headful}
        if browser in ("msedge", "chrome"):
            launch_kwargs["channel"] = browser
        br = p.chromium.launch(**launch_kwargs)
        context = br.new_context(
            storage_state=json.loads(auth_state), locale="zh-CN",
            timezone_id="Asia/Shanghai", viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(45000)

        target_url = args.url or S.RECRUIT_MANAGER_URL
        need_login_check = target_url == S.RECRUIT_MANAGER_URL
        print(f"打开 {target_url} ...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            print(f"  goto 异常：{type(e).__name__}（未登录时站点会中断导航）")
        if need_login_check:
            try:
                page.wait_for_selector(S.GRID_ROW, timeout=30000)
            except Exception:
                print("  [!!] 列表没渲染出来，登录态可能失效：python tools/diag_login.py")
                br.close()
                return 1

        info = collect(page)
        print(f"脚本 {len(info['scripts'])} 个，内联脚本 {len(info['inline'])} 段")

        # 页面自身的 HTML 也过一遍：ExtJS 常常把 handler 直接写在 onclick 里
        scan_text(info["handlers_in_html"], args.keywords, hits)

        for idx, src in enumerate(info["scripts"]):
            try:
                resp = context.request.get(src, timeout=30000)
                body = resp.text()
            except Exception as e:  # noqa: BLE001
                print(f"  读取失败 {src[:80]}：{type(e).__name__}")
                continue
            (outdir / f"script_{idx:02d}.js").write_text(body, encoding="utf-8")
            scan_text(body, args.keywords, hits)

        for idx, body in enumerate(info["inline"]):
            scan_text(body, args.keywords, hits)

        br.close()

    handlers = sorted(set(hits.get("handlers", [])))
    lines = []
    lines.append(f"探测时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"关键词：{', '.join(args.keywords)}")
    lines.append("")
    lines.append("== 发现的 handler 调用（去重） ==")
    lines.extend(f"  {h}" for h in handlers)
    if not handlers:
        lines.append("  （无）")
    lines.append("")
    lines.append("== 命中行 ==")
    unique_lines = list(dict.fromkeys(hits.get("lines", [])))
    lines.extend(f"  {l}" for l in unique_lines)
    if not unique_lines:
        lines.append("  （无）")

    report = "\n".join(lines)
    (outdir / "report.txt").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"\n产物目录：{outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
