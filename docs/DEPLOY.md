# TG-Agent 部署与运维指南

## 安装（Debian / Ubuntu）

```bash
curl -fsSL https://raw.githubusercontent.com/tianhe762-bot/tg-agent-platform/main/install.sh | sudo bash
```

或使用本地发布包：

```bash
sudo bash scripts/install.sh
```

安装程序自动完成：创建 `tgagent` 用户 → 部署到 `/opt/tg-agent` → 创建 venv 并安装依赖 → 生成 `configs/.env` → 安装 systemd 服务。

## 配置

编辑 `/opt/tg-agent/configs/.env`（权限 600）：

```bash
sudo nano /opt/tg-agent/configs/.env
```

必填项：`BOT_TOKEN`、`ADMIN_IDS`、`LLM_API_KEY`（DeepSeek/OpenAI 等）。常用项：`MIHOMO_API`、`TG_PROXY`、`WOL_MAC`、`SUDO_WHITELIST`。

## 启动与验证

```bash
sudo systemctl enable --now tg-agent.service
sudo systemctl status tg-agent.service
sudo journalctl -u tg-agent -f
```

验证：在 Telegram 中向机器人发送 `/status`、`/ai 你好`。

## 运维 CLI（服务器上）

交互式管理面板：

```bash
sudo tg-agent
```

面板包含：状态、实时日志、热重载、温和重置、强杀子进程、备份、编辑配置、重启、卸载。

命令行方式：

```bash
cd /opt/tg-agent
sudo -u tgagent venv/bin/python -m src.cli status
sudo -u tgagent venv/bin/python -m src.cli reload
sudo -u tgagent venv/bin/python -m src.cli soft-reset
sudo -u tgagent venv/bin/python -m src.cli kill-subprocesses
sudo -u tgagent venv/bin/python -m src.cli backup
```

## 更新

重新执行安装引导即可覆盖程序文件；`configs/.env`、`data/`、`logs/` 会保留。

```bash
curl -fsSL https://raw.githubusercontent.com/tianhe762-bot/tg-agent-platform/main/install.sh | sudo bash
```

## 回滚

```bash
sudo systemctl stop tg-agent.service
```

旧版程序位于发布包解压目录（安装时自动清理），若需还原，请重新安装上一版本发布包并恢复 `configs/.env`、`data/`、`logs/` 目录。升级前建议先备份：

```bash
sudo -u tgagent venv/bin/python -m src.cli backup
sudo cp -r /opt/tg-agent/configs /opt/tg-agent/configs.bak.$(date +%Y%m%d)
```

## 卸载

两种模式，均需 root：

```bash
sudo bash /opt/tg-agent/scripts/uninstall.sh --keep-data  # 保留配置与数据
sudo bash /opt/tg-agent/scripts/uninstall.sh --full       # 完全卸载
```

或直接运行 `sudo bash /opt/tg-agent/scripts/uninstall.sh` 进入交互选择。完全卸载会删除 `/opt/tg-agent`、systemd 单元、sudo 白名单与 `tgagent` 用户，需输入 `yes` 确认。

## 旧 Bash 平台共存

TG-Agent 与旧平台（`/opt/tg_bot`）可同时运行；两者使用不同 Bot Token 时不冲突。迁移时可直接复用旧平台 `configs` 中的 `TOKEN/ADMINS/MIHOMO_API/TG_PROXY` 等配置。
