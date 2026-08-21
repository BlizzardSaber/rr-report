"""按现有《RR API数据.xlsx》的样式生成报表（v2：班次列 + 按客服分组 + 汇总页）。

复刻要点（对照原表逐项核对过）：
- 全表 Arial 10 号字体，时间列数字格式 yyyy-mm-dd hh:mm:ss，冻结首行
- 「近24小时分配数据」：新增「班次」列（位于客服列前）；同一客服的记录排在一起，
  按 班次(白→中→夜) → 客服 → 时间倒序 排列；带自动筛选
- 「agent上下线数据」：新增「在线时长(分钟)」列（下线-上线；进行中按报表时刻截断）；
  同一客服的会话排在一起
- 「按客服汇总」：每天每个客服的分配工单数（工单去重）与分配记录数

班次判定（夜班名单固定；其他人按分配时间投票）：
- 名单内（默认 Floria/Linna/Eva/Nancy）→ 永远夜班
- 落在「独家时段」的分配记录投票：上午（白班开始前 ~ 中班开始前）投白班，
  晚间（白班结束后 ~ 中班结束）投中班，深夜投夜班；交叉时段 13:30-17:30 不投票
- 得票最多者胜出（考虑负责人会提前 10-20 分钟接单，时段边界已外扩容差）；
  无票时默认白班。接口时间为 UTC，写入前 +tz_offset_hours 转为 UTC+8。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

DT_FORMAT = "yyyy\\-mm\\-dd\\ hh:mm:ss"
BASE_FONT = Font(name="Arial", size=10)

SHIFT_ORDER = {"白班": 0, "中班": 1, "夜班": 2}

SHEET1_NAME = "分配数据"
SHEET1_HEADERS = ["时间", "工单ID", "班次", "客服", "队列类型", "ID"]
SHEET1_WIDTHS = {"A": 19.14, "B": 8.57, "C": 8.0, "D": 10.0, "E": 22.0, "F": 17.5}

SHEET2_NAME = "agent上下线数据"
SHEET2_HEADERS = ["客服姓名", "上线时间 (UTC+8)", "下线时间 (UTC+8)", "当前状态", "在线时长(分钟)"]
SHEET2_WIDTHS = {"A": 16.96, "B": 19.14, "C": 19.14, "D": 16.5, "E": 15.0}

SHEET3_NAME = "按客服汇总"
SHEET3_HEADERS = ["日期", "班次", "客服", "分配工单数", "分配记录数"]
SHEET3_WIDTHS = {"A": 13.0, "B": 8.0, "C": 12.0, "D": 13.0, "E": 13.0}


def _shift(dt_utc: datetime, tz_offset_hours: int) -> datetime:
    """UTC → 报表时区（默认 UTC+8）。"""
    return dt_utc + timedelta(hours=tz_offset_hours)


def _to_minutes(hhmm: str) -> int:
    """'08:30' → 510。"""
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


def infer_agent_shifts(assignment_rows: list[dict[str, Any]], report_cfg: dict,
                       tz_offset_hours: int) -> dict[str, str]:
    """推断每个客服的班次，返回 {客服名: 白班/中班/夜班}。

    夜班名单直接判定；其他人用独家时段投票（见模块 docstring）。
    """
    night_names = {n.strip().lower() for n in report_cfg.get("night_shift_agents", [])}
    tol = int(report_cfg.get("shift_early_minutes", 20))
    shifts = report_cfg.get("shifts", {})
    day_s = _to_minutes(shifts.get("day", ["08:30", "18:00"])[0])
    day_e = _to_minutes(shifts.get("day", ["08:30", "18:00"])[1])
    mid = shifts.get("mid", [["13:30", "17:30"], ["19:00", "23:00"]])
    mid1_s = _to_minutes(mid[0][0])
    mid1_e = _to_minutes(mid[0][1])
    mid2_e = _to_minutes(mid[1][1])

    votes: dict[str, dict[str, int]] = {}
    labels: dict[str, str] = {}
    for r in assignment_rows:
        name = r["agent_name"]
        if name.strip().lower() in night_names:
            labels[name] = "夜班"
            continue
        tod = _shift(r["event_date_utc"], tz_offset_hours)
        m = tod.hour * 60 + tod.minute
        vote = None
        if m < day_s - tol or m > mid2_e:            # 深夜：夜班独家
            vote = "夜班"
        elif day_s - tol <= m < mid1_s - tol:        # 上午：白班独家
            vote = "白班"
        elif mid1_e < m <= day_e:                    # 17:30-18:00 尾段：白班独家
            vote = "白班"
        elif day_e < m <= mid2_e:                    # 晚间：中班独家
            vote = "中班"
        # mid1_s-tol ~ mid1_e（约 13:10-17:30）为白/中交叉时段，不投票
        if vote:
            votes.setdefault(name, {"白班": 0, "中班": 0, "夜班": 0})[vote] += 1

    for name, v in votes.items():
        labels[name] = max(v, key=v.get) if any(v.values()) else "白班"
    # 没有任何投票的（全部落在交叉时段）默认白班
    for r in assignment_rows:
        labels.setdefault(r["agent_name"], "白班")
    return labels


def _style_sheet(ws, widths: dict[str, float], autofilter: bool = False) -> None:
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width
    for row in ws.iter_rows():
        for cell in row:
            cell.font = BASE_FONT
    ws.freeze_panes = "A2"
    if autofilter:
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"


def build_report_xlsx(
    assignment_rows: list[dict[str, Any]],
    session_rows: list[dict[str, Any]],
    output_path: str,
    tz_offset_hours: int = 8,
    report_cfg: dict | None = None,
    now_utc: datetime | None = None,
) -> str:
    """生成报表 XLSX，返回路径。

    assignment_rows: store.query_assignment_since() 的返回值（时间为 UTC）
    session_rows:     store.query_sessions_since() 的返回值（时间为 UTC）
    report_cfg:       config.report 子字典（班次配置）
    now_utc:          报表时刻（计算进行中会话的在线时长用）
    """
    report_cfg = report_cfg or {}
    shift_map = infer_agent_shifts(assignment_rows, report_cfg, tz_offset_hours)

    wb = Workbook()

    # ---- Sheet1 分配数据（按 班次→客服 分组，组内时间倒序，专员之间空一行） ----
    ws1 = wb.active
    ws1.title = SHEET1_NAME
    ordered = sorted(
        assignment_rows,
        key=lambda r: (SHIFT_ORDER.get(shift_map.get(r["agent_name"], "白班"), 0),
                       r["agent_name"],
                       -r["event_date_utc"].timestamp(),
                       -r["id"]),
    )
    ws1.append(SHEET1_HEADERS)
    prev_agent = None
    for r in ordered:
        if prev_agent is not None and r["agent_name"] != prev_agent:
            ws1.append([])  # 专员之间空一行
        prev_agent = r["agent_name"]
        ws1.append([
            _shift(r["event_date_utc"], tz_offset_hours),
            int(r["ticket_id"]) if str(r["ticket_id"]).isdigit() else r["ticket_id"],
            shift_map.get(r["agent_name"], "白班"),
            r["agent_name"],
            r["queue_name"],
            r["id"],
        ])
    for row in range(2, ws1.max_row + 1):
        if ws1.cell(row=row, column=1).value is not None:
            ws1.cell(row=row, column=1).number_format = DT_FORMAT
    _style_sheet(ws1, SHEET1_WIDTHS, autofilter=True)

    # ---- Sheet2 agent上下线数据（按客服分组，组内上线时间倒序；专员之间空一行） ----
    ws2 = wb.create_sheet(SHEET2_NAME)
    now_utc = now_utc or datetime.utcnow()
    sessions_ordered = sorted(
        session_rows,
        key=lambda r: (r["agent_name"], -r["start_utc"].timestamp()),
    )
    ws2.append(SHEET2_HEADERS)
    prev_agent = None
    for r in sessions_ordered:
        if prev_agent is not None and r["agent_name"] != prev_agent:
            ws2.append([])  # 专员之间空一行
        prev_agent = r["agent_name"]
        ongoing = r["end_utc"] is None
        end_for_calc = r["end_utc"] or now_utc
        ws2.append([
            r["agent_name"],
            _shift(r["start_utc"], tz_offset_hours),
            "进行中..." if ongoing else _shift(r["end_utc"], tz_offset_hours),
            "🟢 在线中" if ongoing else "已下线",
            max(0, round((end_for_calc - r["start_utc"]).total_seconds() / 60)),
        ])
    for row in range(2, ws2.max_row + 1):
        if ws2.cell(row=row, column=2).value is not None:
            ws2.cell(row=row, column=2).number_format = DT_FORMAT
            if not isinstance(ws2.cell(row=row, column=3).value, str):
                ws2.cell(row=row, column=3).number_format = DT_FORMAT
    _style_sheet(ws2, SHEET2_WIDTHS)

    # ---- Sheet3 按客服汇总（每天每客服：去重工单数 + 记录数） ----
    ws3 = wb.create_sheet(SHEET3_NAME)
    summary: dict[tuple, dict[str, set]] = {}
    for r in assignment_rows:
        local = _shift(r["event_date_utc"], tz_offset_hours)
        key = (local.strftime("%Y-%m-%d"),
               SHIFT_ORDER.get(shift_map.get(r["agent_name"], "白班"), 0),
               shift_map.get(r["agent_name"], "白班"),
               r["agent_name"])
        agg = summary.setdefault(key, {"tickets": set(), "count": 0})
        agg["tickets"].add(str(r["ticket_id"]))
        agg["count"] += 1
    ws3.append(SHEET3_HEADERS)
    for key in sorted(summary, key=lambda k: (k[0], k[1], -len(summary[k]["tickets"]), k[3])):
        date_str, _, shift_label, name = key
        agg = summary[key]
        ws3.append([date_str, shift_label, name, len(agg["tickets"]), agg["count"]])
    _style_sheet(ws3, SHEET3_WIDTHS, autofilter=True)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    return output_path


def main() -> int:
    """命令行自测：从库里取数生成表格到 output/。"""
    import argparse
    import sys
    from datetime import timezone

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config_utils  # noqa: E402
    import store  # noqa: E402

    parser = argparse.ArgumentParser(description="生成 RR 报表 XLSX")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 路径")
    args = parser.parse_args()

    config = config_utils.load_config()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    a_rows = store.query_assignment_since(
        int(config["report"]["assignment_window_hours"]), now_utc)
    s_rows = store.query_sessions_since(
        int(config["report"]["availability_days"]), now_utc)
    build_report_xlsx(a_rows, s_rows, args.output,
                      tz_offset_hours=int(config["rr"].get("tz_offset_hours", 8)),
                      report_cfg=config["report"], now_utc=now_utc)
    print(f"已生成: {args.output}（分配 {len(a_rows)} 条 / 会话 {len(s_rows)} 条）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
