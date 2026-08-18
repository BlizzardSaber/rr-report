#!/usr/bin/env bash
# ============================================================================
#  卸载脚本：移除 cron 任务，可选删除安装目录
# ============================================================================
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info()  { printf "${BLUE}[信息]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[成功]${NC} %s\n" "$*"; }

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "${RED}即将卸载 RR 数据推送工具${NC}"
echo "  安装目录: $INSTALL_DIR"
echo

# 1. 移除 cron 任务
info "移除定时任务 ..."
if crontab -l 2>/dev/null | grep -q "rr-report:cron"; then
    # grep 无匹配返回 1 会触发 pipefail，需 || true 兜底
    crontab -l 2>/dev/null | { grep -v "rr-report:cron" || true; } | crontab -
    ok "已移除全部相关 cron 任务（拉取 + 推送）"
else
    info "未发现相关 cron 任务（可能已移除）"
fi

# 2. 是否删除整个目录
echo
read -r -p "$(printf "${YELLOW}是否删除整个安装目录（含累积数据、配置和日志）？[y/N]: ${NC}")" YN
if [[ "$YN" =~ ^[Yy]$ ]]; then
    cd "$HOME"
    rm -rf "$INSTALL_DIR"
    ok "已删除 $INSTALL_DIR"
else
    info "保留目录。如需重新部署，可再次运行 install.sh。"
fi

echo
ok "卸载完成。"
