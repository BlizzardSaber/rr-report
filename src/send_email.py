"""通过 SMTP 发送带 XLSX 附件的邮件。

支持两种连接：
- SSL（端口 465）：smtplib.SMTP_SSL
- STARTTLS（端口 587）：smtplib.SMTP + starttls()

由 config.smtp.use_ssl 决定。
"""

from __future__ import annotations

import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

SMTP_TIMEOUT = 30
MAX_RETRIES = 2  # 失败后最多重试次数

log = logging.getLogger("rr.email")


def _build_message(
    smtp_cfg: dict,
    recipients: list[str],
    subject: str,
    body: str,
    attachment_path: str | None = None,
) -> MIMEMultipart:
    """构造邮件对象。"""
    from_addr = smtp_cfg.get("username", "")
    from_name = smtp_cfg.get("from_name", "RR数据机器人")
    msg = MIMEMultipart()
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    msg.attach(MIMEText(body, "plain", "utf-8"))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
        part["Content-Disposition"] = (
            f'attachment; filename="{os.path.basename(attachment_path)}"'
        )
        msg.attach(part)

    return msg


def send_email(
    smtp_cfg: dict,
    recipients: list[str],
    subject: str,
    body: str,
    attachment_path: str | None = None,
    retries: int = MAX_RETRIES,
) -> None:
    """发送邮件，失败按指数退避重试。

    smtp_cfg: config.smtp 子字典。
    抛出异常表示最终失败。
    """
    host = smtp_cfg["host"]
    port = int(smtp_cfg.get("port", 465))
    use_ssl = smtp_cfg.get("use_ssl", True)
    username = smtp_cfg["username"]
    password = smtp_cfg["password"]

    if not recipients:
        raise ValueError("收件人列表为空")

    msg = _build_message(smtp_cfg, recipients, subject, body, attachment_path)

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT)
            else:
                server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except smtplib.SMTPException:
                    pass  # 服务器不支持 STARTTLS，继续明文（已尝试）
            try:
                server.login(username, password)
                server.sendmail(username, recipients, msg.as_string())
                log.info("邮件发送成功 → %s (第 %d 次尝试)", recipients, attempt + 1)
                return
            finally:
                server.quit()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次发送失败: %s", attempt + 1, e)
            if attempt < retries:
                time.sleep(3 * (attempt + 1))  # 退避 3s, 6s

    raise RuntimeError(f"邮件发送失败（重试 {retries} 次仍出错）: {last_err}")


def build_default_body(stats: dict, report_time: str,
                       is_test: bool = False) -> tuple[str, str]:
    """生成默认的邮件主题和正文。

    stats: 需包含 assignment_count / ticket_count / agent_count /
           session_count / online_agents / since_utc 等字段。
    返回 (subject, body)。
    """
    prefix = "【测试】" if is_test else ""
    subject = f"{prefix}RR数据报表 ({report_time})"
    body = "\n".join([
        "您好，",
        "",
        "这是 RR 客服分配系统的定时数据报表，详见附件 XLSX 表格：",
        f"  · 报表时间        : {report_time} (UTC+8)",
        f"  · 分配记录        : {stats['assignment_count']} 条 [{stats.get('assignment_label', '')}]"
        f"（涉及工单 {stats['ticket_count']} 个 / 客服 {stats['agent_count']} 人）",
        f"  · 客服上下线记录  : {stats['session_count']} 条"
        f"（当前在线 {stats['online_agents']} 人）",
        f"  · 数据累计范围    : 自 {stats['since_local']} 起（本地持续累积，不丢失）",
        "",
        "表格包含三个工作表：分配数据（按班次/客服分组，专员间空行分隔，含每日每人汇总）"
        " / agent上下线数据（含在线时长）/ 按客服汇总。",
        "",
        "—— RR数据机器人（自动发送，请勿直接回复）",
    ])
    if is_test:
        body = "【这是一封测试邮件，用于验证邮件配置是否正确】\n\n" + body
    return subject, body
