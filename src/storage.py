"""配置与状态存储。

两套实现：
  - LocalStorage：本机开发用（config/accounts.json、config/state.json、.auth/<firm>.json）
  - OSSStorage ：阿里云 FC 用（FC 容器无本地持久化，必须走 OSS）

对外只暴露 get_storage()，业务代码不感知底层。将来换 Tablestore/RDS 也只改本文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config

DEFAULT_ACCOUNTS = {"version": 1, "accounts": []}
DEFAULT_STATE = {"version": 1, "updated_at": None, "accounts": {}}
HISTORY_KEEP = 20


class Storage:
    """存储接口"""

    def load_accounts(self) -> dict[str, Any]:
        raise NotImplementedError

    def load_state(self) -> dict[str, Any]:
        raise NotImplementedError

    def save_state(self, state: dict[str, Any]) -> None:
        raise NotImplementedError

    # 登录态（Playwright storage_state 的 JSON 文本）
    def load_auth(self, firm_id: str) -> str | None:
        raise NotImplementedError

    def save_auth(self, firm_id: str, data: str) -> None:
        raise NotImplementedError

    @property
    def kind(self) -> str:
        return self.__class__.__name__


class LocalStorage(Storage):
    """本机文件存储"""

    def __init__(self, root: Path | None = None):
        self.root = root or config.REPO_ROOT
        self.config_dir = self.root / "config"
        self.auth_dir = self.root / ".auth"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.auth_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path, default: dict) -> dict:
        if not path.exists():
            return json.loads(json.dumps(default))
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return json.loads(json.dumps(default))

    def load_accounts(self) -> dict[str, Any]:
        data = self._read_json(self.config_dir / "accounts.json", DEFAULT_ACCOUNTS)
        if not data.get("accounts"):
            # 没有配置时，回退到环境变量里的单账号（本机开发便利）
            user, pwd = config.env("OA_USER"), config.env("OA_PASS")
            if user:
                data = {"version": 1, "accounts": [{
                    "id": config.env("FIRM_ID", "firm_a"),
                    "name": config.env("FIRM_NAME", "默认律所"),
                    "enabled": True,
                    "oa_user_ref": "env:OA_USER",
                    "oa_pass_ref": "env:OA_PASS",
                    "notify": {
                        "email": config.env("NOTIFY_EMAIL"),
                        "phone": config.env("NOTIFY_PHONE"),
                        "email_enabled": bool(config.env("NOTIFY_EMAIL")),
                        "sms_enabled": False,
                    },
                }]}
        return data

    def load_state(self) -> dict[str, Any]:
        return self._read_json(self.config_dir / "state.json", DEFAULT_STATE)

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now_iso()
        (self.config_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_auth(self, firm_id: str) -> str | None:
        p = self.auth_dir / f"{firm_id}.json"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def save_auth(self, firm_id: str, data: str) -> None:
        (self.auth_dir / f"{firm_id}.json").write_text(data, encoding="utf-8")


class OSSStorage(Storage):
    """阿里云 OSS 存储（FC 运行时使用）"""

    def __init__(self, bucket_name: str, endpoint: str, prefix: str = "repostbot/"):
        import oss2  # 延迟导入，本机不装也能跑 LocalStorage

        ak = config.env("ALIBABA_CLOUD_ACCESS_KEY_ID")
        sk = config.env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        token = config.env("ALIBABA_CLOUD_SECURITY_TOKEN")
        if token:
            auth = oss2.StsAuth(ak, sk, token)      # FC 配置 RAM 角色时的临时凭证
        else:
            auth = oss2.Auth(ak, sk)
        self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"

    def _key(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def _get(self, name: str) -> str | None:
        try:
            return self.bucket.get_object(self._key(name)).read().decode("utf-8")
        except Exception:
            return None

    def _put(self, name: str, data: str) -> None:
        self.bucket.put_object(self._key(name), data.encode("utf-8"))

    def load_accounts(self) -> dict[str, Any]:
        raw = self._get("accounts.json")
        if not raw:
            return json.loads(json.dumps(DEFAULT_ACCOUNTS))
        try:
            return json.loads(raw)
        except Exception:
            return json.loads(json.dumps(DEFAULT_ACCOUNTS))

    def load_state(self) -> dict[str, Any]:
        raw = self._get("state.json")
        if not raw:
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            return json.loads(raw)
        except Exception:
            return json.loads(json.dumps(DEFAULT_STATE))

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now_iso()
        self._put("state.json", json.dumps(state, ensure_ascii=False, indent=2))

    def load_auth(self, firm_id: str) -> str | None:
        return self._get(f"auth/{firm_id}.json")

    def save_auth(self, firm_id: str, data: str) -> None:
        self._put(f"auth/{firm_id}.json", data)


def _now_iso() -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def get_storage() -> Storage:
    """按环境变量选择实现：配了 OSS_BUCKET 就用 OSS，否则本机文件"""
    bucket = config.env("OSS_BUCKET")
    endpoint = config.env("OSS_ENDPOINT") or f"https://oss-{config.env('OSS_REGION', 'cn-shanghai')}.aliyuncs.com"
    if bucket:
        return OSSStorage(bucket, endpoint, config.env("OSS_KEY_PREFIX", "repostbot/"))
    return LocalStorage()


def record_run(state: dict[str, Any], firm_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """把一次执行结果写入 state（history 只保留最近 20 条）"""
    acc = state.setdefault("accounts", {}).setdefault(firm_id, {})
    acc["last_run_at"] = _now_iso()
    acc["last_status"] = result.get("status")
    acc["last_message"] = result.get("message")

    if result.get("status") == "success":
        acc["last_published_at"] = result.get("sort_time_after") or _now_iso()
        acc["source"] = "page"
        acc["fail_count"] = 0
    elif result.get("status") == "failed":
        acc["fail_count"] = int(acc.get("fail_count", 0)) + 1
        # 连续失败 3 次自动停用，避免带着故障反复跑被站点判定异常
        if acc["fail_count"] >= 3:
            acc["auto_disabled"] = True

    history = acc.setdefault("history", [])
    history.append({"at": _now_iso(), "status": result.get("status"), "message": result.get("message")})
    acc["history"] = history[-HISTORY_KEEP:]
    return state
