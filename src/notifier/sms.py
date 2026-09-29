"""短信回执（阿里云短信服务）。

前置条件（缺一不可）：
  1. 阿里云账号已企业实名认证
  2. 短信签名已审核通过        -> SMS_SIGN_NAME
  3. 短信模板已审核通过        -> SMS_TEMPLATE_CODE
凭证来源（两种，自动识别）：
  1. 长期 AccessKey      -> 环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID / SECRET（本机、CI）
  2. RAM 角色临时凭证 STS -> 额外有 ALIBABA_CLOUD_SECURITY_TOKEN（**阿里云 FC 推荐**，
                            给函数绑定 RAM 角色后由运行时自动注入，凭证自动轮换，
                            无需在代码或配置里保存任何 AK）
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
        # 阿里云 FC 绑定 RAM 角色后，运行时会注入 ALIBABA_CLOUD_SECURITY_TOKEN，
        # 此时必须使用 STS 临时凭证，否则调用会被拒绝
        token = config.env("ALIBABA_CLOUD_SECURITY_TOKEN")
        if token:
            cfg.security_token = token
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
