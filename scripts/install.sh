#!/usr/bin/env bash
# ============================================================
# TG-Agent 一键安装脚本（Debian / Ubuntu）
# 用法: sudo bash scripts/install.sh
# ============================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ 请使用 root 权限运行: sudo bash scripts/install.sh"
    exit 1
fi

APP_DIR="/opt/tg-agent"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=========================================="
echo "       TG-Agent Installer v$(cat "$SRC_DIR/VERSION")"
echo "=========================================="

# 1. 基础依赖
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip >/dev/null

# 2. 创建用户与目录
if ! id tgagent >/dev/null 2>&1; then
    useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin tgagent
    echo "✅ 已创建系统用户 tgagent"
fi
mkdir -p "$APP_DIR"/{src,tools,configs,data,logs,scripts,systemd}

# 3. 拷贝程序文件（排除开发目录与敏感配置）
cp -r "$SRC_DIR/src/." "$APP_DIR/src/"
cp "$SRC_DIR/requirements.txt" "$APP_DIR/"
cp "$SRC_DIR/VERSION" "$APP_DIR/"
cp -r "$SRC_DIR/scripts/." "$APP_DIR/scripts/"
cp -r "$SRC_DIR/systemd/." "$APP_DIR/systemd/"

# 4. 虚拟环境与依赖
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "✅ 依赖安装完成"

# 5. 配置文件
if [ ! -f "$APP_DIR/configs/.env" ]; then
    cp "$SRC_DIR/configs/.env.example" "$APP_DIR/configs/.env"
    chmod 600 "$APP_DIR/configs/.env"
    echo "✅ 已生成 configs/.env（请填写 BOT_TOKEN / ADMIN_IDS / LLM_API_KEY）"
else
    echo "ℹ️ configs/.env 已存在，保留现有配置"
fi

# 6. sudo 白名单（示例，请按需精确添加，禁止通配符）
if [ ! -f /etc/sudoers.d/tg-agent ]; then
    cat > /etc/sudoers.d/tg-agent <<'EOF'
# TG-Agent sudo 死白名单示例：只允许精确命令，禁止通配符
# tgagent ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart tg-agent.service
# tgagent ALL=(ALL) NOPASSWD: /usr/sbin/reboot
# tgagent ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now
EOF
    chmod 440 /etc/sudoers.d/tg-agent
    echo "✅ 已创建 /etc/sudoers.d/tg-agent（请按需取消注释）"
fi

# 7. systemd 服务
cp "$SRC_DIR/systemd/tg-agent.service" /etc/systemd/system/tg-agent.service
systemctl daemon-reload
systemctl enable tg-agent.service
echo "✅ systemd 服务已启用"

# 8. 权限
chown -R tgagent:tgagent "$APP_DIR"

echo
echo "=========================================="
echo "       ✅ TG-Agent 安装完成"
echo "=========================================="
echo
echo "下一步："
echo "1. 编辑 $APP_DIR/configs/.env 填入配置"
echo "2. 启动服务: sudo systemctl start tg-agent.service"
echo "3. 查看日志: sudo journalctl -u tg-agent -f"
