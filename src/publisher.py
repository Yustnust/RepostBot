"""单账号发布流程。

只做一件事：满 20 天时，点击「更新排序时间」把招聘信息顶上去。

设计原则（与项目方案 §14 实施纪律一致）：
1. dry_run 模式下绝不调用 dialog.accept()；
2. 只有确认成功后才认为本周期已消耗机会；
3. 每 20 天周期只有一次置顶机会，绝不重复点击。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("未安装 playwright：pip install -r requirements.txt && playwright install chromium")

import page_selectors as S

CST = timezone(timedelta(hours=8))


def now_cst() -> datetime:
    return datetime.now(CST)


def parse_sort_time(text: str) -> datetime | None:
    """解析列表里的排序时间，如 2026-09-24 09:34"""
    text = (text or "").strip()
    for fmt in (S.SORT_TIME_FORMAT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def _is_login_page(url: str) -> bool:
    return any(m in url for m in S.LOGIN_URL_MARKERS)


def _try_login(page, user: str, pwd: str) -> bool:
    """登录态失效时的兜底登录"""
    filled = 0
    for sel in S.LOGIN_USERNAME_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(user)
            filled += 1
            break
    for sel in S.LOGIN_PASSWORD_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.fill(pwd)
            filled += 1
            break
    if filled < 2:
        return False
    for sel in S.LOGIN_SUBMIT_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.click(timeout=3000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            return True
    return False


def scan_rows(page) -> list[dict[str, Any]]:
    """读取列表所有行，返回结构化数据"""
    rows = page.locator(S.GRID_ROW)
    data = []
    for i in range(rows.count()):
        row = rows.nth(i)

        def cell(sel: str) -> str:
            loc = row.locator(sel)
            return loc.first.inner_text().strip() if loc.count() else ""

        data.append({
            "index": i,
            "title": cell(S.COL_TITLE),
            "company": cell(S.COL_COMPANY),
            "publish_date": cell(S.COL_PUBLISH_DATE),
            "sort_time_text": cell(S.COL_SORT_TIME),
            "status": cell(S.COL_STATUS),
        })
    return data


def pick_target(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选出要置顶的行：状态为「招聘中」且排序时间最早的一条"""
    candidates = [r for r in rows if r["status"] == S.STATUS_RECRUITING]
    if not candidates:
        return None
    with_time = [r for r in candidates if parse_sort_time(r["sort_time_text"])]
    pool = with_time or candidates
    return min(pool, key=lambda r: parse_sort_time(r["sort_time_text"]) or now_cst())


def run(
    firm_id: str,
    *,
    user: str = "",
    pwd: str = "",
    auth_state: str | None = None,
    on_auth: Callable[[str], None] | None = None,
    dry_run: bool = True,
    headless: bool = True,
    browser: str = "chromium",
    timeout_ms: int = 60000,
    ignore_interval: bool = False,
    trace_path: str | None = None,
) -> dict[str, Any]:
    """执行单个律所账号的置顶流程。

    auth_state : 登录态 JSON 文本（由 storage 层提供）
    on_auth    : 回写登录态的回调
    ignore_interval : 仅供测试链路用，跳过 20 天判断（与 dry_run 搭配才安全）
    trace_path : 记录网络请求，用于抓取「更新排序时间」的真实接口

    返回 dict：status ∈ {success, skipped, failed}
    """
    result: dict[str, Any] = {
        "firm": firm_id,
        "status": "failed",
        "message": "",
        "sort_time_before": None,
        "sort_time_after": None,
        "days_since": None,
        "dialog": None,
        "dry_run": dry_run,
        "row_selected": False,
    }

    dialog_box: dict[str, str] = {}

    def on_dialog(dialog):
        msg = dialog.message
        dialog_box["message"] = msg
        if dry_run:
            dialog_box["action"] = "dismiss(dry-run)"
            dialog.dismiss()
        elif S.CONFIRM_TEXT_KEYWORD in msg:
            dialog_box["action"] = "accept"
            dialog.accept()
        else:
            # 弹的不是预期的确认框，绝不点确定
            dialog_box["action"] = "dismiss(unexpected)"
            dialog.dismiss()

    with sync_playwright() as p:
        launch_kwargs: dict[str, Any] = {"headless": headless}
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
            storage_state=state,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.on("dialog", on_dialog)

        requests: list[dict[str, Any]] = []
        if trace_path:
            def on_request(req):
                try:
                    requests.append({
                        "method": req.method,
                        "url": req.url,
                        "post_data": req.post_data,
                    })
                except Exception:
                    pass
            page.on("request", on_request)

        try:
            page.goto(S.RECRUIT_MANAGER_URL, wait_until="domcontentloaded", timeout=timeout_ms)

            if _is_login_page(page.url):
                if user and pwd and _try_login(page, user, pwd):
                    page.goto(S.RECRUIT_MANAGER_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                if _is_login_page(page.url):
                    result["message"] = (
                        f"登录态失效且自动登录失败（firm={firm_id}）。"
                        f"本机请运行 python tools/recon.py --firm {firm_id} 重新生成登录态"
                    )
                    return result

            page.wait_for_selector(S.GRID_ROW, timeout=timeout_ms)
            rows = scan_rows(page)
            if not rows:
                result["message"] = "列表为空，未找到任何招聘行"
                return result

            target = pick_target(rows)
            if not target:
                result["status"] = "skipped"
                result["message"] = "没有状态为「招聘中」的招聘信息，跳过"
                return result

            sort_dt = parse_sort_time(target["sort_time_text"])
            result["sort_time_before"] = target["sort_time_text"]
            if not sort_dt:
                result["message"] = f"无法解析排序时间：{target['sort_time_text']!r}"
                return result

            days = (now_cst() - sort_dt).days
            result["days_since"] = days

            if days < S.REPOST_INTERVAL_DAYS and not ignore_interval:
                result["status"] = "skipped"
                result["message"] = (
                    f"距上次置顶仅 {days} 天，未满 {S.REPOST_INTERVAL_DAYS} 天，跳过"
                    f"（下次可执行：{sort_dt + timedelta(days=S.REPOST_INTERVAL_DAYS):%Y-%m-%d}）"
                )
                return result
            if days < S.REPOST_INTERVAL_DAYS:
                result["message"] = f"（测试模式：距上次置顶仅 {days} 天，仍继续验证链路）"

            # 选中目标行（即使按钮不依赖选中，也先选上，保证作用于正确的岗位）
            page.locator(S.GRID_ROW).nth(target["index"]).click()
            page.wait_for_timeout(500)
            result["row_selected"] = page.locator(S.GRID_ROW_SELECTED).count() > 0

            page.locator(S.BTN_UPDATE_SORT_TIME).first.click(timeout=15000)
            page.wait_for_timeout(1500)
            result["dialog"] = dialog_box or None

            if dry_run:
                result["status"] = "skipped"
                result["message"] = "dry-run：已点击按钮并捕获确认框，按要求取消，未真正置顶"
                return result

            if dialog_box.get("action") != "accept":
                result["message"] = f"未获得预期的确认框，已放弃：{dialog_box}"
                return result

            # 刷新后复核排序时间是否更新
            page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(S.GRID_ROW, timeout=timeout_ms)
            after = pick_target(scan_rows(page))
            result["sort_time_after"] = after["sort_time_text"] if after else None
            if after and after["sort_time_text"] != target["sort_time_text"]:
                result["status"] = "success"
                result["message"] = f"置顶成功，排序时间更新为 {after['sort_time_text']}"
            else:
                result["status"] = "failed"
                result["message"] = "已确认但排序时间未变化，请人工复核"
        except Exception as e:  # noqa: BLE001
            result["message"] = f"执行异常：{type(e).__name__}: {e}"
        finally:
            try:
                if on_auth:
                    on_auth(json.dumps(context.storage_state(), ensure_ascii=False))
            except Exception:
                pass
            if trace_path and requests:
                try:
                    with open(trace_path, "w", encoding="utf-8") as f:
                        json.dump(requests, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            br.close()

    return result
