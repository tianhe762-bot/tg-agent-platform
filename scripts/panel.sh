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
BACKUP_DIR="$APP_DIR/backups"
UPDATE_CONF="$APP_DIR/configs/update.env"
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

pause()
{
    echo
    read -rp "  按回车继续..." _
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
        pause
    else
        echo "  已取消，返回主菜单"
        sleep 1
    fi
}

# ---------------- 日志查看 ----------------
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
            1)
                if [ -f "$LOG_DIR/tg-agent.log" ]; then
                    less -N "$LOG_DIR/tg-agent.log" 2>/dev/null || true
                else
                    echo "  ❌ tg-agent.log 不存在"
                    sleep 1
                fi
                ;;
            2)
                if [ -f "$LOG_DIR/audit_sudo.log" ]; then
                    less -N "$LOG_DIR/audit_sudo.log" 2>/dev/null || true
                else
                    echo "  ❌ audit_sudo.log 不存在（尚无 sudo 操作）"
                    sleep 1
                fi
                ;;
            3)
                if [ -f "$LOG_DIR/tg-agent.log" ]; then
                    LANG=zh_CN.UTF-8 tail -f "$LOG_DIR/tg-agent.log" || true
                else
                    echo "  ❌ tg-agent.log 不存在"
                    sleep 1
                fi
                ;;
            0) return 0 ;;
            *) echo "  ❌ 无效选项"; sleep 1 ;;
        esac
    done
}

# ---------------- 更新管理 ----------------
fetch_latest_tag()
{
    curl -s -o /dev/null -w '%{url_effective}' -L --max-time 20 \
        "https://github.com/$REPO/releases/latest" 2>/dev/null | sed -n 's#.*/tag/##p'
}

check_update_only()
{
    echo "  正在检测远程最新版本..."
    source "$UPDATE_CONF" 2>/dev/null || true
    local CURRENT
    CURRENT="$(tr -d ' \r\n' < "$APP_DIR/VERSION" 2>/dev/null || echo '?')"
    echo "  当前版本: v$CURRENT"
    if [ -n "${TG_AGENT_PACKAGE_URL:-}" ]; then
        echo "  ℹ️ 已设置自定义更新源，跳过远程版本检测"
        echo "  更新源: $TG_AGENT_PACKAGE_URL"
        return 0
    fi
    local LATEST
    LATEST="$(fetch_latest_tag)"
    if [ -z "$LATEST" ]; then
        echo "  ❌ 无法获取最新版本（GitHub 不可达或网络异常）"
        return 1
    fi
    echo "  最新版本: $LATEST"
    if [ "$CURRENT" = "${LATEST#v}" ]; then
        echo "  ✅ 已是最新版本"
    elif [ "$(printf '%s\n%s\n' "$CURRENT" "${LATEST#v}" | sort -V | tail -n1)" = "${LATEST#v}" ]; then
        echo "  📦 发现新版本 v${LATEST#v}，可进入「在线更新」安装"
    else
        echo "  ✅ 已是最新版本（本地版本更高）"
    fi
}

online_update()
{
    source "$UPDATE_CONF" 2>/dev/null || true
    local CURRENT LATEST_VERSION URL SHA256_URL TMP_DIR MODE CONFIRM BACKUP_TAR BACKUP_TAG SOURCE TOP ENTRIES EXPECTED ACTUAL
    CURRENT="$(tr -d ' \r\n' < "$APP_DIR/VERSION" 2>/dev/null || echo '?')"
    echo "  在线更新"
    echo "  当前版本: v$CURRENT"
    echo
    echo "  请选择更新模式："
    echo "  1) 标准更新（推荐，保留配置/数据/日志）"
    echo "  2) 完全重置更新（清空配置后重装）"
    echo "  0) 返回"
    echo
    read -rp "  请选择 [默认 1]: " MODE
    MODE="${MODE:-1}"
    if [ "$MODE" = "0" ]; then
        return
    fi
    if [ "$MODE" = "2" ]; then
        read -rp "  ⚠️ 确定清空全部配置并重装? (yes/no): " CONFIRM
        if [ "$CONFIRM" != "yes" ]; then
            echo "  已取消"
            return
        fi
    fi

    if [ -n "${TG_AGENT_PACKAGE_URL:-}" ]; then
        URL="$TG_AGENT_PACKAGE_URL"
        SHA256_URL="${URL}.sha256"
        LATEST_VERSION="自定义源"
        echo "  使用自定义更新源: $URL"
    else
        LATEST_VERSION="$(fetch_latest_tag)"
        if [ -z "$LATEST_VERSION" ]; then
            echo "  ❌ 无法获取最新版本"
            return 1
        fi
        LATEST_VERSION="${LATEST_VERSION#v}"
        URL="https://github.com/$REPO/releases/download/v$LATEST_VERSION/tg-agent-v$LATEST_VERSION.tar.gz"
        SHA256_URL="${URL}.sha256"
        echo "  最新版本: v$LATEST_VERSION"
    fi

    TMP_DIR="$(mktemp -d)"
    echo "  下载中..."
    if ! curl -fL --connect-timeout 15 --retry 3 -o "$TMP_DIR/pkg.tar.gz" "$URL"; then
        echo "  ❌ 下载失败，已中止"
        rm -rf "$TMP_DIR"
        return 1
    fi
    if curl -fL --connect-timeout 10 --retry 2 -o "$TMP_DIR/pkg.sha256" "$SHA256_URL" 2>/dev/null; then
        EXPECTED="$(awk '{print $1}' "$TMP_DIR/pkg.sha256")"
        ACTUAL="$(sha256sum "$TMP_DIR/pkg.tar.gz" | awk '{print $1}')"
        if [ "$EXPECTED" != "$ACTUAL" ]; then
            echo "  ❌ SHA256 校验失败，已中止"
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
    ENTRIES="$(find "$TMP_DIR/extract" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"
    TOP="$(find "$TMP_DIR/extract" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -n1)"
    if [ "$ENTRIES" -eq 1 ] && [ -n "$TOP" ]; then
        SOURCE="$TOP"
    else
        SOURCE="$TMP_DIR/extract"
    fi
    if [ ! -f "$SOURCE/scripts/install.sh" ]; then
        echo "  ❌ 更新包结构错误，缺少 scripts/install.sh"
        rm -rf "$TMP_DIR"
        return 1
    fi

    mkdir -p "$BACKUP_DIR"
    BACKUP_TAG="$(date +%Y%m%d_%H%M%S)"
    BACKUP_TAR="$BACKUP_DIR/tg-agent_v${CURRENT}_${BACKUP_TAG}.tar.gz"
    echo "  备份当前版本 -> $BACKUP_TAR"
    tar -czf "$BACKUP_TAR" --exclude="backups" --exclude="logs" --exclude="data" \
        --exclude="configs/.env" -C "$APP_DIR" . 2>/dev/null || true

    if [ "$MODE" = "2" ]; then
        rm -f "$APP_DIR/configs/.env"
        echo "  已清空配置，将按全新安装处理"
    fi

    echo "  应用更新（保留配置与数据）..."
    bash "$SOURCE/scripts/install.sh"
    systemctl restart tg-agent.service
    sleep 3
    if systemctl is-active --quiet tg-agent.service; then
        echo "  ✅ 更新成功，服务已重启"
    else
        echo "  ⚠️ 服务启动失败，自动回滚..."
        tar -xzf "$BACKUP_TAR" -C "$APP_DIR" 2>/dev/null || true
        systemctl restart tg-agent.service
        echo "  ❌ 更新失败，已回滚至 v$CURRENT"
    fi
    rm -rf "$TMP_DIR"
}

rollback_version()
{
    mkdir -p "$BACKUP_DIR"
    echo "  可用更新备份："
    local idx=1
    declare -A RB_MAP
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        echo "  [$idx] $(basename "$f")"
        RB_MAP["$idx"]="$f"
        idx=$((idx + 1))
    done < <(find "$BACKUP_DIR" -maxdepth 1 -name "tg-agent_*.tar.gz" -type f 2>/dev/null | sort -r)
    if [ "$idx" -eq 1 ]; then
        echo "  （暂无备份）"
        return
    fi
    echo
    read -rp "  请输入要回滚的序号: " N
    local TARGET="${RB_MAP[$N]:-}"
    if [ -z "$TARGET" ] || [ ! -f "$TARGET" ]; then
        echo "  ❌ 无效序号"
        return
    fi
    read -rp "  确认回滚到 $(basename "$TARGET")？(yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "  已取消"
        return
    fi
    echo "  回滚中..."
    tar -xzf "$TARGET" -C "$APP_DIR"
    systemctl restart tg-agent.service
    echo "  ✅ 已回滚并重启服务"
}

set_update_source()
{
    source "$UPDATE_CONF" 2>/dev/null || true
    echo "  当前更新源: ${TG_AGENT_PACKAGE_URL:-GitHub 官方 Release}"
    echo "  说明：设置后「在线更新」将使用此地址下载（.tar.gz），留空恢复官方源。"
    echo
    read -rp "  请输入自定义包 URL（留空=官方）: " URL
    if [ -n "$URL" ]; then
        mkdir -p "$APP_DIR/configs"
        touch "$UPDATE_CONF"
        local TMP
        TMP="$(mktemp)"
        grep -v '^TG_AGENT_PACKAGE_URL=' "$UPDATE_CONF" > "$TMP" 2>/dev/null || true
        printf 'TG_AGENT_PACKAGE_URL=%q\n' "$URL" >> "$TMP"
        cat "$TMP" > "$UPDATE_CONF"
        rm -f "$TMP"
        chmod 600 "$UPDATE_CONF"
        echo "  ✅ 自定义更新源已保存"
    else
        rm -f "$UPDATE_CONF"
        echo "  ✅ 已恢复 GitHub 官方更新源"
    fi
}

update_menu()
{
    while true; do
        clear 2>/dev/null || true
        echo "=============================================="
        echo "            更新管理"
        echo "=============================================="
        echo
        echo "  1) 检查更新（仅检测版本，不自动安装）"
        echo "  2) 在线更新（标准更新保留配置 / 完全重置）"
        echo "  3) 回滚版本"
        echo "  4) 设置自定义更新源"
        echo "  5) 查看当前版本"
        echo "  6) 查看更新备份列表"
        echo "  0) 返回主菜单"
        echo
        read -rp "  请选择 [0-6]: " CHOICE
        echo
        case "$CHOICE" in
            1) check_update_only; pause ;;
            2) online_update; pause ;;
            3) rollback_version; pause ;;
            4) set_update_source; pause ;;
            5) echo "  当前版本: v$(tr -d ' \r\n' < "$APP_DIR/VERSION" 2>/dev/null || echo '?')"; echo "  安装目录: $APP_DIR"; pause ;;
            6) if [ -d "$BACKUP_DIR" ] && [ -n "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]; then ls -lh "$BACKUP_DIR"; else echo "  （暂无备份）"; fi; pause ;;
            0) return 0 ;;
            *) echo "  ❌ 无效选项"; sleep 1 ;;
        esac
    done
}

# ---------------- 纯 AI 模式 ----------------
pure_ai_menu()
{
    while true; do
        clear 2>/dev/null || true
        echo "=============================================="
        echo "            纯 AI 模式"
        echo "=============================================="
        echo
        echo "  说明：开启后，所有消息（包括以 / 开头的文字）"
        echo "  一律作为普通文字交给 AI 纯聊天，不执行工具、"
        echo "  不解析斜杠命令——直接发文字即可对话。"
        echo "  关闭需在此面板操作（Telegram 端无法关闭）。"
        echo
        local STATUS
        STATUS="$(run_cli pure-ai status 2>/dev/null | grep -oE '开启|关闭' | head -n1)"
        echo "  当前状态: ${STATUS:-未知}"
        echo
        echo "  1) 开启纯 AI 模式"
        echo "  2) 关闭纯 AI 模式"
        echo "  0) 返回主菜单"
        echo
        read -rp "  请选择 [0-2]: " CHOICE
        case "$CHOICE" in
            1) run_cli pure-ai on; pause ;;
            2) run_cli pure-ai off; pause ;;
            0) return 0 ;;
            *) echo "  ❌ 无效选项"; sleep 1 ;;
        esac
    done
}

# ---------------- 主菜单 ----------------
while true; do
    clear 2>/dev/null || true
    VERSION="$(tr -d ' \r\n' < "$APP_DIR/VERSION" 2>/dev/null || echo '?')"
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
    echo "  9) 更新管理"
    echo "  10) 纯 AI 模式"
    echo "  11) 卸载 TG-Agent"
    echo "  0) 退出"
    echo
    read -rp "  请输入选项 [0-11]: " CHOICE
    echo
    case "$CHOICE" in
        1) run_cli status; pause ;;
        2) view_logs ;;
        3) confirm_action "配置热重载：重新读取 configs/.env 的配置并立即生效，无需重启服务。" run_cli reload ;;
        4) confirm_action "温和重置：清空待确认状态与任务队列，不触碰数据库与 WAL 文件。" run_cli soft-reset ;;
        5) confirm_action "强杀子进程：强制终止所有活跃的子进程组（SIGKILL），适用于命令卡死场景。" run_cli kill-subprocesses ;;
        6) run_cli backup
           echo "  提示：备份在后台执行，结果将通过 Telegram 推送到管理员。"
           pause ;;
        7) nano "$APP_DIR/configs/.env" || true ;;
        8) if systemctl restart tg-agent.service; then echo "  ✅ 已重启"; else echo "  ❌ 重启失败"; fi
           pause ;;
        9) update_menu ;;
        10) pure_ai_menu ;;
        11) if bash "$APP_DIR/scripts/uninstall.sh"; then exit 0; fi ;;
        0) exit 0 ;;
        *) echo "  ❌ 无效选项"; sleep 1 ;;
    esac
done
