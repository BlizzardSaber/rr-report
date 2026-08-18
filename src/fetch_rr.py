"""拉取 RR（RoundRobin）系统的两个数据接口。

接口说明（2026-08 实测）：
- ___get_assignment_log   ：近 24 小时的工单分配流水（滚动窗口，服务端固定）
- ___get_agent_availability：客服上下线会话记录（服务端保留约 14 天）

两个接口都是 GET，只接受 p1=<api_key> 一个参数；多传任何参数都会触发
存储过程参数数量错误。因此想保留超过 24 小时的分配数据，只能靠定时
轮询 + 本地按唯一 id 去重累积（见 store.py）。

时间口径：接口返回的时间是 UTC，报表时统一 +tz_offset_hours 转为 UTC+8。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

HTTP_TIMEOUT = 30


def _parse_dt(s: str) -> datetime:
    """解析接口时间字符串，兼容带毫秒和不带毫秒两种格式。"""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间字符串: {s!r}")


def _api_url(base_url: str, proc: str, api_key: str) -> str:
    return f"{base_url.rstrip('/')}/{proc}?p1={urllib.parse.quote(api_key)}"


def _fetch_data(url: str) -> list[dict[str, Any]]:
    """请求单个接口并返回 data 列表，返回结构异常时给出可读错误。"""
    req = urllib.request.Request(url, headers={"User-Agent": "rr-report/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict) or "data" not in payload:
        # 常见于 key 失效 / 参数错误，接口会把 MySQL 报错放在 message 里
        msg = payload.get("message") if isinstance(payload, dict) else str(payload)[:200]
        raise RuntimeError(f"接口返回异常: {msg or str(payload)[:200]}")
    return payload["data"]


def fetch_assignment(rr_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """拉取近 24 小时分配流水，返回标准化后的列表（时间为 naive UTC）。"""
    url = _api_url(rr_cfg["base_url"], rr_cfg["assignment_proc"], rr_cfg["api_key"])
    rows = _fetch_data(url)
    out = []
    for r in rows:
        out.append({
            "id": int(r["id"]),
            "event_date_utc": _parse_dt(r["event_date"]),
            "ticket_id": str(r.get("ticket_id") or ""),
            "agent_id": str(r.get("agent_id") or ""),
            "agent_name": r.get("agent_name") or "",
            "queue_name": r.get("queue_name") or "",
            "message": r.get("message"),
        })
    return out


def fetch_availability(rr_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """拉取客服上下线会话，返回标准化后的列表（时间为 naive UTC，end 可能为 None）。"""
    url = _api_url(rr_cfg["base_url"], rr_cfg["availability_proc"], rr_cfg["api_key"])
    rows = _fetch_data(url)
    out = []
    for r in rows:
        end_raw = (r.get("end") or "").strip()
        out.append({
            "agent_name": r.get("agent_name") or "",
            "agent_id": str(r.get("agent_id") or ""),
            "start_utc": _parse_dt(r["start"]),
            "end_utc": _parse_dt(end_raw) if end_raw else None,
        })
    return out
