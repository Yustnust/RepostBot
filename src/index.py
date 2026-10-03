"""主入口：调度 + 执行 + 回执。

本机 CLI：
    python src/index.py                       # dry-run（默认）
    python src/index.py --live                # 真正执行
    python src/index.py --headful --browser msedge
    python src/index.py --trace trace.json    # 记录网络请求（抓接口用）

阿里云 FC：
    入口填 index.handler，事件源为定时触发器（每天 09:00）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import page_selectors as S  # noqa: E402
import publisher  # noqa: E402
import storage as storage_mod  # noqa: E402
import logger  # noqa: E402
from notifier import notify_exception, notify_result  # noqa: E402

log = logger.get_logger()


def _dry_run_default(cli_live: bool) -> bool:
    """是否演练：--live 显式关闭；否则看环境变量 DRY_RUN"""
    if cli_live:
        return False
    return config.env_bool("DRY_RUN", True)


def process_account(account: dict, store: storage_mod.Storage, *, dry_run: bool,
                    headless: bool, browser: str, force: bool,
                    trace_path: str | None) -> dict:
    firm_id = account.get("id", "default")
    if account.get("enabled") is False:
        log.info("[跳过] firm=%s 账号已停用", firm_id)
        return {"firm": firm_id, "status": "skipped", "message": "账号已停用"}

    user = config.resolve_ref(account.get("oa_user_ref", "env:OA_USER"))
    pwd = config.resolve_ref(account.get("oa_pass_ref", "env:OA_PASS"))

    try:
        recent_accept_at = storage_mod.last_accept_at(store.load_state(), firm_id)
    except Exception:  # noqa: BLE001
        recent_accept_at = None

    result = publisher.run(
        firm_id,
        user=user,
        pwd=pwd,
        auth_state=store.load_auth(firm_id),
        on_auth=lambda data: store.save_auth(firm_id, data),
        dry_run=dry_run,
        headless=headless,
        browser=browser,
        ignore_interval=force,
        trace_path=trace_path,
        recent_accept_at=recent_accept_at,
        guard_days=config.env_int("ACCEPT_RETRY_GUARD_DAYS", S.ACCEPT_RETRY_GUARD_DAYS),
    )

    # 写状态
    try:
        state = store.load_state()
        storage_mod.record_run(state, firm_id, result)
        store.save_state(state)
    except Exception as e:  # noqa: BLE001
        result["state_error"] = f"{type(e).__name__}: {e}"

    # 发回执：只在「成功」或「失败」时通知客户。
    # 「未到期跳过」每天都会发生，若也发邮件 = 每天一封骚扰邮件。
    if result.get("status") in ("success", "failed"):
        try:
            result["notify"] = notify_result(result, account)
        except Exception as e:  # noqa: BLE001
            result["notify_error"] = f"{type(e).__name__}: {e}"
    else:
        log.info("[回执] firm=%s 状态=%s，不发送回执（避免每日骚扰）",
                 firm_id, result.get("status"))

    return result


def order_accounts(accounts: list[dict], state: dict) -> list[dict]:
    """按「最久没置顶」排前面：last_published_at 升序，没有记录的排最前。

    纯函数，便于单测。将来放开多账号时，先到期的先跑，避免配额被未到期的占掉。
    """
    acc_state = state.get("accounts", {}) or {}

    def key(account: dict) -> str:
        return (acc_state.get(account.get("id", "")) or {}).get("last_published_at") or ""

    return sorted(accounts, key=key)


def run_all(*, dry_run: bool, headless: bool, browser: str, force: bool,
            trace_path: str | None, only: str | None = None) -> list[dict]:
    store = storage_mod.get_storage()
    accounts = store.load_accounts().get("accounts", [])
    if only:
        accounts = [a for a in accounts if a.get("id") == only]

    if not accounts:
        log.error("[运行] 没有可用账号：请配置 config/accounts.json 或 OSS 上的 accounts.json")
        return [{"firm": "-", "status": "failed",
                 "message": "没有可用账号：请配置 config/accounts.json 或 OSS 上的 accounts.json"}]

    # 限流：MAX_PER_RUN 表示每轮最多跑几家（<=0 视为不限）。
    # 先按「最久没置顶」排序，再截配额——顺序很重要，否则会随机漏掉真的到期的。
    max_per_run = config.env_int("MAX_PER_RUN", 0)
    ordered = order_accounts(accounts, store.load_state())
    deferred: list[dict] = []
    if max_per_run > 0 and len(ordered) > max_per_run:
        deferred = ordered[max_per_run:]
        ordered = ordered[:max_per_run]
        log.warning("[调度] 本轮账号 %s 家，超出 MAX_PER_RUN=%s，"
                    "延后 %s 家：%s", len(accounts), max_per_run,
                    len(deferred), ", ".join(a.get("id", "?") for a in deferred))

    log.info("[运行] 开始处理 %s 个账号 dry_run=%s", len(ordered), dry_run)
    results = []
    for account in ordered:
        results.append(process_account(
            account, store, dry_run=dry_run, headless=headless,
            browser=browser, force=force, trace_path=trace_path,
        ))
    for account in deferred:
        firm_id = account.get("id", "?")
        msg = f"超出本轮限流 MAX_PER_RUN={max_per_run}，本轮不处理（下一轮会优先排到）"
        log.info("[跳过] firm=%s %s", firm_id, msg)
        results.append({"firm": firm_id, "status": "skipped", "message": msg})
    return results


def _test_email(to: str | None = None) -> int:
    """发送一封测试邮件，用于验证回执通道是否可用（不触发任何真实业务动作）"""
    from notifier import email as email_notifier

    recipient = to or config.env("ADMIN_EMAIL") or config.env("NOTIFY_EMAIL")
    if not recipient:
        print("未指定收件人：请配置 ADMIN_EMAIL / NOTIFY_EMAIL，或用 --to 指定")
        return 1

    sample = {
        "status": "skipped",
        "message": "这是一封配置测试邮件，用于验证回执通道（非真实执行结果）",
        "sort_time_before": None,
        "sort_time_after": None,
        "days_since": None,
    }
    subject, html = email_notifier.render_result(sample, "（回执通道测试）", "—")
    ok, info = email_notifier.send("[测试] RepostBot 回执通道验证", html, recipient)
    print(f"收件人：{recipient}")
    print(f"发件人：{email_notifier._sender_address(config.env('SMTP_USER'), config.env('SMTP_HOST'))}")
    print(f"结果：{'成功' if ok else '失败'} - {info}")
    return 0 if ok else 1


def _test_sms(to: str | None = None) -> int:
    """发一条测试短信，验证短信通道（阿里云签名/模板/号码）是否可用"""
    from notifier import sms as sms_notifier

    recipient = to or config.env("ADMIN_PHONE") or config.env("NOTIFY_PHONE")
    if not recipient:
        print("未指定手机号：请配置 ADMIN_PHONE / NOTIFY_PHONE，或用 --to 指定")
        return 1

    alert_code = config.env("SMS_TEMPLATE_CODE_ALERT")
    receipt_code = config.env("SMS_TEMPLATE_CODE")
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    if alert_code:
        template_code, params = alert_code, {
            "name": "RepostBot", "reason": "通道测试", "time": now_text[5:],
        }
    elif receipt_code:
        template_code, params = receipt_code, {"time": now_text, "nexttime": "-"}
    else:
        template_code, params = None, {"time": now_text, "nexttime": "-"}

    print(f"手机号：{recipient}")
    print(f"签名：{config.env('SMS_SIGN_NAME') or '（未配置）'}")
    print(f"模板：{template_code or '（未配置）'}")
    ok, info = sms_notifier.send(recipient, params, template_code=template_code)
    print(f"结果：{'成功' if ok else '失败'} - {info}")
    return 0 if ok else 1


# ------------------------------------------------------------------ 体检
# 发布前自检：不启动浏览器、不产生任何真实动作，只回答「配置齐没齐」

def doctor() -> int:
    """打印配置体检表（供真人发布前核对，也用于新机器排障）"""
    store = storage_mod.get_storage()
    try:
        accounts = store.load_accounts().get("accounts", [])
    except Exception as e:  # noqa: BLE001
        accounts = []
        log.warning("[体检] 读取账号失败：%s", e)

    dry_run = _dry_run_default(False)
    smtp_ready = all(config.env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))
    sms_ready = all(config.env(k) for k in ("SMS_SIGN_NAME", "SMS_TEMPLATE_CODE"))
    admin_ready = bool(config.env("ADMIN_EMAIL") or config.env("ADMIN_PHONE"))

    rows = [
        ("存储后端", store.kind, True),
        ("账号数量", str(len(accounts)), bool(accounts)),
        ("OA 账号", "已配置" if config.env("OA_USER") and config.env("OA_PASS") else "缺失",
         bool(config.env("OA_USER") and config.env("OA_PASS"))),
        ("DRY_RUN（演练开关）", "true=只演练，不会真置顶" if dry_run else "false=到期会真置顶", True),
        ("浏览器", config.env("BROWSER", "msedge"), True),
        ("邮件 SMTP", "已配置" if smtp_ready else "未配置（回执发不出）", smtp_ready),
        ("管理员告警", "已配置" if admin_ready else "未配置（失败无人知）", admin_ready),
        ("短信回执", "已配置" if sms_ready else "未配置或模板未就绪", sms_ready),
    ]

    width = max(len(r[0]) for r in rows)
    print("=" * 60)
    print("RepostBot 配置体检")
    print("=" * 60)
    for name, value, ok in rows:
        print(f"{'[ok]' if ok else '[!!]'} {name.ljust(width)}  {value}")

    if dry_run:
        print("\n注意：DRY_RUN 当前为 true，每天运行到点也只会演练。"
              "\n      真实置顶前请在 .env 设 DRY_RUN=false（或用 --live）。")
    print("=" * 60)
    return 0 if all(ok for _, _, ok in rows) else 1


def handler(event, context):  # noqa: ARG001
    """阿里云 FC 入口（定时触发器调用）"""
    try:
        results = run_all(
            dry_run=_dry_run_default(False),
            headless=True,
            browser=config.env("BROWSER", "chromium"),
            force=False,
            trace_path=config.env("TRACE_PATH") or None,
        )
    except Exception as e:  # noqa: BLE001
        msg = f"运行异常：{type(e).__name__}: {e}"
        log.exception(msg)
        try:
            notify_exception(msg)
        except Exception:  # noqa: BLE001
            pass
        return {"results": [{"firm": "-", "status": "failed", "message": msg}]}
    return {"results": results}


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(config.REPO_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="东方律师网招聘置顶机器人")
    parser.add_argument("--firm", default=None, help="只跑指定律所（默认跑全部启用账号）")
    parser.add_argument("--live", action="store_true", help="真正执行置顶（默认 dry-run）")
    parser.add_argument("--force", action="store_true",
                        help="测试用：跳过 20 天判断，继续点击按钮验证链路（仍受 dry-run 保护）")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--browser", default=None, choices=["chromium", "msedge", "chrome"])
    parser.add_argument("--trace", default=None, help="记录网络请求到指定文件")
    parser.add_argument("--test-email", action="store_true",
                        help="发送一封测试邮件验证回执通道（默认发给 ADMIN_EMAIL）")
    parser.add_argument("--test-sms", action="store_true",
                        help="发送一条测试短信验证短信通道（默认发给 ADMIN_PHONE）")
    parser.add_argument("--doctor", action="store_true",
                        help="配置体检：不启浏览器、不执行任何动作，只检查配置是否齐备")
    parser.add_argument("--to", default=None,
                        help="配合 --test-email / --test-sms 指定收件人或手机号")
    args = parser.parse_args()

    if args.test_email:
        return _test_email(args.to)
    if args.test_sms:
        return _test_sms(args.to)
    if args.doctor:
        return doctor()

    browser = args.browser or config.env("BROWSER", "msedge" if sys.platform == "win32" else "chromium")
    try:
        results = run_all(
            dry_run=_dry_run_default(args.live),
            headless=not args.headful,
            browser=browser,
            force=args.force,
            trace_path=args.trace,
            only=args.firm,
        )
    except Exception as e:  # noqa: BLE001
        # 顶层崩溃（OSS 不可用 / 逻辑异常）：必须让管理员第一时间知道，不能静默失败
        msg = f"运行异常：{type(e).__name__}: {e}"
        log.exception(msg)
        try:
            notify_exception(msg)
        except Exception:  # noqa: BLE001
            pass
        return 1

    # 控制台汇总（同时日志文件也会记录，见 src/logger.py）
    log.info("=" * 60)
    log.info("运行结果：\n%s", json.dumps(results, ensure_ascii=False, indent=2))
    log.info("=" * 60)

    # 注意：Windows 控制台可能是 GBK，不要用 emoji，否则 UnicodeEncodeError
    for r in results:
        log.info("[%s] %s: %s", r.get("status"), r.get("firm"), r.get("message"))
    return 0 if all(r.get("status") in ("success", "skipped") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
