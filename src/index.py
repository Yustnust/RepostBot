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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
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

    log.info("[运行] 开始处理 %s 个账号 dry_run=%s", len(accounts), dry_run)
    results = []
    for account in accounts:
        results.append(process_account(
            account, store, dry_run=dry_run, headless=headless,
            browser=browser, force=force, trace_path=trace_path,
        ))
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


def handler(event, context):  # noqa: ARG001
    """阿里云 FC 入口（定时触发器调用）"""
    results = run_all(
        dry_run=_dry_run_default(False),
        headless=True,
        browser=config.env("BROWSER", "chromium"),
        force=False,
        trace_path=config.env("TRACE_PATH") or None,
    )
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
    parser.add_argument("--to", default=None, help="配合 --test-email 指定收件人")
    args = parser.parse_args()

    if args.test_email:
        return _test_email(args.to)

    browser = args.browser or config.env("BROWSER", "msedge" if sys.platform == "win32" else "chromium")
    results = run_all(
        dry_run=_dry_run_default(args.live),
        headless=not args.headful,
        browser=browser,
        force=args.force,
        trace_path=args.trace,
        only=args.firm,
    )

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
