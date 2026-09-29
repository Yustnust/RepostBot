"""统一日志：同时输出到控制台和 logs/repostbot.log。

- logs/ 已被 .gitignore 排除（*.log），不会误提交。
- 本地开发：写到仓库 logs/ 目录，便于回溯。
- 阿里云 FC：容器根目录可写，但若无权限则静默跳过文件日志，仅保留控制台输出。
- 所有模块统一用 `from logger import get_logger; log = get_logger()` 获取，避免重复添加 handler。
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOGGER_NAME = "repostbot"
_LOG_FILE = "repostbot.log"

# 统一时区：本机 Python 进程可能拿到 UTC，导致日志时间戳与实际差 8 小时。
# 强制所有日志时间戳使用北京时间（与 FC 容器内的 TZ=Asia/Shanghai 一致）。
CST = timezone(timedelta(hours=8))


def _cst_converter(seconds: float | None = None):
    ts = seconds if seconds is not None else time.time()
    return datetime.fromtimestamp(ts, CST).timetuple()


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fmt.converter = _cst_converter

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 文件日志尽力而为：本地写 logs/，FC 等无权限环境静默跳过
    try:
        log_dir = Path(__file__).resolve().parents[1] / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / _LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except Exception:
        pass

    return logger
