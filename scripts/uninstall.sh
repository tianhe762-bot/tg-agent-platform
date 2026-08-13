#!/usr/bin/env bash
# ============================================================
# TG-Agent 卸载脚本
#
# 用法:
#   sudo bash scripts/uninstall.sh               # 交互式选择
#   sudo bash scripts/uninstall.sh --keep-data   # 保留配置与数据
#   sudo bash scripts/uninstall.sh --full        # 完全卸载（删除全部，不可恢复）
# ============================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

# 防止删除当前工作目录导致 getcwd 错误
cd /

APP_DIR="/opt/tg-agent"

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ 请使用 root 权限运行: sudo bash scripts/uninstall.sh"
    exit 1
fi

MODE=""
case "${1:-}" in
    --keep-data) MODE="keep" ;;
    --full) MODE="full" ;;
    "")
        echo "=========================================="
        echo "       TG-Agent 卸载程序"
        echo "=========================================="
        echo
        echo "1) 保留数据卸载 —— 移除程序与服务，保留配置与数据（可重装）"
        echo "2) 完全卸载 —— 删除程序、配置、数据与用户（不可恢复）"
        echo
        read -rp "请选择 [1/2]: " CHOICE
        case "$CHOICE" in
            1) MODE="keep" ;;
            2) MODE="full" ;;
            *)
                echo "❌ 无效选择，卸载已取消"
                exit 1
                ;;
        esac
        ;;
    *)
        echo "❌ 未知参数: $1（支持 --keep-data / --full）"
        exit 1
        ;;
esac

if [ "$MODE" = "full" ]; then
    read -rp "⚠️ 完全卸载将删除 $APP_DIR 全部数据，输入 yes 确认: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "已取消。"
        exit 0
    fi
fi

echo
echo "[1/3] 停止并禁用服务..."
systemctl stop tg-agent.service 2>/dev/null || true
systemctl disable tg-agent.service 2>/dev/null || true

echo "[2/3] 清理 systemd 单元与 sudo 白名单..."
rm -f /etc/systemd/system/tg-agent.service
rm -f /etc/sudoers.d/tg-agent
systemctl daemon-reload 2>/dev/null || true

if [ "$MODE" = "full" ]; then
    echo "[3/3] 删除程序目录与系统用户..."
    if [ -d "$APP_DIR" ]; then
        rm -rf "$APP_DIR"
    fi
    if id tgagent >/dev/null 2>&1; then
        userdel tgagent 2>/dev/null || true
    fi
    echo
    echo "✅ 完全卸载完成（程序/配置/数据/用户均已删除）"
else
    echo "[3/3] 保留数据..."
    echo
    echo "✅ 已卸载程序与服务；$APP_DIR 下的配置与数据已保留"
    echo "   如需重装: curl -fsSL https://raw.githubusercontent.com/tianhe762-bot/tg-agent-platform/main/install.sh | sudo bash"
fi

echo "=========================================="
echo "       TG-Agent 卸载结束"
echo "=========================================="
