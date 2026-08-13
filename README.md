# TG-Agent

<p align="center"><b>Debian 个人服务器 AI Agent · Python 版 · tg-bot-platform 升级换代</b></p>

TG-Agent 是基于 Python asyncio 的 Telegram 服务器管理 Agent：既保留了上一代 Bash 平台的全部运维命令（状态、端口、Mihomo 节点、WOL、备份、重启），又新增了 ReAct AI 对话能力，并内置资源降级、安全熔断、分级写库等生产级防护，专为低配机械盘家庭服务器设计。

## ✨ 功能一览

| 能力 | 说明 |
| --- | --- |
| 🤖 AI 对话 | 自然语言直接问，自动调用工具获取真实数据（OpenAI 兼容接口） |
| 📊 系统状态 | `/status` CPU/内存/磁盘/网络/Docker/Mihomo 一次看全 |
| 🌐 服务端口 | `/ports` 自动扫描 Docker 与非 Docker 端口，给出局域网地址 |
| 🚀 节点管理 | `/mihomo` 最底层节点并行测速；`/switch 节点名` 一键切换并校验生效 |
| ⚡ WOL 远程开机 | `/wake` 发送魔包 |
| 💾 自动备份 | 数据库 VACUUM INTO 一致性备份，SHA-256 校验 + 外置同步 |
| 🔔 监控告警 | CPU/内存/磁盘异常自动推送，同内容冷却去重，5 秒窗口聚合 |
| 🐶 看门狗 | 事件循环心跳检测，卡死自动重启服务 |
| 🛡️ 安全机制 | 管理员白名单、入站注入过滤、出站脱敏、sudo 死白名单 + 审计 |

## 🏗️ 架构

```text
Telegram Gateway (长轮询 / 管理员白名单 / 注入过滤 / 脱敏 / 离线队列)
        │
        ▼
Monolithic Python Runtime (asyncio + 模块级异常隔离)
  ├── ReAct Agent ── LLM 客户端（429 退避 + 三态熔断器）
  ├── 工具层（executor / system / monitor / db / mihomo / wol）
  ├── 三维降级（Load / 内存 / I/O 等待，两级滞回）
  └── SQLite WAL 分级写（关键同步 / 非关键合并）+ 安全 VACUUM + 备份闭环
```

## 🚀 快速开始

### 1. 一键安装（Debian）

```bash
curl -fsSL https://raw.githubusercontent.com/tianhe762-bot/tg-agent-platform/main/install.sh | sudo bash
```

或使用本地源码包：`sudo bash scripts/install.sh`。

安装程序自动完成：创建 `tgagent` 用户 → 部署到 `/opt/tg-agent` → 创建 venv 并安装依赖 → 生成 `configs/.env` → 安装 systemd 服务。

### 2. 填写配置

```bash
sudo nano /opt/tg-agent/configs/.env
```

必填项：

- `BOT_TOKEN`：找 [@BotFather](https://t.me/BotFather) 创建
- `ADMIN_IDS`：你的 Telegram 数字 ID（逗号分隔）
- `LLM_API_KEY`：DeepSeek / OpenAI 或任意 OpenAI 兼容服务密钥（本地 vLLM/Ollama 可留空）

DeepSeek 示例：

```bash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=deepseek-chat
```

常用项：`MIHOMO_API`（Clash 外部控制器地址）、`WOL_MAC`、`TG_PROXY`、`SUDO_WHITELIST`。

### 3. 启动

```bash
sudo systemctl start tg-agent.service
sudo systemctl status tg-agent.service
sudo journalctl -u tg-agent -f
```

### 4. 使用

在 Telegram 里向机器人发消息即可。斜杠命令是快速通道（不消耗 LLM）：

| 命令 | 功能 |
| --- | --- |
| `/status` | 系统状态总览 |
| `/ports` | 局域网服务端口 |
| `/mihomo` | 节点测速（≤800ms） |
| `/switch 节点名` | 切换节点 |
| `/wake` | WOL 远程开机 |
| `/backup` | 数据与配置备份 |
| `/reboot`、`/shutdown` | 重启/关机（二次确认） |
| `/watchdog` | 看门狗管理 |
| `/ai 你的问题` | 显式 AI 模式 |
| `/help` | 使用帮助 |

直接发自然语言消息也会进入 AI 模式，例如：`服务器现在负载怎么样？`、`帮我切到延迟最低的香港节点`。

## ⚙️ 运维 CLI

### 管理面板（推荐）

```bash
sudo tg-agent
```

交互式面板：查看状态、日志浏览（进入日志目录用 less 查看，按 q 退出）、配置热重载、温和重置、强杀子进程、备份、编辑配置、重启、检查更新（对比 GitHub Release 一键更新）、卸载。

热重载 / 温和重置 / 强杀子进程等操作会先弹出二级确认界面，描述功能后再询问是否执行（1=是 / 0=返回）。

更新管理（选项 9）与旧平台一致：检查更新（仅检测）/ 在线更新（标准保留配置或完全重置，自动备份、SHA-256 校验、失败回滚）/ 回滚版本 / 自定义更新源 / 查看版本 / 查看备份列表。

纯 AI 模式（选项 10）：开启后所有消息（包括以 `/` 开头的文字）一律作为普通文字交给 AI 纯聊天，不执行工具、不解析命令，直接发文字即可；关闭需在面板操作。

### 命令行

```bash
python -m src.cli status          # 状态与数据库统计
python -m src.cli reload          # 配置原子热重载（不重启）
python -m src.cli soft-reset      # 温和重置（清空任务队列与确认状态）
python -m src.cli kill-subprocesses  # 强杀所有活跃子进程组
python -m src.cli backup          # 触发备份
python -m src.cli restart         # systemctl restart tg-agent
```

## 🔐 安全设计

- **sudo 死白名单**：`SUDO_WHITELIST` 只接受精确 argv（如 `["/usr/sbin/reboot"]`），禁止通配符；执行前二次 realpath 断言，全程写入 `logs/audit_sudo.log`
- **出站脱敏**：API Key、JWT、密码、私网 IP、敏感路径在发往 Telegram 前自动打码
- **入站注入过滤**：疑似提示词注入直接拦截
- **命令配额**：`execute` 工具只允许只读白名单命令，输出限 100KB、频率 20 次/分钟
- **系统隔离**：`ProtectSystem=strict`、`ProtectHome=read-only`、`NoNewPrivileges`、CPUQuota=95%、IOWeight=20

## 🔄 发布

```bash
bash scripts/release.sh   # 生成 dist/tg-agent-vX.Y.Z.tar.gz + .sha256
```

发布包内所有文本文件统一 LF 换行（CI 强制校验无 CRLF），防止 Windows autocrlf 破坏服务器脚本。

版本策略：v1.0.0 为稳定基线，之后按语义化版本迭代（v1.0.x 补丁 / v1.x.0 新功能 / v2.0.0 重大变更）。每次发布更新 `VERSION` 与 `CHANGELOG.md`，打 tag 并创建 GitHub Release，已部署服务器通过面板「检查更新」一键升级。

## 🗑️ 卸载

```bash
sudo bash scripts/uninstall.sh             # 交互式选择
sudo bash scripts/uninstall.sh --keep-data # 保留配置与数据（可重装）
sudo bash scripts/uninstall.sh --full      # 完全卸载（删除全部，不可恢复）
```

- 保留数据卸载：移除程序与 systemd 服务，`/opt/tg-agent` 的配置和数据原样保留
- 完全卸载：删除程序、配置、数据、systemd 单元、sudo 白名单与 `tgagent` 用户（需输入 `yes` 二次确认）

## 🧪 测试

```bash
python -m pytest -q
python -m flake8 src tests --max-line-length=140
```

## 📌 路线图

- Webhook 模式（TLS）
- 文件上传/下载受限目录（100MB 配额，禁止可执行文件）
- 定时任务与 Webhook 接收
- 自更新（复用 Bash 版 update 流程）
