"""cron 入口公用工具：日志初始化 + 单实例锁。"""

from __future__ import annotations

import fcntl
import logging
import os
import sys
from datetime import datetime, timezone

from config_utils import PROJECT_ROOT

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOCK_DIR = os.path.join(PROJECT_ROOT, "data")


def utcnow() -> datetime:
    """当前 naive UTC 时间（与接口/数据库口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def setup_logging(task: str) -> str:
    """配置日志，同时输出到文件和 stderr，返回日志文件路径。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(LOG_DIR, f"{task}_{stamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
        force=True,
    )
    return log_file


class SingleInstance:
    """flock 文件锁：防止上一轮 cron 还没跑完时重复执行。"""

    def __init__(self, name: str):
        os.makedirs(LOCK_DIR, exist_ok=True)
        self._path = os.path.join(LOCK_DIR, f".{name}.lock")
        self._fh = None

    def __enter__(self) -> "SingleInstance":
        self._fh = open(self._path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            raise RuntimeError("上一次任务仍在运行，本次跳过")
        return self

    def __exit__(self, *exc) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
