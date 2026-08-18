#!/usr/bin/env bash
# ============================================================================
#  交互式管理菜单：RR 密钥 / 发件箱 / 收件人增删 / 拉取间隔 / 推送时间 / 立即测试
#  所有修改都落到 config/config.json，下次 cron 自动生效，无需重启。
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"   # 兜底：用系统 python（需已装依赖）
CONFIG="$SCRIPT_DIR/config/config.json"
SRC_DIR="$SCRIPT_DIR/src"

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info()  { printf "${BLUE}[信息]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[成功]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[警告]${NC} %s\n" "$*"; }
die()   { printf "${RED}[错误]${NC} %s\n" "$*"; exit 1; }

[[ -f "$CONFIG" ]] || die "未找到配置文件 ${CONFIG}，请先运行 ./install.sh"

# 用 python 操作 config.json（保证 JSON 正确 + 校验邮箱/时间格式）
py_json() { # 任意 python 代码段，config 路径作为第一个参数
    "$VENV_PY" - "$CONFIG" <<PYEOF
import sys
sys.path.insert(0, '$SRC_DIR')
import config_utils
cfg_path=sys.argv[1]
cfg=config_utils.load_config(cfg_path)
$1
PYEOF
}

pause() { echo; read -r -p "$(printf "${YELLOW}按回车返回菜单...${NC}")" _; }

# ---------- 菜单项 ----------
menu_view() {
    printf "%s\n" "${BLUE}--- 当前配置 ---${NC}"
    py_json '
def mask(s):
    return s[:6]+"****"+s[-4:] if s and len(s)>14 else ("****" if s else "(未配置)")
print("RR 接口地址 :", cfg["rr"]["base_url"])
print("RR API 密钥 :", mask(cfg["rr"]["api_key"]))
print("时区偏移    : UTC+", cfg["rr"]["tz_offset_hours"])
print("SMTP 主机   :", cfg["smtp"]["host"], ":", cfg["smtp"]["port"],
      "(SSL)" if cfg["smtp"]["use_ssl"] else "(STARTTLS)")
print("发件账号    :", cfg["smtp"]["username"])
print("发件人名称  :", cfg["smtp"]["from_name"])
print("收件人列表  :", ", ".join(cfg["recipients"]) if cfg["recipients"] else "(空)")
s=cfg["schedule"]
print("数据拉取    : 每", s["pull_interval_minutes"], "分钟一次")
print("报表推送    : 每天", ", ".join(s["push_times"]), "(服务器本地时间)")
r=cfg["report"]
print("报表窗口    : 分配近", r["assignment_window_hours"], "小时 / 上下线近", r["availability_days"], "天")
errs=config_utils.validate_config(cfg)
if errs:
    print()
    print("[配置问题]")
    for e in errs: print("  -", e)
'
    pause
}

menu_add_recipient() {
    read -r -p "$(printf "${YELLOW}输入要新增的收件人邮箱（多个用逗号分隔）: ${NC}")" RAW
    [[ -z "$RAW" ]] && { warn "未输入，已取消。"; pause; return; }
    py_json "
added=config_utils.parse_recipients_input('''$RAW''')
existing=set(cfg['recipients'])
new=[e for e in added if e not in existing]
cfg['recipients'].extend(new)
config_utils.save_config(cfg,cfg_path)
invalid=[e for e in added if not config_utils.is_valid_email(e)]
print('已新增:', ', '.join(new) if new else '(无，均已存在)')
if invalid: print('以下格式不正确，请核对:', ', '.join(invalid))
"
    pause
}

menu_del_recipient() {
    py_json '
recs=cfg["recipients"]
if not recs:
    print("收件人列表为空，无可删除项。")
else:
    for i,e in enumerate(recs,1): print(f"  {i}) {e}")
'
    read -r -p "$(printf "${YELLOW}输入要删除的序号或邮箱（多个用逗号分隔）: ${NC}")" RAW
    [[ -z "$RAW" ]] && { warn "未输入，已取消。"; pause; return; }
    py_json "
import re
inputs=[x.strip() for x in re.split(r'[,;\s]+','''$RAW''') if x.strip()]
recs=cfg['recipients']
to_del=set()
for x in inputs:
    if x.isdigit() and 1<=int(x)<=len(recs):
        to_del.add(recs[int(x)-1])
    elif x in recs:
        to_del.add(x)
cfg['recipients']=[r for r in recs if r not in to_del]
config_utils.save_config(cfg,cfg_path)
print('已删除:', ', '.join(sorted(to_del)) if to_del else '(无匹配)')
print('当前收件人:', ', '.join(cfg['recipients']) if cfg['recipients'] else '(空)')
"
    pause
}

menu_edit_smtp() {
    echo "${BLUE}修改发件邮箱（回车保留当前值）${NC}"
    cur_host=$(py_json 'print(cfg["smtp"]["host"],end="")')
    cur_port=$(py_json 'print(cfg["smtp"]["port"],end="")')
    cur_user=$(py_json 'print(cfg["smtp"]["username"],end="")')
    cur_name=$(py_json 'print(cfg["smtp"]["from_name"],end="")')

    read -r -p "SMTP 主机 [$cur_host]: " v; HOST="${v:-$cur_host}"
    read -r -p "端口 [$cur_port] (465=SSL/587=STARTTLS): " v; PORT="${v:-$cur_port}"
    if [[ "$PORT" == "465" ]]; then USE_SSL="true"; else USE_SSL="false"; fi
    read -r -p "发件账号 [$cur_user]: " v; USER="${v:-$cur_user}"
    read -r -p "发件人显示名称 [$cur_name]: " v; NAME="${v:-$cur_name}"
    read -rs -p "密码/授权码 (回车保留原值): " v; echo
    if [[ -z "$v" ]]; then
        # 不改密码：单独 patch，避免覆盖
        py_json "
cfg['smtp']['host']='''$HOST'''
cfg['smtp']['port']=int('$PORT')
cfg['smtp']['use_ssl']=$USE_SSL=='true'
cfg['smtp']['username']='''$USER'''
cfg['smtp']['from_name']='''$NAME'''
config_utils.save_config(cfg,cfg_path)
print('已更新（密码保留原值）')
"
    else
        py_json "
cfg['smtp']['host']='''$HOST'''
cfg['smtp']['port']=int('$PORT')
cfg['smtp']['use_ssl']=$USE_SSL=='true'
cfg['smtp']['username']='''$USER'''
cfg['smtp']['password']='''$v'''
cfg['smtp']['from_name']='''$NAME'''
config_utils.save_config(cfg,cfg_path)
print('已更新')
"
    fi
    pause
}

menu_edit_rr() {
    echo "${BLUE}修改 RR 接口配置（回车保留当前值）${NC}"
    cur_base=$(py_json 'print(cfg["rr"]["base_url"],end="")')
    read -r -p "接口基础地址 [$cur_base]: " v; BASE="${v:-$cur_base}"
    read -rs -p "API 密钥 p1 (回车保留原值): " v; echo
    if [[ -z "$v" ]]; then
        py_json "
cfg['rr']['base_url']='''$BASE'''
config_utils.save_config(cfg,cfg_path)
print('已更新（密钥保留原值）')
"
    else
        py_json "
cfg['rr']['base_url']='''$BASE'''
cfg['rr']['api_key']='''$v'''
config_utils.save_config(cfg,cfg_path)
print('已更新')
"
    fi
    pause
}

menu_edit_pull_interval() {
    echo "${BLUE}修改数据拉取间隔（回车保留当前值）${NC}"
    echo "  提示：分配接口只保留最近 24 小时，建议不超过 60 分钟，否则中间数据会丢失。"
    cur_n=$(py_json 'print(cfg["schedule"]["pull_interval_minutes"],end="")')
    while true; do
        read -r -p "拉取间隔（分钟 1-1440，整小时按整点触发）[$cur_n]: " v
        N="${v:-$cur_n}"
        if [[ "$N" =~ ^[0-9]+$ ]] && [ "$N" -ge 1 ] && [ "$N" -le 1440 ]; then break; fi
        warn "请输入 1-1440 的整数。"
    done
    py_json "
cfg['schedule']['pull_interval_minutes']=int('$N')
config_utils.save_config(cfg,cfg_path)
print('已更新拉取间隔: 每 $N 分钟')
"
    update_cron
    pause
}

menu_edit_push_times() {
    echo "${BLUE}修改报表推送时间（回车保留当前值）${NC}"
    cur_t=$(py_json 'print(", ".join(cfg["schedule"]["push_times"]),end="")')
    echo "  支持每天多个时间点，用逗号分隔，例如: 09:00,21:00（服务器本地时间）"
    read -r -p "推送时间 [$cur_t]: " v
    RAW="${v:-$cur_t}"
    if py_json "
times=config_utils.parse_push_times_input('''$RAW''')
if not times: raise SystemExit('至少需要一个时间点')
cfg['schedule']['push_times']=times
config_utils.save_config(cfg,cfg_path)
print('已更新推送时间: 每天', ', '.join(times))
"; then
        update_cron
    else
        warn "时间格式有误，未保存。"
    fi
    pause
}

update_cron() {
    info "同步更新 cron 任务 ..."
    local CRON_LINES
    # 用 python 生成 cron 行（避免 shell 拼接出错）
    CRON_LINES=$("$VENV_PY" - "$CONFIG" <<PYEOF
import sys
sys.path.insert(0, "$SRC_DIR")
import config_utils as cu
cfg=cu.load_config("$CONFIG")
for line in cu.cron_lines(cfg, "$VENV_PY", "$SRC_DIR", "$SCRIPT_DIR/logs/cron.log"):
    print(line)
PYEOF
)
    # crontab 为空时 crontab -l 失败 / grep 无匹配返回 1，需 || true 兜底（set -o pipefail）
    ( crontab -l 2>/dev/null | { grep -v "rr-report:cron" || true; }; echo "$CRON_LINES" ) | crontab -
    ok "cron 已更新："
    echo "$CRON_LINES" | sed 's/^/    /'
}

menu_pull_now() {
    info "立即拉取一次数据 ..."
    "$VENV_PY" "$SRC_DIR/run_pull.py" && ok "拉取完成。" || warn "拉取失败，请查看上方日志。"
    pause
}

menu_push_now() {
    info "立即生成并发送一次报表（测试邮件，标记【测试】）..."
    "$VENV_PY" "$SRC_DIR/run_push.py" --test && ok "测试完成，请查收邮件。" || warn "测试失败，请查看上方日志。"
    pause
}

menu_status() {
    "$VENV_PY" "$SRC_DIR/show_status.py" || true
    pause
}

menu_view_cron() {
    echo "${BLUE}--- 当前 cron 任务（含本工具）---${NC}"
    if crontab -l 2>/dev/null; then
        :
    else
        warn "无 crontab"
    fi
    pause
}

menu_uninstall() {
    bash "$SCRIPT_DIR/uninstall.sh"
    exit 0
}

# ---------- 主循环 ----------
while true; do
    echo
    printf "${GREEN}====== RR 数据推送管理菜单 ======${NC}\n"
    cat <<'EOF'
  1) 查看当前配置
  2) 新增收件人邮箱
  3) 删除收件人邮箱
  4) 修改发件邮箱（SMTP）
  5) 修改 RR 接口配置（地址/密钥）
  6) 修改数据拉取间隔
  7) 修改报表推送时间
  8) 立即拉取一次数据
  9) 立即生成并发送一次报表（测试）
 10) 查看数据统计与最近日志
 11) 查看 cron 任务
 12) 卸载
  0) 退出
EOF
    read -r -p "$(printf "${YELLOW}请选择 [0-12]: ${NC}")" CHOICE
    case "$CHOICE" in
        1)  menu_view ;;
        2)  menu_add_recipient ;;
        3)  menu_del_recipient ;;
        4)  menu_edit_smtp ;;
        5)  menu_edit_rr ;;
        6)  menu_edit_pull_interval ;;
        7)  menu_edit_push_times ;;
        8)  menu_pull_now ;;
        9)  menu_push_now ;;
        10) menu_status ;;
        11) menu_view_cron ;;
        12) menu_uninstall ;;
        0)  echo "再见。"; exit 0 ;;
        *)  warn "无效选择" ;;
    esac
done
