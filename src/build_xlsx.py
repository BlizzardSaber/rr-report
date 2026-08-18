"""按现有《RR API数据.xlsx》的样式生成报表。

复刻要点（对照原表逐项核对过）：
- 两个工作表：近24小时分配数据 / agent上下线数据
- 全表 Arial 10 号字体
- 时间列数字格式 yyyy-mm-dd hh:mm:ss，列宽 19.14
- 冻结首行；分配表带自动筛选
- 上下线表：进行中的会话下线时间写「进行中...」、状态「🟢 在线中」；
  已结束的会话状态写「已下线」
- 接口时间为 UTC，写入前 +tz_offset_hours 转为 UTC+8
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

DT_FORMAT = "yyyy\\-mm\\-dd\\ hh:mm:ss"
BASE_FONT = Font(name="Arial", size=10)
CENTER = Alignment(horizontal="center", vertical="center")

SHEET1_NAME = "近24小时分配数据"
SHEET1_HEADERS = ["时间", "工单ID", "客服", "队列类型", "ID"]
SHEET1_WIDTHS = {"A": 19.14, "B": 8.57, "C": 10.0, "D": 22.0, "E": 17.5}

SHEET2_NAME = "agent上下线数据"
SHEET2_HEADERS = ["客服姓名", "上线时间 (UTC+8)", "下线时间 (UTC+8)", "当前状态"]
SHEET2_WIDTHS = {"A": 16.96, "B": 19.14, "C": 19.14, "D": 16.5}


def _shift(dt_utc: datetime, tz_offset_hours: int) -> datetime:
    """UTC → 报表时区（默认 UTC+8）。"""
    return dt_utc + timedelta(hours=tz_offset_hours)


def _style_sheet(ws, widths: dict[str, float]) -> None:
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width
    for row in ws.iter_rows():
        for cell in row:
            cell.font = BASE_FONT


def build_report_xlsx(
    assignment_rows: list[dict[str, Any]],
    session_rows: list[dict[str, Any]],
    output_path: str,
    tz_offset_hours: int = 8,
) -> str:
    """生成报表 XLSX，返回路径。

    assignment_rows: store.query_assignment_since() 的返回值（时间为 UTC）
    session_rows:     store.query_sessions_since() 的返回值（时间为 UTC）
    """
    wb = Workbook()

    # ---- Sheet1 近24小时分配数据 ----
    ws1 = wb.active
    ws1.title = SHEET1_NAME
    ws1.append(SHEET1_HEADERS)
    for r in assignment_rows:
        ws1.append([
            _shift(r["event_date_utc"], tz_offset_hours),
            int(r["ticket_id"]) if str(r["ticket_id"]).isdigit() else r["ticket_id"],
            r["agent_name"],
            r["queue_name"],
            r["id"],
        ])
    for row in range(2, ws1.max_row + 1):
        ws1.cell(row=row, column=1).number_format = DT_FORMAT
    ws1.freeze_panes = "A2"
    ws1.auto_filter.ref = f"A1:{get_column_letter(len(SHEET1_HEADERS))}{ws1.max_row}"
    _style_sheet(ws1, SHEET1_WIDTHS)

    # ---- Sheet2 agent上下线数据 ----
    ws2 = wb.create_sheet(SHEET2_NAME)
    ws2.append(SHEET2_HEADERS)
    for r in session_rows:
        ongoing = r["end_utc"] is None
        ws2.append([
            r["agent_name"],
            _shift(r["start_utc"], tz_offset_hours),
            "进行中..." if ongoing else _shift(r["end_utc"], tz_offset_hours),
            "🟢 在线中" if ongoing else "已下线",
        ])
    for row in range(2, ws2.max_row + 1):
        ws2.cell(row=row, column=2).number_format = DT_FORMAT
        if not isinstance(ws2.cell(row=row, column=3).value, str):
            ws2.cell(row=row, column=3).number_format = DT_FORMAT
    ws2.freeze_panes = "A2"
    _style_sheet(ws2, SHEET2_WIDTHS)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    return output_path


def main() -> int:
    """命令行自测：从库里取数生成表格到 output/。"""
    import argparse
    import sys
    from datetime import timezone

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import store  # noqa: E402

    parser = argparse.ArgumentParser(description="生成 RR 报表 XLSX")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 路径")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    a_rows = store.query_assignment_since(24, now_utc)
    s_rows = store.query_sessions_since(14, now_utc)
    build_report_xlsx(a_rows, s_rows, args.output)
    print(f"已生成: {args.output}（分配 {len(a_rows)} 条 / 会话 {len(s_rows)} 条）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
