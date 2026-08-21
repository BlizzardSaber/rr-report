"""按现有《RR API数据.xlsx》的样式生成报表（v3）。

工作表结构：
- 「分配数据」：全部累积的分配明细，带「班次」列，整体按时间倒序（最原始的排序）
- 「agent上下线数据-夜班」：夜班名单内客服的会话，按上线时间倒序
- 「agent上下线数据-白中班」：其他客服的会话，按上线时间倒序（白/中班以班次列区分）
- 「按客服汇总」：每天每个客服的分配工单数（去重）与记录数

样式：全表 Arial 10、时间列 yyyy-mm-dd hh:mm:ss、冻结首行；
明细页不按人分组（同一人的记录按时间与其他人自然穿插）。

班次判定（夜班名单固定；其他人按独家时段投票，同一人在所有页签标注一致）：
- 名单内（默认 Floria/Linna/Eva/Nancy）→ 永远夜班
- 分配时间落在上午档（白班开始前 ~ 中班开始前）投白班，晚间档（白班结束后 ~
  中班结束）投中班，深夜投夜班；13:30-17:30 白/中交叉时段不投票，票多者胜出
- 只有会话、没有分配记录的客服，退化为按登录时刻投票（上午登录→白班，
  午后/晚间登录→中班，凌晨登录→夜班）
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

SHEET2N_NAME = "agent上下线数据-夜班"
SHEET2D_NAME = "agent上下线数据-白中班"
SHEET2_HEADERS = ["班次", "客服姓名", "上线时间 (UTC+8)", "下线时间 (UTC+8)", "当前状态"]
SHEET2_WIDTHS = {"A": 8.0, "B": 16.96, "C": 19.14, "D": 19.14, "E": 16.5}

SHEET3_NAME = "按客服汇总"
SHEET3_HEADERS = ["日期", "班次", "客服", "分配工单数", "分配记录数"]
SHEET3_WIDTHS = {"A": 13.0, "B": 8.0, "C": 12.0, "D": 13.0, "E": 13.0}


def _shift_tz(dt_utc: datetime, tz_offset_hours: int) -> datetime:
    """UTC → 报表时区（默认 UTC+8）。"""
    return dt_utc + timedelta(hours=tz_offset_hours)


def _to_minutes(hhmm: str) -> int:
    """'08:30' → 510。"""
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


class ShiftRules:
    """从 config.report 解析班次时段，提供按「一天内分钟数」投票的判定。"""

    def __init__(self, report_cfg: dict):
        self.night_names = {n.strip().lower() for n in report_cfg.get("night_shift_agents", [])}
        tol = int(report_cfg.get("shift_early_minutes", 20))
        shifts = report_cfg.get("shifts", {})
        day = shifts.get("day", ["08:30", "18:00"])
        mid = shifts.get("mid", [["13:30", "17:30"], ["19:00", "23:00"]])
        self.day_s = _to_minutes(day[0])
        self.day_e = _to_minutes(day[1])
        self.mid1_s = _to_minutes(mid[0][0])
        self.mid1_e = _to_minutes(mid[0][1])
        self.mid2_e = _to_minutes(mid[1][1])
        self.tol = tol

    def is_fixed_night(self, name: str) -> bool:
        return name.strip().lower() in self.night_names

    def vote(self, m: int) -> str | None:
        """按一天内分钟数投票；交叉时段返回 None（不投票）。"""
        if m < self.day_s - self.tol or m > self.mid2_e:   # 深夜：夜班独家
            return "夜班"
        if self.day_s - self.tol <= m < self.mid1_s - self.tol:  # 上午：白班独家
            return "白班"
        if self.mid1_e < m <= self.day_e:                  # 17:30-18:00 尾段：白班独家
            return "白班"
        if self.day_e < m <= self.mid2_e:                  # 晚间：中班独家
            return "中班"
        return None  # 约 13:10-17:30 白/中交叉，不投票

    def vote_login(self, m: int) -> str | None:
        """按登录时刻投票（仅用于没有分配记录的客服兜底）：
        上午登录→白班，午后/晚间登录→中班，凌晨登录→夜班。"""
        if m < self.day_s - self.tol:
            return "夜班"
        if m < self.mid1_s - self.tol:
            return "白班"
        return "中班"


def infer_agent_shifts(assignment_rows: list[dict[str, Any]],
                       session_rows: list[dict[str, Any]],
                       report_cfg: dict,
                       tz_offset_hours: int) -> dict[str, str]:
    """推断每个客服的班次，返回 {客服名: 白班/中班/夜班}。"""
    rules = ShiftRules(report_cfg)
    votes: dict[str, dict[str, int]] = {}
    labels: dict[str, str] = {}

    for r in assignment_rows:
        name = r["agent_name"]
        if rules.is_fixed_night(name):
            labels[name] = "夜班"
            continue
        tod = _shift_tz(r["event_date_utc"], tz_offset_hours)
        vote = rules.vote(tod.hour * 60 + tod.minute)
        if vote:
            votes.setdefault(name, {"白班": 0, "中班": 0, "夜班": 0})[vote] += 1

    for r in session_rows:  # 没有分配记录的客服，用登录时刻兜底投票
        name = r["agent_name"]
        if rules.is_fixed_night(name):
            labels[name] = "夜班"
            continue
        if name in votes or name in labels:
            continue
        tod = _shift_tz(r["start_utc"], tz_offset_hours)
        vote = rules.vote_login(tod.hour * 60 + tod.minute)
        if vote:
            votes.setdefault(name, {"白班": 0, "中班": 0, "夜班": 0})[vote] += 1

    for name, v in votes.items():
        labels[name] = max(v, key=v.get)
    for r in assignment_rows:
        labels.setdefault(r["agent_name"], "白班")  # 全部落在交叉时段的默认白班
    for r in session_rows:
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


def _write_session_sheet(ws, rows: list[dict[str, Any]], shift_map: dict[str, str],
                         tz_offset_hours: int) -> None:
    """写一个上下线工作表：班次|客服|上线|下线|状态，按上线时间倒序。"""
    ws.append(SHEET2_HEADERS)
    for r in sorted(rows, key=lambda r: -r["start_utc"].timestamp()):
        ongoing = r["end_utc"] is None
        ws.append([
            shift_map.get(r["agent_name"], "白班"),
            r["agent_name"],
            _shift_tz(r["start_utc"], tz_offset_hours),
            "进行中..." if ongoing else _shift_tz(r["end_utc"], tz_offset_hours),
            "🟢 在线中" if ongoing else "已下线",
        ])
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=3).value is not None:
            ws.cell(row=row, column=3).number_format = DT_FORMAT
            if not isinstance(ws.cell(row=row, column=4).value, str):
                ws.cell(row=row, column=4).number_format = DT_FORMAT
    _style_sheet(ws, SHEET2_WIDTHS)


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
    now_utc:          预留（当前版本未使用）
    """
    report_cfg = report_cfg or {}
    rules = ShiftRules(report_cfg)
    shift_map = infer_agent_shifts(assignment_rows, session_rows, report_cfg,
                                   tz_offset_hours)

    wb = Workbook()

    # ---- Sheet1 分配数据（最原始排序：整体按时间倒序，仅增加班次列） ----
    ws1 = wb.active
    ws1.title = SHEET1_NAME
    ws1.append(SHEET1_HEADERS)
    for r in sorted(assignment_rows,
                    key=lambda r: (-r["event_date_utc"].timestamp(), -r["id"])):
        ws1.append([
            _shift_tz(r["event_date_utc"], tz_offset_hours),
            int(r["ticket_id"]) if str(r["ticket_id"]).isdigit() else r["ticket_id"],
            shift_map.get(r["agent_name"], "白班"),
            r["agent_name"],
            r["queue_name"],
            r["id"],
        ])
    for row in range(2, ws1.max_row + 1):
        ws1.cell(row=row, column=1).number_format = DT_FORMAT
    _style_sheet(ws1, SHEET1_WIDTHS, autofilter=True)

    # ---- Sheet2 上下线数据拆两页：夜班 / 白中班（均按上线时间倒序） ----
    night_rows = [r for r in session_rows if rules.is_fixed_night(r["agent_name"])]
    other_rows = [r for r in session_rows if not rules.is_fixed_night(r["agent_name"])]
    _write_session_sheet(wb.create_sheet(SHEET2N_NAME), night_rows, shift_map,
                         tz_offset_hours)
    _write_session_sheet(wb.create_sheet(SHEET2D_NAME), other_rows, shift_map,
                         tz_offset_hours)

    # ---- Sheet3 按客服汇总（每天每客服：去重工单数 + 记录数） ----
    ws3 = wb.create_sheet(SHEET3_NAME)
    summary: dict[tuple, dict[str, set]] = {}
    for r in assignment_rows:
        local = _shift_tz(r["event_date_utc"], tz_offset_hours)
        shift_label = shift_map.get(r["agent_name"], "白班")
        key = (local.strftime("%Y-%m-%d"), SHIFT_ORDER.get(shift_label, 0),
               shift_label, r["agent_name"])
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
