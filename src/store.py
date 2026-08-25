"""本地数据累积存储（SQLite）。

为什么需要本地累积：
- 分配流水接口只返回「当前时刻往前约 24 小时」的滚动窗口（无法传时间参数），
  不轮询累积的话，超过 24 小时的数据就永久丢失；
- 上下线接口保留约 14 天，同样有限。

合并规则：
- assignment_log 以接口返回的唯一 id 为主键，INSERT OR IGNORE 去重；
- agent_sessions 以 (客服姓名, 上线时间) 为主键；「进行中」会话（end 为空）
  后续拉到下线时间时补写 end，空的 end 永远不会覆盖已有的下线时间。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from config_utils import PROJECT_ROOT

DB_PATH = os.path.join(PROJECT_ROOT, "data", "rr_data.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assignment_log (
    id             INTEGER PRIMARY KEY,
    event_date_utc TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM:SS'（UTC）
    ticket_id      TEXT,
    agent_id       TEXT,
    agent_name     TEXT,
    queue_name     TEXT,
    message        TEXT,
    first_seen_utc TEXT,
    last_seen_utc  TEXT
);
CREATE INDEX IF NOT EXISTS idx_assignment_event_date ON assignment_log(event_date_utc);

CREATE TABLE IF NOT EXISTS agent_sessions (
    agent_name     TEXT NOT NULL,
    start_utc      TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM:SS'（UTC）
    end_utc        TEXT,               -- NULL = 进行中
    agent_id       TEXT,
    first_seen_utc TEXT,
    last_seen_utc  TEXT,
    PRIMARY KEY (agent_name, start_utc)
);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON agent_sessions(start_utc);

CREATE TABLE IF NOT EXISTS pull_runs (
    ts_utc             TEXT PRIMARY KEY,
    assignment_count   INTEGER,
    availability_count INTEGER,
    new_assignment     INTEGER,
    ok                 INTEGER
);
"""


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def merge_assignment(rows: list[dict[str, Any]], now_utc: datetime,
                     conn: sqlite3.Connection | None = None) -> int:
    """合并分配流水，返回本次新增条数。"""
    own = conn is None
    conn = conn or _connect()
    try:
        now = _fmt(now_utc)
        before = conn.execute("SELECT COUNT(*) FROM assignment_log").fetchone()[0]
        conn.executemany(
            """INSERT OR IGNORE INTO assignment_log
               (id, event_date_utc, ticket_id, agent_id, agent_name, queue_name,
                message, first_seen_utc, last_seen_utc)
               VALUES (:id, :event_date_utc, :ticket_id, :agent_id, :agent_name,
                       :queue_name, :message, :now, :now)""",
            [{"id": r["id"],
              "event_date_utc": _fmt(r["event_date_utc"]),
              "ticket_id": r["ticket_id"], "agent_id": r["agent_id"],
              "agent_name": r["agent_name"], "queue_name": r["queue_name"],
              "message": r["message"], "now": now} for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM assignment_log").fetchone()[0]
        if own:
            conn.commit()
        return after - before
    finally:
        if own:
            conn.close()


def merge_sessions(rows: list[dict[str, Any]], now_utc: datetime,
                   conn: sqlite3.Connection | None = None) -> None:
    """合并上下线会话：新会话插入；已有会话仅在拿到下线时间时补写 end。"""
    own = conn is None
    conn = conn or _connect()
    try:
        now = _fmt(now_utc)
        conn.executemany(
            """INSERT INTO agent_sessions
               (agent_name, start_utc, end_utc, agent_id, first_seen_utc, last_seen_utc)
               VALUES (:agent_name, :start_utc, :end_utc, :agent_id, :now, :now)
               ON CONFLICT(agent_name, start_utc) DO UPDATE SET
                   end_utc      = COALESCE(excluded.end_utc, end_utc),
                   agent_id     = COALESCE(excluded.agent_id, agent_id),
                   last_seen_utc = excluded.last_seen_utc""",
            [{"agent_name": r["agent_name"],
              "start_utc": _fmt(r["start_utc"]),
              "end_utc": _fmt(r["end_utc"]) if r["end_utc"] else None,
              "agent_id": r["agent_id"], "now": now} for r in rows],
        )
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def record_pull_run(now_utc: datetime, assignment_count: int,
                    availability_count: int, new_assignment: int, ok: bool) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pull_runs VALUES (?,?,?,?,?)",
            (_fmt(now_utc), assignment_count, availability_count,
             new_assignment, 1 if ok else 0),
        )
        conn.commit()
    finally:
        conn.close()


def query_assignment_since(window_hours: int,
                           now_utc: datetime,
                           since: datetime | None = None) -> list[dict[str, Any]]:
    """取窗口内的分配流水（时间为精确到秒的 UTC），按时间倒序。

    window_hours=0 表示不过滤（全部累积）；since 显式指定窗口起点时优先生效
    （用于按自然月取数）。
    """
    if since is None:
        since = (now_utc - timedelta(hours=window_hours)
                 if window_hours > 0 else datetime(2000, 1, 1))
    since = _fmt(since)
    conn = _connect()
    try:
        cur = conn.execute(
            """SELECT event_date_utc, ticket_id, agent_name, queue_name, id
               FROM assignment_log
               WHERE event_date_utc >= ?
               ORDER BY event_date_utc DESC, id DESC""",
            (since,),
        )
        return [
            {"event_date_utc": _parse(r[0]), "ticket_id": r[1],
             "agent_name": r[2], "queue_name": r[3], "id": r[4]}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def query_sessions_since(days: int, now_utc: datetime,
                         since: datetime | None = None) -> list[dict[str, Any]]:
    """取窗口内的上下线会话，按上线时间倒序。

    days=0 表示不过滤（全部累积）；since 显式指定窗口起点时优先生效。
    """
    if since is None:
        since = (now_utc - timedelta(days=days)
                 if days > 0 else datetime(2000, 1, 1))
    since = _fmt(since)
    conn = _connect()
    try:
        cur = conn.execute(
            """SELECT agent_name, start_utc, end_utc
               FROM agent_sessions
               WHERE start_utc >= ?
               ORDER BY start_utc DESC, agent_name""",
            (since,),
        )
        return [
            {"agent_name": r[0], "start_utc": _parse(r[1]),
             "end_utc": _parse(r[2]) if r[2] else None}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def stats(now_utc: datetime) -> dict[str, Any]:
    """数据累计概况，用于菜单展示和邮件正文。"""
    conn = _connect()
    try:
        def one(sql: str, *args: Any) -> Any:
            return conn.execute(sql, args).fetchone()[0]

        total_assign = one("SELECT COUNT(*) FROM assignment_log")
        total_sessions = one("SELECT COUNT(*) FROM agent_sessions")
        online = one("SELECT COUNT(DISTINCT agent_name) FROM agent_sessions "
                     "WHERE end_utc IS NULL")
        last_pull = conn.execute(
            "SELECT ts_utc FROM pull_runs ORDER BY ts_utc DESC LIMIT 1"
        ).fetchone()
        first_event = conn.execute(
            "SELECT MIN(event_date_utc) FROM assignment_log"
        ).fetchone()[0]
        return {
            "total_assignment": total_assign,
            "total_sessions": total_sessions,
            "online_agents": online,
            "last_pull_utc": last_pull[0] if last_pull else None,
            "first_event_utc": first_event,
            "db_path": DB_PATH,
        }
    finally:
        conn.close()
