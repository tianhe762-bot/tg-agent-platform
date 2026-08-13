#!/usr/bin/env bash
# ============================================================
# TG-Agent 管理面板
# 用法: sudo tg-agent  （或 sudo bash /opt/tg-agent/scripts/panel.sh）
# ============================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

APP_DIR="/opt/tg-agent"
PY="$APP_DIR/venv/bin/python"
GITHUB_URL="https://github.com/tianhe762-bot/tg-agent-platform"

if [ ! -d "$APP_DIR" ]; then
    echo "❌ 未检测到 TG-Agent 安装（$APP_DIR 不存在），请先运行安装脚本"
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "⚠️ 面板需要 root 权限，请使用: sudo tg-agent"
    exit 1
fi

run_cli()
{
    (cd "$APP_DIR" && sudo -u tgagent "$PY" -m src.cli "$@" 2>&1)
}

while true; do
    clear 2>/dev/null || true
    VERSION="$(cat "$APP_DIR/VERSION" 2>/dev/null || echo '?')"
    echo "=============================================="
    echo "        TG-Agent 管理面板 v$VERSION"
    echo "=============================================="
    echo "🌐 GitHub: $GITHUB_URL"
    echo "----------------------------------------------"
    echo
    echo "  1) 查看运行状态"
    echo "  2) 查看实时日志"
    echo "  3) 配置热重载"
    echo "  4) 温和重置"
    echo "  5) 强杀子进程"
    echo "  6) 触发备份"
    echo "  7) 编辑配置"
    echo "  8) 重启服务"
    echo "  9) 卸载 TG-Agent"
    echo "  0) 退出"
    echo
    read -rp "  请输入选项 [0-9]: " CHOICE
    echo
    case "$CHOICE" in
        1) run_cli status ;;
        2) LANG=zh_CN.UTF-8 journalctl -u tg-agent -f -n 50 || true ;;
        3) run_cli reload ;;
        4) run_cli soft-reset ;;
        5) run_cli kill-subprocesses ;;
        6) run_cli backup ;;
        7) nano "$APP_DIR/configs/.env" || true ;;
        8) if systemctl restart tg-agent.service; then echo "✅ 已重启"; else echo "❌ 重启失败"; fi ;;
        9) if bash "$APP_DIR/scripts/uninstall.sh"; then exit 0; fi ;;
        0) exit 0 ;;
        *) echo "❌ 无效选项"; sleep 1 ;;
    esac
    echo
    read -rp "  按回车返回面板..." _
done
