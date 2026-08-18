"""查看数据累计状态与最近日志（manage.sh 菜单调用）。"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_utils  # noqa: E402
import store  # noqa: E402
from common import LOG_DIR, utcnow  # noqa: E402


def main() -> int:
    config = config_utils.load_config()
    tz = int(config["rr"].get("tz_offset_hours", 8))
    s = store.stats(utcnow())

    def local(ts_utc: str | None) -> str:
        if not ts_utc:
            return "—"
        from datetime import datetime
        dt = datetime.strptime(ts_utc, "%Y-%m-%d %H:%M:%S")
        return (dt + timedelta(hours=tz)).strftime("%Y-%m-%d %H:%M:%S")

    print("--- 数据累计状态 ---")
    print(f"  分配记录累计  : {s['total_assignment']} 条")
    print(f"  上下线会话累计: {s['total_sessions']} 条")
    print(f"  当前在线客服  : {s['online_agents']} 人")
    print(f"  最早分配数据  : {local(s['first_event_utc'])} (UTC+{tz})")
    print(f"  最近一次拉取  : {local(s['last_pull_utc'])} (UTC+{tz})")
    print(f"  数据库文件    : {s['db_path']}")

    print("\n--- 最近日志（logs/ 最新 20 行）---")
    logs = sorted(
        (os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.endswith(".log")),
        key=os.path.getmtime, reverse=True,
    )[:3]
    if not logs:
        print("  （暂无日志）")
    for path in logs:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print(f"  [{os.path.basename(path)}]")
        for line in lines[-20:]:
            print("   ", line.rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
