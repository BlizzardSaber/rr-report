"""定时拉取入口：请求两个 RR 接口 → 按唯一键去重合并进本地 SQLite。

cron 按 schedule.pull_interval_minutes 调用本脚本。分配接口只有 24 小时
滚动窗口，拉取频率建议不超过 60 分钟，否则中间的数据会永久丢失。

用法：
    python run_pull.py     # cron / 手动
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_utils  # noqa: E402
import store  # noqa: E402
from common import SingleInstance, setup_logging, utcnow  # noqa: E402
from pipeline import pull_and_store  # noqa: E402


def main() -> int:
    log_file = setup_logging("pull")
    log = logging.getLogger("rr.pull")
    log.info("=== 定时拉取开始 ===")

    try:
        config = config_utils.load_config()
    except RuntimeError as e:
        log.error("%s", e)
        return 1
    if not config["rr"].get("api_key"):
        log.error("rr.api_key 未配置，请运行 manage.sh 完善。")
        return 1

    try:
        with SingleInstance("pull"):
            pull_and_store(config, utcnow(), log)
    except RuntimeError as e:
        log.warning("%s", e)
        return 0
    except Exception:
        log.exception("拉取失败")
        store.record_pull_run(utcnow(), 0, 0, 0, ok=False)
        return 2

    log.info("=== 拉取完成（日志: %s）===", log_file)
    return 0


if __name__ == "__main__":
    import logging
    sys.exit(main())
