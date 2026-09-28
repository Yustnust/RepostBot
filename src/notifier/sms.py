"""短信回执（阿里云短信服务）。

前置条件（缺一不可）：
  1. 阿里云账号已企业实名认证
  2. 短信签名已审核通过        -> SMS_SIGN_NAME
  3. 短信模板已审核通过        -> SMS_TEMPLATE_CODE
建议模板：您的招聘信息已于${time}在东方律师网完成置顶更新，下次可更新时间为${nexttime}
"""

from __future__ import annotations

import json

import config


def send(phone: str, params: dict) -> tuple[bool, str]:
    """发送模板短信，params 需与模板变量一一对应"""
    sign = config.env("SMS_SIGN_NAME")
    code = config.env("SMS_TEMPLATE_CODE")
    ak = config.env("ALIBABA_CLOUD_ACCESS_KEY_ID")
    sk = config.env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")

    if not (phone and sign and code):
        return False, "短信未配置（手机号 / SMS_SIGN_NAME / SMS_TEMPLATE_CODE 缺失）"
    if not (ak and sk):
        return False, "短信未配置（AccessKey 缺失）"

    try:
        from alibabacloud_dysmsapi20170525.client import Client as DysmsClient
        from alibabacloud_dysmsapi20170525 import models as dysms_models
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError:
        return False, "未安装短信 SDK：pip install alibabacloud_dysmsapi20170525"

    try:
        cfg = open_api_models.Config(access_key_id=ak, access_key_secret=sk)
        cfg.endpoint = "dysmsapi.aliyuncs.com"
        client = DysmsClient(cfg)
        req = dysms_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=sign,
            template_code=code,
            template_param=json.dumps(params, ensure_ascii=False),
        )
        resp = client.send_sms(req)
        ok = resp.body.code == "OK"
        return ok, f"{resp.body.code} {resp.body.message}"
    except Exception as e:  # noqa: BLE001
        return False, f"发送失败：{type(e).__name__}: {e}"
