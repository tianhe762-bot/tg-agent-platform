#!/usr/bin/env bash
# ============================================================
# TG-Agent 管理面板
# 用法: sudo tg-agent
# ============================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

APP_DIR="/opt/tg-agent"
PY="$APP_DIR/venv/bin/python"
LOG_DIR="$APP_DIR/logs"
REPO="tianhe762-bot/tg-agent-platform"
GITHUB_URL="https://github.com/$REPO"

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

# 二级确认界面：先描述功能，再询问 1=执行 / 0=返回
confirm_action()
{
    local desc="$1"
    shift
    echo
    echo "  操作说明："
    printf '    %s\n' "$desc"
    echo
    read -rp "  是否执行？[1=是 / 0=返回]: " OK
    echo
    if [ "$OK" = "1" ]; then
        "$@"
        echo
        read -rp "  操作完成，按回车返回..." _
    else
        echo "  已取消，返回主菜单"
        sleep 1
    fi
}

# 日志查看：进入日志目录自己浏览，看完自己退出
view_logs()
{
    while true; do
        clear 2>/dev/null || true
        echo "=============================================="
        echo "          TG-Agent 日志查看"
        echo "=============================================="
        echo "  📁 日志目录: $LOG_DIR"
        echo "----------------------------------------------"
        echo
        ls -lh "$LOG_DIR" 2>/dev/null | tail -n +2 || echo "  （暂无日志文件）"
        echo
        echo "  1) 查看 tg-agent.log（less 翻页，按 q 退出）"
        echo "  2) 查看 audit_sudo.log（sudo 审计日志）"
        echo "  3) 跟随最新日志（tail -f，Ctrl-C 停止）"
        echo "  0) 返回主菜单"
        echo
        read -rp "  请选择 [0-3]: " CHOICE
        case "$CHOICE" in
            1) less -N "$LOG_DIR/tg-agent.log" 2>/dev/null || true ;;
            2) less -N "$LOG_DIR/audit_sudo.log" 2>/dev/null || true ;;
            3) LANG=zh_CN.UTF-8 tail -f "$LOG_DIR/tg-agent.log" || true ;;
            0) return 0 ;;
            *) echo "  ❌ 无效选项"; sleep 1 ;;
        esac
    done
}

# 检查更新：对比 GitHub Release，确认后下载、校验、安装并重启（保留配置）
check_update()
{
    echo "  正在检查更新..."
    local CURRENT LATEST_TAG LATEST_VERSION URL TMP_DIR EXPECTED ACTUAL
    CURRENT="$(cat "$APP_DIR/VERSION" 2>/dev/null || echo '?')"
    LATEST_TAG="$(curl -s -o /dev/null -w '%{url_effective}' -L --max-time 20 "https://github.com/$REPO/releases/latest" 2>/dev/null | sed -n 's#.*/tag/##p')"
    if [ -z "$LATEST_TAG" ]; then
        echo "  ❌ 无法连接 GitHub 获取最新版本，请检查服务器网络"
        return 1
    fi
    LATEST_VERSION="${LATEST_TAG#v}"
    echo "  当前版本: v$CURRENT"
    echo "  最新版本: $LATEST_TAG"
    if [ "$LATEST_VERSION" = "$CURRENT" ]; then
        echo "  ✅ 已是最新版本"
        return 0
    fi
    echo
    read -rp "  是否更新到 $LATEST_TAG？[1=是 / 0=否]: " OK
    if [ "$OK" != "1" ]; then
        echo "  已取消"
        return 0
    fi

    TMP_DIR="$(mktemp -d)"
    URL="https://github.com/$REPO/releases/download/$LATEST_TAG/tg-agent-v$LATEST_VERSION.tar.gz"
    echo "  下载中: $URL"
    if ! curl -fL --connect-timeout 15 --retry 3 -o "$TMP_DIR/pkg.tar.gz" "$URL"; then
        echo "  ❌ 下载失败"
        rm -rf "$TMP_DIR"
        return 1
    fi
    if curl -fL --connect-timeout 10 --retry 2 -o "$TMP_DIR/pkg.sha256" "$URL.sha256" 2>/dev/null; then
        EXPECTED="$(awk '{print $1}' "$TMP_DIR/pkg.sha256")"
        ACTUAL="$(sha256sum "$TMP_DIR/pkg.tar.gz" | awk '{print $1}')"
        if [ "$EXPECTED" != "$ACTUAL" ]; then
            echo "  ❌ SHA256 校验失败，已中止更新"
            rm -rf "$TMP_DIR"
            return 1
        fi
        echo "  ✅ SHA256 校验通过"
    else
        echo "  ⚠️ 校验文件下载失败，跳过强校验"
    fi
    mkdir -p "$TMP_DIR/extract"
    if ! tar -xzf "$TMP_DIR/pkg.tar.gz" -C "$TMP_DIR/extract"; then
        echo "  ❌ 解压失败"
        rm -rf "$TMP_DIR"
        return 1
    fi
    echo "  安装新版本（保留配置与数据）..."
    bash "$TMP_DIR/extract/scripts/install.sh"
    systemctl restart tg-agent.service
    rm -rf "$TMP_DIR"
    echo "  ✅ 更新完成，服务已重启"
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
    echo "  2) 查看日志"
    echo "  3) 配置热重载"
    echo "  4) 温和重置"
    echo "  5) 强杀子进程"
    echo "  6) 触发备份"
    echo "  7) 编辑配置"
    echo "  8) 重启服务"
    echo "  9) 检查更新"
    echo "  10) 卸载 TG-Agent"
    echo "  0) 退出"
    echo
    read -rp "  请输入选项 [0-10]: " CHOICE
    echo
    case "$CHOICE" in
        1) run_cli status
           echo
           read -rp "  按回车返回主菜单..." _ ;;
        2) view_logs ;;
        3) confirm_action "配置热重载：重新读取 configs/.env 的配置并立即生效，无需重启服务。" run_cli reload ;;
        4) confirm_action "温和重置：清空待确认状态与任务队列，不触碰数据库与 WAL 文件。" run_cli soft-reset ;;
        5) confirm_action "强杀子进程：强制终止所有活跃的子进程组（SIGKILL），适用于命令卡死场景。" run_cli kill-subprocesses ;;
        6) run_cli backup
           echo
           read -rp "  按回车返回主菜单..." _ ;;
        7) nano "$APP_DIR/configs/.env" || true ;;
        8) if systemctl restart tg-agent.service; then echo "  ✅ 已重启"; else echo "  ❌ 重启失败"; fi
           read -rp "  按回车返回主菜单..." _ ;;
        9) check_update
           echo
           read -rp "  按回车返回主菜单..." _ ;;
        10) if bash "$APP_DIR/scripts/uninstall.sh"; then exit 0; fi ;;
        0) exit 0 ;;
        *) echo "  ❌ 无效选项"; sleep 1 ;;
    esac
done
