# 更新记录

> 📌 **版本策略**：v1.0.0 为稳定基线，此后按语义化版本正常迭代。
> - 补丁 v1.0.x：仅修复 bug，不新增功能
> - 次版本 v1.x.0：新增功能，保持向后兼容
> - 主版本 v2.0.0：不兼容的重大变更
>
> 每次发布流程：更新 `VERSION` → 追加本文件 → 构建发布包（`scripts/build_release.py`）→ 打 tag → 创建 GitHub Release。已部署服务器可通过面板「检查更新」一键升级。

## v1.0.0 (2026-08-13)

TG-Agent Python 版首个正式发布，作为 Bash 版 tg-bot-platform 的升级换代。

### 核心能力

- Telegram 长轮询网关：管理员白名单、入站注入过滤、出站脱敏、离线消息队列（去重 + 24h 过期）
- ReAct AI Agent：OpenAI 兼容接口（OpenAI / one-api / vLLM / Ollama），工具自动调度
- 斜杠命令快速通道（无需 LLM）：`/status`、`/ports`、`/mihomo`、`/switch`、`/wake`、`/backup`、`/reboot`、`/shutdown`、`/watchdog`、`/ai`、`/help`
- Mihomo 节点管理：最底层节点测速、一键切换、主组路由联动、切换后关闭旧连接
- 三维动态降级：Load_1min / 可用内存 / 磁盘 I/O 等待，两级降级 + 滞回恢复
- SQLite WAL 分级写：关键写同步 + 被动 checkpoint，非关键写合并（10s / 50 条 / 5MB 上限）
- 安全 VACUUM：freelist > 30% 且 > 50MB 才执行，每周一次；内置备份重试 + SHA-256 校验 + 外置同步
- LLM 韧性：429 指数退避（Retry-After 感知）、5xx/超时三态熔断器（Closed → Open 2min → Half-Open）
- 命令执行安全：只读白名单、字节/频率双限、超时 PGID 强杀、sudo 死白名单 + 二次断言 + 审计日志
- 看门狗：事件循环心跳检测，卡死自动重启（systemd 兜底）
- 运维 CLI：`status`、`reload`、`soft-reset`、`kill-subprocesses`、`backup`、`restart`
- 部署：systemd（CPUQuota=95%、IOWeight=20、ProtectSystem=strict）、一键安装脚本、Docker、GitHub Actions CI
- 卸载：`scripts/uninstall.sh` 支持保留数据卸载与完全卸载两种模式
- 管理面板：`sudo tg-agent` 交互式菜单（标题 + GitHub 链接，含状态/日志/配置/备份/重启/卸载）
- 修复：面板执行 CLI 的工作目录问题；实时日志降噪（apscheduler/httpx 不再刷英文 INFO，同时避免 Bot Token 出现在日志）
- 修复：`TG_AGENT_ENV` 未设置时配置路径错误解析为 `.`，导致 CLI 读不到 `.env`
- 面板：新增"检查更新"（对比 GitHub Release，可一键更新并保留配置）；热重载/温和重置/强杀子进程增加二级确认界面（描述 + 0/1 选择）；日志改为文件浏览（进入日志目录用 less 查看，按 q 退出）
- 日志：最多保留 30 天，自动清理过期文件
- 配置示例：`configs/.env.example` 增加大量中文注释；安装完成后醒目提醒填写必填项

### 与 Bash 版的关系

- 兼容现有 `MIHOMO_API` / `TG_PROXY` 等配置项，可平滑迁移
- 覆盖旧平台全部管理命令，并新增自然语言 AI 能力
