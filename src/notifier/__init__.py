"""回执通知统一入口。

规则：
  - 成功 / 跳过：给客户发（邮件 + 可选短信）
  - 失败：给客户发，同时给管理员发告警
"""

from __future__ import annotations

from datetime import datetime, timedelta

import config
import page_selectors as S
from notifier import email as email_notifier
from notifier import sms as sms_notifier


def _next_due(result: dict) -> str | None:
    """根据本次排序时间推算下次可置顶日期"""
    text = result.get("sort_time_after") or result.get("sort_time_before")
    if not text:
        return None
    for fmt in (S.SORT_TIME_FORMAT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.strip(), fmt)
            return (dt + timedelta(days=S.REPOST_INTERVAL_DAYS)).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def notify_result(result: dict, account: dict) -> dict:
    """按账号配置发送回执，返回各通道结果"""
    out: dict = {"email": None, "sms": None}

    notify_cfg = account.get("notify", {}) or {}
    firm_name = account.get("name", result.get("firm", ""))
    next_due = _next_due(result)

    subject, html = email_notifier.render_result(result, firm_name, next_due)

    # 失败时默认**不打扰客户**，只通知管理员；
    # 需要让客户也知道失败时，把 NOTIFY_CLIENT_ON_FAILURE 设为 true
    failed = result.get("status") == "failed"
    notify_client = True
    if failed and not config.env_bool("NOTIFY_CLIENT_ON_FAILURE", False):
        notify_client = False
        out["email"] = (False, "失败：按配置不通知客户（NOTIFY_CLIENT_ON_FAILURE=false）")
        out["sms"] = (False, "失败：按配置不通知客户")

    if notify_client:
        if notify_cfg.get("email_enabled", True) and notify_cfg.get("email"):
            out["email"] = email_notifier.send(subject, html, notify_cfg["email"])
        elif config.env("NOTIFY_EMAIL"):
            out["email"] = email_notifier.send(subject, html, config.env("NOTIFY_EMAIL"))

        if notify_cfg.get("sms_enabled") and notify_cfg.get("phone"):
            params = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "nexttime": next_due or "",
            }
            out["sms"] = sms_notifier.send(notify_cfg["phone"], params)

    if failed:
        admin = config.env("ADMIN_EMAIL")
        if admin:
            out["admin_email"] = email_notifier.send(
                f"[告警] {firm_name} 置顶失败，需人工处理", html, admin
            )
    return out


def notify_exception(text: str) -> dict:
    """程序级异常告警（如登录态失效、OSS 不可用）"""
    admin = config.env("ADMIN_EMAIL")
    if not admin:
        return {"email": (False, "未配置 ADMIN_EMAIL")}
    html = f"""<html><body style="font-family:sans-serif;padding:16px">
    <div style="max-width:520px;margin:auto;background:#fef2f2;border:1px solid #fecaca;
                border-radius:12px;padding:24px">
      <h2 style="color:#991b1b">RepostBot 运行异常</h2>
      <pre style="white-space:pre-wrap;font-size:13px;color:#333">{text}</pre>
    </div></body></html>"""
    return {"email": email_notifier.send("[告警] RepostBot 运行异常", html, admin)}
