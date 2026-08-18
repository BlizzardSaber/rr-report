"""拉取流水线：请求两个接口 → 合并入库 → 记录运行轨迹。

run_pull.py（cron 定时拉取）和 run_push.py（发报表前先取最新数据）共用。
"""

from __future__ import annotations

import logging
from datetime import datetime

import fetch_rr
import store


def pull_and_store(config: dict, now_utc: datetime,
                   log: logging.Logger) -> dict:
    """执行一次完整拉取并入库，返回计数信息。"""
    rr_cfg = config["rr"]

    log.info("拉取分配流水（近24小时滚动窗口）...")
    assignment = fetch_rr.fetch_assignment(rr_cfg)
    log.info("分配流水接口返回 %d 条。", len(assignment))

    log.info("拉取客服上下线会话...")
    sessions = fetch_rr.fetch_availability(rr_cfg)
    log.info("上下线接口返回 %d 条。", len(sessions))

    conn = store._connect()
    try:
        new_assignment = store.merge_assignment(assignment, now_utc, conn)
        store.merge_sessions(sessions, now_utc, conn)
        conn.commit()
    finally:
        conn.close()

    store.record_pull_run(now_utc, len(assignment), len(sessions),
                          new_assignment, ok=True)
    log.info("入库完成：本次新增分配记录 %d 条（累计见 stats）。", new_assignment)
    return {
        "assignment_pulled": len(assignment),
        "sessions_pulled": len(sessions),
        "new_assignment": new_assignment,
    }
