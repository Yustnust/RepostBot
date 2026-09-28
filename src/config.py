"""配置与环境变量读取。敏感值一律从环境变量取，不写进代码。

> v1 范围：本仓库当前仅服务「上海臻至律师事务所」一家客户（firm_a）。
> 多账号能力已通过 accounts.json 的数组结构预留，但暂不实现。
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = REPO_ROOT / ".auth"
CONFIG_DIR = REPO_ROOT / "config"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def env_int(name: str, default: int) -> int:
    raw = env(name)
    try:
        return int(raw)
    except ValueError:
        return default


def storage_path(firm_id: str) -> Path:
    """登录态文件路径，默认 .auth/<firm_id>.json"""
    return AUTH_DIR / f"{firm_id}.json"


def resolve_ref(ref: str) -> str:
    """把 accounts.json 里的 `env:XXX` 引用解析成实际值"""
    if ref.startswith("env:"):
        return env(ref[4:])
    return ref
