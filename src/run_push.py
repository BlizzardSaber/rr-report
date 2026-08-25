"""定时推送入口：取最新数据 → 生成《RR API数据》样式 XLSX → 邮件推送。

cron 按 schedule.push_times 逐个时间点调用本脚本。

用法：
    python run_push.py             # 正式推送（cron / 手动）
    python run_push.py --test      # 发送测试邮件，主题带【测试】标记
    python run_push.py --dry-run   # 只生成表格不发邮件（本地调试用）
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_xlsx  # noqa: E402
import config_utils  # noqa: E402
import send_email  # noqa: E402
import store  # noqa: E402
from common import SingleInstance, setup_logging, utcnow  # noqa: E402
from pipeline import pull_and_store  # noqa: E402

OUTPUT_DIR = os.path.join(config_utils.PROJECT_ROOT, "output")


def run(test: bool = False, dry_run: bool = False) -> int:
    log_file = setup_logging("push")
    log = logging.getLogger("rr.push")
    mode = "（测试模式）" if test else ("（dry-run，不发邮件）" if dry_run else "定时推送")
    log.info("=== 推送任务开始 %s ===", mode)

    # 1. 配置检查（dry-run 只需要 RR 部分，不强制 SMTP/收件人）
    try:
        config = config_utils.load_config()
    except RuntimeError as e:
        log.error("%s", e)
        return 1
    if not config["rr"].get("api_key"):
        log.error("rr.api_key 未配置，请运行 manage.sh 完善。")
        return 1
    if not dry_run:
        errors = config_utils.validate_config(config)
        if errors:
            for e in errors:
                log.error("配置错误: %s", e)
            log.error("请运行 manage.sh 修正配置后重试。")
            return 1

    tz_offset = int(config["rr"].get("tz_offset_hours", 8))
    window_hours = int(config["report"].get("assignment_window_hours", 24))
    avail_days = int(config["report"].get("availability_days", 14))
    now_utc = utcnow()
    report_dt = now_utc + timedelta(hours=tz_offset)

    # 2. 先取一次最新数据（即使拉取 cron 刚跑过，也保证报表是最新的）
    try:
        with SingleInstance("push"):
            pull_and_store(config, now_utc, log)
    except RuntimeError as e:
        log.warning("%s", e)
        return 0
    except Exception:
        log.exception("拉取最新数据失败，改用本地累积数据继续生成报表")
        # 本地库里有历史累积，报表仍可生成，不直接失败

    # 3. 从本地库取报表数据（0=全部累积；-1=本月自然月，按报表时区当月1日起）
    month_start_utc = None
    if window_hours == -1 or avail_days == -1:
        month_start_utc = (report_dt.replace(day=1, hour=0, minute=0, second=0,
                                             microsecond=0)
                           - timedelta(hours=tz_offset))
    a_rows = store.query_assignment_since(
        window_hours, now_utc, since=month_start_utc if window_hours == -1 else None)
    s_rows = store.query_sessions_since(
        avail_days, now_utc, since=month_start_utc if avail_days == -1 else None)
    window_label = {0: "全部累积", -1: "本月"}.get(
        window_hours, f"近 {window_hours} 小时")
    avail_label = {0: "全部累积", -1: "本月"}.get(
        avail_days, f"近 {avail_days} 天")
    log.info("报表数据：分配 %d 条（%s），会话 %d 条（%s）。",
             len(a_rows), window_label, len(s_rows), avail_label)

    # 4. 生成 XLSX
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = report_dt.strftime("%Y-%m-%d_%H-%M")
    xlsx_name = f"RR数据_{stamp}.xlsx"
    xlsx_path = os.path.join(OUTPUT_DIR, xlsx_name)
    try:
        build_xlsx.build_report_xlsx(a_rows, s_rows, xlsx_path,
                                     tz_offset_hours=tz_offset,
                                     report_cfg=config["report"],
                                     now_utc=now_utc)
    except Exception:
        log.exception("生成 XLSX 失败")
        return 4
    log.info("XLSX 已生成: %s", xlsx_path)

    # 5. dry-run 到此为止
    if dry_run:
        log.info("dry-run 完成，未发送邮件。文件: %s", xlsx_path)
        return 0

    # 6. 组装统计 + 发邮件
    db_stats = store.stats(now_utc)
    if month_start_utc is not None:
        since_local = (month_start_utc + timedelta(hours=tz_offset)).strftime(
            "%Y-%m-%d %H:%M")
    else:
        since_local = db_stats["first_event_utc"] or "—"
        if since_local != "—":
            since_local = (datetime.strptime(since_local, "%Y-%m-%d %H:%M:%S")
                           + timedelta(hours=tz_offset)).strftime("%Y-%m-%d %H:%M")
    email_stats = {
        "assignment_label": window_label,
        "assignment_count": len(a_rows),
        "ticket_count": len({r["ticket_id"] for r in a_rows}),
        "agent_count": len({r["agent_name"] for r in a_rows}),
        "session_count": len(s_rows),
        "online_agents": db_stats["online_agents"],
        "since_local": since_local,
    }

    subject, body = send_email.build_default_body(
        email_stats, report_dt.strftime("%Y-%m-%d %H:%M"), is_test=test)
    recipients = config["recipients"]
    log.info("发送邮件 → %s", recipients)
    try:
        send_email.send_email(config["smtp"], recipients, subject, body,
                              attachment_path=xlsx_path)
    except Exception:
        log.exception("邮件发送失败")
        return 5

    log.info("=== 推送完成（日志: %s）===", log_file)
    return 0


def main() -> int:
    return run(test="--test" in sys.argv, dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
