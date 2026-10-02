"""邮件回执。

必须用 SMTP_SSL + 465 端口。在 FC 里用 smtp.connect(host, 25) 会报
[Errno 101] Network is unreachable。
"""

from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText

import config


def _sender_address(user: str, host: str) -> str:
    """发件人地址。

    163 等邮箱要求 From 必须是**完整邮箱地址**且等于登录账号，
    只写用户名（如 zhizhe_2020）会被拒：550 Invalid User。
    可用 SMTP_FROM 显式覆盖。
    """
    override = config.env("SMTP_FROM")
    if override:
        return override
    if "@" in user:
        return user
    # smtp.163.com -> 163.com
    domain = host.split("smtp.", 1)[-1] if "smtp." in host else host
    return f"{user}@{domain}"


def send(subject: str, html: str, to: str | None = None) -> tuple[bool, str]:
    """发送一封 HTML 邮件，返回 (是否成功, 说明)"""
    host = config.env("SMTP_HOST")
    port = config.env_int("SMTP_PORT", 465)
    user = config.env("SMTP_USER")
    pwd = config.env("SMTP_PASS")
    recipient = to or config.env("NOTIFY_EMAIL")

    if not (host and user and pwd and recipient):
        return False, "邮件未配置（SMTP_HOST/SMTP_USER/SMTP_PASS/NOTIFY_EMAIL 缺失）"

    sender = _sender_address(user, host)
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = recipient

    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, pwd)
            # 信封发件人也要用完整地址，否则 163 报 550 Invalid User
            smtp.sendmail(sender, [recipient], msg.as_string())
        return True, f"已发送至 {recipient}"
    except Exception as e:  # noqa: BLE001
        return False, f"发送失败：{type(e).__name__}: {e}"


def render_result(result: dict, firm_name: str, next_due: str | None = None) -> tuple[str, str]:
    """把执行结果渲染成 (主题, HTML 正文)"""
    status = result.get("status")
    if status == "success":
        title = "招聘信息置顶成功"
        color, border, bg = "#166534", "#bbf7d0", "#f0fdf4"
        icon = "OK"
        headline = "您的招聘信息已在东方律师网完成置顶更新"
    elif status == "skipped":
        title = "本次未执行置顶"
        color, border, bg = "#92400e", "#fde68a", "#fffbeb"
        icon = "-"
        headline = "本次检查未执行置顶操作"
    else:
        title = "置顶执行失败，需人工处理"
        color, border, bg = "#991b1b", "#fecaca", "#fef2f2"
        icon = "!"
        headline = "自动置顶未能完成，请人工登录处理"

    rows = [
        ("律所", firm_name),
        ("执行结果", {"success": "置顶成功", "skipped": "未到期 / 演练", "failed": "失败"}.get(status, status)),
        ("上次排序时间", result.get("sort_time_before") or "-"),
        ("本次排序时间", result.get("sort_time_after") or "-"),
        ("距上次置顶", f"{result.get('days_since')} 天" if result.get("days_since") is not None else "-"),
        ("说明", result.get("message") or "-"),
    ]
    if next_due:
        rows.append(("下次预计可置顶", next_due))

    body = "".join(
        f'<tr><td style="padding:6px 12px;color:#666">{k}</td>'
        f'<td style="padding:6px 12px;color:#222">{v}</td></tr>'
        for k, v in rows
    )

    html = f"""<html><body style="margin:0;padding:16px;background:#f5f5f5;font-family:sans-serif">
<div style="max-width:520px;margin:auto;background:{bg};border:1px solid {border};
            border-radius:12px;padding:28px">
  <div style="font-size:28px;font-weight:bold;color:{color}">{icon}</div>
  <h2 style="color:{color};margin:8px 0 12px">{headline}</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px">{body}</table>
  <p style="margin-top:18px;font-size:12px;color:#999">本邮件由 RepostBot 自动发送</p>
</div></body></html>"""
    return title, html
