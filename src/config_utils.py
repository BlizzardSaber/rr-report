"""配置文件读写与校验工具。

所有脚本（拉取、报表、发邮件、cron 入口、管理菜单）统一通过本模块访问
config/config.json，避免配置散落各处、口径不一致。

config.json 结构见 config/config.example.json。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# 项目根目录：本文件位于 <root>/src/config_utils.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.json")
EXAMPLE_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.example.json")

# 默认配置（首次部署或缺失字段时回填）
DEFAULTS: dict[str, Any] = {
    "rr": {
        "base_url": "",
        "api_key": "",
        "assignment_proc": "___get_assignment_log",
        "availability_proc": "___get_agent_availability",
        "tz_offset_hours": 8,
    },
    "smtp": {
        "host": "",
        "port": 465,
        "use_ssl": True,
        "username": "",
        "password": "",
        "from_name": "RR数据机器人",
    },
    "recipients": [],
    "schedule": {
        "pull_interval_minutes": 30,
        "push_times": ["09:00"],
    },
    "report": {
        "assignment_window_hours": 0,  # 分配明细取数窗口（小时）；0 = 全部累积数据
        "availability_days": 14,
        "night_shift_agents": [],
        "shifts": {
            "day": ["08:30", "18:00"],
            "mid": [["14:00", "18:00"], ["20:00", "23:00"]],
        },
    },
}

# 邮箱格式校验
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# cron 行末尾的定位标记，卸载/更新时据此精确移除
CRON_MARKER = "rr-report:cron"


def is_valid_email(email: str) -> bool:
    """简单校验邮箱格式。"""
    return bool(_EMAIL_RE.match(email.strip()))


def _deep_merge(base: dict, override: dict) -> dict:
    """用 override 递归合并进 base（缺失字段用 base 的默认值补齐）。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str = CONFIG_PATH) -> dict[str, Any]:
    """读取配置，自动用默认值补齐缺失字段。

    如果配置文件不存在，返回纯默认值（调用方应判断是否已初始化）。
    """
    if not os.path.exists(path):
        return _deep_merge(DEFAULTS, {})
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"配置文件 {path} 解析失败: {e}")
    return _deep_merge(DEFAULTS, user_cfg)


def save_config(config: dict[str, Any], path: str = CONFIG_PATH) -> None:
    """写入配置（UTF-8、缩进 2、保留中文可读）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    # 仅本用户可读写，保护密钥
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_config_exists() -> bool:
    """若 config.json 不存在则从 example 拷贝一份，返回是否新建。"""
    if os.path.exists(CONFIG_PATH):
        return False
    if os.path.exists(EXAMPLE_CONFIG_PATH):
        with open(EXAMPLE_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = _deep_merge(DEFAULTS, {})
    save_config(data)
    return True


def validate_config(config: dict[str, Any]) -> list[str]:
    """校验配置完整性，返回错误信息列表（空列表表示通过）。"""
    errors: list[str] = []

    rr = config.get("rr", {})
    if not rr.get("api_key"):
        errors.append("rr.api_key（RR 接口密钥）未配置")
    if not rr.get("base_url"):
        errors.append("rr.base_url（RR 接口地址）未配置")

    smtp = config.get("smtp", {})
    if not smtp.get("host"):
        errors.append("smtp.host 未配置")
    if not smtp.get("username"):
        errors.append("smtp.username 未配置")
    if not smtp.get("password"):
        errors.append("smtp.password 未配置")

    recipients = config.get("recipients", [])
    if not isinstance(recipients, list) or not recipients:
        errors.append("recipients 收件人列表为空")
    else:
        for addr in recipients:
            if not is_valid_email(addr):
                errors.append(f"收件人邮箱格式不正确: {addr}")

    sch = config.get("schedule", {})
    n = sch.get("pull_interval_minutes")
    if not isinstance(n, int) or n < 1 or n > 1440:
        errors.append("schedule.pull_interval_minutes 应为 1-1440 的整数（分钟）")
    for t in sch.get("push_times", []):
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", str(t)):
            errors.append(f"schedule.push_times 时间格式不正确: {t}（应为 HH:MM）")
    if not sch.get("push_times"):
        errors.append("schedule.push_times 推送时间为空")

    return errors


def parse_recipients_input(raw: str) -> list[str]:
    """把用户输入的逗号/空格/分号分隔的收件人解析为列表（不去重，保留顺序）。"""
    parts = re.split(r"[,;\s]+", raw.strip())
    return [p.strip() for p in parts if p.strip()]


def parse_push_times_input(raw: str) -> list[str]:
    """把用户输入的逗号/空格分隔的 HH:MM 解析为合法列表，非法项抛 ValueError。"""
    times: list[str] = []
    for t in parse_recipients_input(raw):
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", t):
            raise ValueError(f"时间格式不正确: {t}（应为 HH:MM，例如 09:00）")
        hh, mm = t.split(":")
        norm = f"{int(hh):02d}:{mm}"
        if norm not in times:
            times.append(norm)
    return times


def pull_cron_expr(config: dict[str, Any]) -> str:
    """根据拉取间隔生成 cron 时间表达式。

    1-59 分钟 → */N；整小时（60/120/.../1440）→ 0 */h；其余取小时近似并向上取整。
    """
    n = int(config["schedule"]["pull_interval_minutes"])
    if 1 <= n < 60:
        return f"*/{n} * * * *"
    if n % 60 == 0 and 60 <= n <= 1440:
        return f"0 */{n // 60} * * *"
    hours = max(1, (n + 59) // 60)
    return f"0 */{hours} * * *"


def cron_lines(config: dict[str, Any], venv_python: str, src_dir: str,
               log_file: str) -> list[str]:
    """根据配置生成全部 crontab 行（拉取 1 行 + 每个推送时间 1 行）。"""
    lines = [f"{pull_cron_expr(config)} {venv_python} {src_dir}/run_pull.py "
             f">> \"{log_file}\" 2>&1  # {CRON_MARKER}:pull"]
    for t in config["schedule"]["push_times"]:
        hh, mm = t.split(":")
        lines.append(f"{int(mm)} {int(hh)} * * * {venv_python} {src_dir}/run_push.py "
                     f">> \"{log_file}\" 2>&1  # {CRON_MARKER}:push")
    return lines
