#!/usr/bin/env bash
# ============================================================================
#  RR 数据推送 — 一行命令部署脚本
#  用法：
#    curl -fsSL https://raw.githubusercontent.com/BlizzardSaber/rr-report/main/install.sh | bash
#  或本地直接：
#    ./install.sh
# ============================================================================
set -euo pipefail

# 检测管道模式：curl|bash 时 stdin 是管道而非 tty，
# 交互式 read 会失效。自动把脚本落地到临时文件并以 tty 重新执行。
if [ ! -t 0 ] && [ -z "${RRREPORT_RERUN:-}" ]; then
    TMP_SELF="$(mktemp /tmp/rr-report-install.XXXXXX.sh)"
    trap 'rm -f "$TMP_SELF"' EXIT
    cat > "$TMP_SELF"
    RRREPORT_RERUN=1 exec bash "$TMP_SELF" </dev/tty
fi

# ---------- 颜色 ----------
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info()  { printf "${BLUE}[信息]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[成功]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[警告]${NC} %s\n" "$*"; }
die()   { printf "${RED}[错误]${NC} %s\n" "$*"; exit 1; }

# GitHub 仓库地址（已发布，可通过环境变量覆盖）
REPO_URL="${RRREPORT_REPO_URL:-https://github.com/BlizzardSaber/rr-report.git}"
BRANCH="${RRREPORT_BRANCH:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 若脚本已在项目目录内（旁边有 manage.sh），就地安装；否则克隆到 ~/rr-report
if [[ -f "$SCRIPT_DIR/manage.sh" && -d "$SCRIPT_DIR/src" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
else
    INSTALL_DIR="${RRREPORT_INSTALL_DIR:-$HOME/rr-report}"
fi

# ---------- 1. 环境检测（缺失依赖自动安装） ----------
title() { printf "\n${GREEN}======== %s ========${NC}\n" "$*"; }

title "第 1 步：环境检测（缺失依赖自动安装）"
[[ "$(uname -s)" == "Darwin" || "$(uname -s)" == "Linux" ]] || die "仅支持 macOS / Linux。"

MISSING=()
command -v python3  >/dev/null 2>&1 || MISSING+=(python3)
command -v pip3     >/dev/null 2>&1 || MISSING+=(pip3)
command -v crontab  >/dev/null 2>&1 || MISSING+=(crontab)
if [[ "$INSTALL_DIR" != "$SCRIPT_DIR" ]]; then
    command -v git >/dev/null 2>&1 || MISSING+=(git)
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    info "检测到缺少依赖: ${MISSING[*]}，尝试自动安装 ..."
    if [ "$(uname -s)" == "Linux" ] && command -v apt-get >/dev/null 2>&1; then
        # Ubuntu / Debian：apt 自动安装（python3-venv 在 Debian 系是独立拆包，必须一起装）
        SUDO=""
        if [ "$(id -u)" -ne 0 ]; then
            command -v sudo >/dev/null 2>&1 \
                || die "缺少 ${MISSING[*]} 且无 sudo 权限。请手动执行: apt-get install -y python3 python3-pip python3-venv cron"
            SUDO="sudo"
        fi
        $SUDO apt-get update -y
        $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv cron
    elif [ "$(uname -s)" == "Darwin" ]; then
        command -v brew >/dev/null 2>&1 \
            || die "缺少 ${MISSING[*]}。macOS 请先安装 Homebrew（https://brew.sh）后执行: brew install python"
        brew install python   # crontab macOS 系统自带
    else
        die "当前系统缺少 ${MISSING[*]} 且无法自动安装，请手动安装后重试。"
    fi
fi

# 安装后复检（任一仍缺失则给出明确指引）
command -v python3 >/dev/null 2>&1 || die "python3 不可用。请手动安装 Python 3.8+ 后重试。"
command -v pip3   >/dev/null 2>&1 || die "pip3 不可用。请手动安装（如 apt-get install -y python3-pip）。"
if ! command -v crontab >/dev/null 2>&1; then
    warn "未检测到 crontab，定时任务将无法安装。（macOS 需在系统设置中授予「完全磁盘访问权限」）"
fi
PYVER=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
ok "python3 $PYVER / pip3 已就绪"

# ---------- 1.5 设置时区（让 cron 按北京时间触发） ----------
title "第 1.5 步：检查服务器时区"
# 仅 Linux 需要设置；macOS 跳过（其时区由「系统设置」管理，cron 语义也不同）
if [[ "$(uname -s)" == "Linux" ]]; then
    DESIRED_TZ="Asia/Shanghai"
    CURRENT_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    if [[ -z "$CURRENT_TZ" ]]; then
        CURRENT_TZ="$(cat /etc/timezone 2>/dev/null || true)"
    fi
    if [[ "$CURRENT_TZ" == "$DESIRED_TZ" ]]; then
        ok "时区已是 ${DESIRED_TZ}（北京时间），cron 将按本地时区触发。"
    else
        info "当前时区：${CURRENT_TZ:-未知}，设置为 $DESIRED_TZ ..."
        if command -v timedatectl >/dev/null 2>&1; then
            if timedatectl set-timezone "$DESIRED_TZ"; then
                ok "时区已设为 ${DESIRED_TZ}（$(date +%Z), $(date +%:z)）"
            else
                warn "timedatectl 设置失败（可能非 root）。cron 将以服务器当前时区为准。"
            fi
        else
            # 老系统兜底：用 /etc/localtime 软链
            if ln -sf "/usr/share/zoneinfo/$DESIRED_TZ" /etc/localtime 2>/dev/null \
               && echo "$DESIRED_TZ" > /etc/timezone 2>/dev/null; then
                ok "时区已设为 ${DESIRED_TZ}（$(date +%Z)）"
            else
                warn "无法自动设置时区。cron 将以服务器当前时区为准。"
            fi
        fi
    fi
else
    info "非 Linux 系统，跳过时区设置（请自行确保 cron 按预期时区运行）。"
fi

# ---------- 2. 拉取 / 更新代码 ----------
title "第 2 步：准备代码目录 $INSTALL_DIR"
if [[ "$INSTALL_DIR" == "$SCRIPT_DIR" ]]; then
    ok "已在项目目录内，就地安装。"
elif [[ -d "$INSTALL_DIR/.git" ]]; then
    info "目录已存在，执行 git pull 更新..."
    git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" >/dev/null
    ok "代码已更新到最新"
else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    info "git clone $REPO_URL ..."
    git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR" \
        || die "克隆失败。请确认仓库地址，或手动把项目目录传到服务器后在其内部执行 ./install.sh"
    ok "代码克隆完成"
fi
# 确保 .sh 脚本有可执行权限（git clone 可能丢失执行位）
chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true
cd "$INSTALL_DIR"

# ---------- 3. 虚拟环境 + 依赖 ----------
title "第 3 步：创建虚拟环境并安装依赖"
VENV_DIR="$INSTALL_DIR/.venv"
PY="$VENV_DIR/bin/python"

info "创建虚拟环境 ..."
if ! python3 -m venv --clear "$VENV_DIR"; then
    die "创建虚拟环境失败。请检查 python3 是否完整（可能缺少 ensurepip）。"
fi

# Python 3.12+ 的 venv 可能不带 pip，需要引导
if [ ! -x "$VENV_DIR/bin/pip" ]; then
    info "venv 缺少 pip，正在引导 ..."
    "$PY" -m ensurepip --upgrade || die "ensurepip 失败，请手动执行: python3 -m pip install --user virtualenv"
fi

# 关闭 set -e，因为 pip 升级自身时退出码可能非零但实际成功
set +e
info "升级 pip ..."
"$PY" -m pip install --upgrade pip >/dev/null 2>&1
info "安装 openpyxl（HTTP 拉取用标准库，无其他依赖）..."
"$PY" -m pip install openpyxl
INSTALL_RC=$?
set -e

if [ "$INSTALL_RC" -ne 0 ]; then
    die "依赖安装失败（退出码 ${INSTALL_RC}）。可手动重试: $PY -m pip install openpyxl"
fi

"$PY" -c "import openpyxl" 2>/dev/null || die "依赖导入失败，请检查虚拟环境: $VENV_DIR"
ok "依赖安装完成（隔离在 ${VENV_DIR}，不污染系统）"

# ---------- 4. 交互式生成配置 ----------
title "第 4 步：配置（密钥仅写入本地 config.json，不上传）"
CONFIG_FILE="$INSTALL_DIR/config/config.json"

prompt() { # prompt "提示" "默认值" -> 读到 $REPLY
    local msg="$1" def="${2-}"
    if [[ -n "$def" ]]; then
        printf "${YELLOW}%s${NC} [回车保留默认: %s]: " "$msg" "$def"
    else
        printf "${YELLOW}%s${NC}: " "$msg"
    fi
    read -r REPLY
    REPLY="${REPLY:-$def}"
}
prompt_secret() { # 隐藏输入
    local msg="$1" def="${2-}"
    printf "${YELLOW}%s${NC}: " "$msg"
    read -rs REPLY; echo
    REPLY="${REPLY:-$def}"
}

echo
echo "${BLUE}--- RR 接口 ---${NC}"
echo "  地址形如: https://podX.roundrobin-assignment.com/call/<编号>（含 /call/<编号>）"
read -r -p "$(printf "${YELLOW}RR 接口基础地址: ${NC}")" RR_BASE
[[ -z "$RR_BASE" ]] && die "接口地址不能为空。"
prompt_secret "RR API 密钥（URL 里 p1= 后面那串 UUID）"; RR_KEY="$REPLY"
[[ -z "$RR_KEY" ]] && die "API 密钥不能为空。"

echo
echo "${BLUE}--- 发件邮箱（SMTP）---${NC}"
echo "常见 SMTP 配置："
echo "  QQ邮箱:       smtp.qq.com:465  (SSL)   授权码在 设置→账户→SMTP 开启"
echo "  163邮箱:      smtp.163.com:465 (SSL)   授权码在 设置→POP3/SMTP"
echo "  Gmail:        smtp.gmail.com:465(SSL)/587(TLS)  需用「应用专用密码」"
echo "  企业微信邮箱: smtp.exmail.qq.com:465 (SSL)"
echo "  Outlook:      smtp.office365.com:587 (STARTTLS)"
prompt "SMTP 服务器地址" "smtp.qq.com"; SMTP_HOST="$REPLY"
prompt "SMTP 端口 (465=SSL / 587=STARTTLS)" "465"; SMTP_PORT="$REPLY"
if [[ "$SMTP_PORT" == "465" ]]; then USE_SSL="true"; else USE_SSL="false"; fi
prompt "发件邮箱账号 (用户名)" ""; SMTP_USER="$REPLY"
prompt_secret "发件邮箱密码/授权码"; SMTP_PASS="$REPLY"
prompt "发件人显示名称" "RR数据机器人"; SMTP_NAME="$REPLY"

echo
echo "${BLUE}--- 收件人（可填多个，逗号分隔）---${NC}"
prompt "收件人邮箱（多个用逗号分隔）" ""; RECIPIENTS_RAW="$REPLY"

echo
echo "${BLUE}--- 定时设置 ---${NC}"
echo "  提示：分配接口只保留最近 24 小时数据，拉取间隔建议不超过 60 分钟。"
prompt "数据拉取间隔（分钟）" "30"; SCHED_PULL="$REPLY"
echo "  推送支持每天多个时间点，用逗号分隔，例如: 09:00,21:00"
prompt "报表推送时间（HH:MM）" "09:00"; PUSH_TIMES_RAW="$REPLY"

# ---------- 5. 写 config.json ----------
info "写入配置文件 $CONFIG_FILE ..."
mkdir -p "$(dirname "$CONFIG_FILE")"

# 用 python 处理切分和 JSON 序列化，避免 shell 转义地狱
"$PY" - "$CONFIG_FILE" "$RR_BASE" "$RR_KEY" \
    "$SMTP_HOST" "$SMTP_PORT" "$USE_SSL" "$SMTP_USER" "$SMTP_PASS" "$SMTP_NAME" \
    "$RECIPIENTS_RAW" "$SCHED_PULL" "$PUSH_TIMES_RAW" <<'PYEOF'
import json, re, sys, os
(cfg, rr_base, rr_key, host, port, ssl_, user, pwd, name,
 recips, pull_min, push_raw) = sys.argv[1:]
recipients = [r.strip() for r in re.split(r"[,;\s]+", recips) if r.strip()]
push_times = []
for t in re.split(r"[,;\s]+", push_raw.strip()):
    if not t: continue
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", t)
    if not m: raise SystemExit(f"推送时间格式不正确: {t}（应为 HH:MM）")
    push_times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
if not push_times: raise SystemExit("至少需要一个推送时间")
data = {
    "rr": {
        "base_url": rr_base.rstrip("/"),
        "api_key": rr_key,
        "assignment_proc": "___get_assignment_log",
        "availability_proc": "___get_agent_availability",
        "tz_offset_hours": 8,
    },
    "smtp": {
        "host": host, "port": int(port), "use_ssl": ssl_ == "true",
        "username": user, "password": pwd, "from_name": name,
    },
    "recipients": recipients,
    "schedule": {
        "pull_interval_minutes": int(pull_min),
        "push_times": push_times,
    },
    "report": {"assignment_window_hours": 24, "availability_days": 14},
}
os.makedirs(os.path.dirname(cfg), exist_ok=True)
with open(cfg, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2); f.write("\n")
os.chmod(cfg, 0o600)
print("收件人:", ", ".join(recipients) or "(空)")
print("拉取间隔: 每", pull_min, "分钟；推送时间: 每天", ", ".join(push_times))
PYEOF
ok "配置已保存（权限 600）"

# ---------- 6. 写入 crontab ----------
title "第 5 步：注册定时任务（cron）"
LOG_FILE="$INSTALL_DIR/logs/cron.log"
CRON_LINES=$("$PY" - "$CONFIG_FILE" <<PYEOF
import sys
sys.path.insert(0, "$INSTALL_DIR/src")
import config_utils as cu
cfg = cu.load_config("$CONFIG_FILE")
for line in cu.cron_lines(cfg, "$PY", "$INSTALL_DIR/src", "$LOG_FILE"):
    print(line)
PYEOF
)

# 清掉旧的（如果重装），再写入新行
# 注意：crontab 为空时 crontab -l 失败 / grep 无匹配返回 1，需 || true 兜底，
# 否则 set -o pipefail 会把整个安装脚本中断
( crontab -l 2>/dev/null | { grep -v "rr-report:cron" || true; }; echo "$CRON_LINES" ) | crontab -
ok "已注册 cron 任务："
echo "$CRON_LINES" | sed 's/^/    /'

# ---------- 7. 可选立即测试 ----------
title "第 6 步：完成"
cat <<EOF
${GREEN}部署成功！${NC}

  安装目录 : $INSTALL_DIR
  配置文件 : ${CONFIG_FILE}（含密钥，勿外传）
  虚拟环境 : $VENV_DIR
  运行日志 : $LOG_FILE 及 logs/ 目录

后续管理：
  修改 RR 密钥 / 发件箱 / 收件人 / 拉取与推送时间 → ${YELLOW}cd $INSTALL_DIR && ./manage.sh${NC}
  立即拉取一次数据                           → ${YELLOW}./manage.sh${NC} 选 8
  立即发送一封测试邮件                       → ${YELLOW}./manage.sh${NC} 选 9
  卸载                                       → ${YELLOW}./uninstall.sh${NC}
EOF

read -r -p "$(printf "${YELLOW}是否立即拉取一次数据验证接口配置？[Y/n]: ${NC}")" YN
if [[ ! "$YN" =~ ^[Nn]$ ]]; then
    info "正在拉取 ..."
    if "$PY" "$INSTALL_DIR/src/run_pull.py"; then
        ok "拉取成功，数据已入库。"
        read -r -p "$(printf "${YELLOW}是否立即发送一封测试邮件验证 SMTP 配置？[y/N]: ${NC}")" YN2
        if [[ "$YN2" =~ ^[Yy]$ ]]; then
            if "$PY" "$INSTALL_DIR/src/run_push.py" --test; then
                ok "测试邮件已发送，请查收（含【测试】标记）。"
            else
                warn "测试发送失败，请用 ./manage.sh 检查 SMTP 配置。"
            fi
        fi
    else
        warn "拉取失败，请检查 RR 接口地址与密钥（manage.sh 选 5 修改）。"
    fi
fi
echo
ok "全部完成。"
