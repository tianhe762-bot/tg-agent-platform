"""Tool registry and default tool wiring."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.config import AppConfig
from src.tools.db import AgentDB
from src.tools.executor import CommandDeniedError, Executor, ExecError, RateLimitError
from src.tools.mihomo import MihomoClient
from src.tools.monitor import Monitor


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[[int, Dict[str, Any]], Awaitable[str]]
    parameters: List[Dict[str, Any]] = field(default_factory=list)

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list(self) -> List[Tool]:
        return list(self._tools.values())

    async def call(self, name: str, chat_id: int, args: Dict[str, Any]) -> str:
        tool = self.get(name)
        if tool is None:
            return f"❌ 未知工具: {name}"
        try:
            return await tool.handler(chat_id, args or {})
        except (ExecError, RateLimitError, CommandDeniedError) as exc:
            return f"❌ {exc}"
        except Exception as exc:  # module-level isolation
            return f"❌ 工具 {name} 执行异常: {exc}"


def _arg(args: Dict[str, Any], key: str, default: Any = None) -> Any:
    return args.get(key, default)


def register_default_tools(
    registry: ToolRegistry,
    config: AppConfig,
    db: AgentDB,
    executor: Executor,
    monitor: Monitor,
    mihomo: MihomoClient,
) -> None:
    from src import __version__
    from src.tools import system as sys_tools
    from src.tools import wol as wol_tools

    async def status_handler(chat_id: int, args: Dict[str, Any]) -> str:
        return await sys_tools.system_status(config, executor, mihomo)

    async def ports_handler(chat_id: int, args: Dict[str, Any]) -> str:
        return await sys_tools.ports(config, executor)

    async def mihomo_nodes_handler(chat_id: int, args: Dict[str, Any]) -> str:
        return await mihomo.nodes_text()

    async def mihomo_switch_handler(chat_id: int, args: Dict[str, Any]) -> str:
        name = str(_arg(args, "name", "")).strip()
        if not name:
            return "❌ 请提供节点名，例如 /switch 节点-香港01"
        return await mihomo.switch(name)

    async def wol_handler(chat_id: int, args: Dict[str, Any]) -> str:
        mac = str(_arg(args, "mac", "")).strip()
        if not mac:
            return "❌ 未配置 WOL MAC，请提供 mac 参数"
        try:
            await wol_tools.send_wol(mac)
        except ValueError as exc:
            return f"❌ {exc}"
        return "✅ 已发送 WOL 唤醒包"

    async def backup_handler(chat_id: int, args: Dict[str, Any]) -> str:
        return await sys_tools.backup_all(config, db, executor)

    async def execute_handler(chat_id: int, args: Dict[str, Any]) -> str:
        command = str(_arg(args, "command", "")).strip()
        if not command:
            return "❌ 请提供 command 参数"
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return f"❌ 命令解析失败: {exc}"
        if not executor.check_allowlist(argv):
            return f"❌ 命令不在只读白名单中（允许: {' '.join(config.exec_allowlist)}）"
        result = await executor.run(argv)
        return result.combined() or f"（命令完成，退出码 {result.returncode}）"

    async def reboot_handler(chat_id: int, args: Dict[str, Any]) -> str:
        if not bool(_arg(args, "confirm", False)):
            return "⚠️ 危险操作，请回复 /reboot 确认执行"
        return await sys_tools.reboot(config, executor)

    async def shutdown_handler(chat_id: int, args: Dict[str, Any]) -> str:
        if not bool(_arg(args, "confirm", False)):
            return "⚠️ 危险操作，请回复 /shutdown 确认执行"
        return await sys_tools.shutdown(config, executor)

    async def watchdog_handler(chat_id: int, args: Dict[str, Any]) -> str:
        action = str(_arg(args, "action", "status")).strip().lower()
        current = await db.kv_get("watchdog_enabled") or ("true" if config.watchdog_enabled else "false")
        if action == "on":
            await db.kv_set("watchdog_enabled", "true")
            return "✅ 看门狗已开启"
        if action == "off":
            await db.kv_set("watchdog_enabled", "false")
            return "✅ 看门狗已关闭"
        return f"🐶 看门狗状态: {'开启' if current == 'true' else '关闭'}\n回复 /watchdog on 或 /watchdog off 切换"

    async def help_handler(chat_id: int, args: Dict[str, Any]) -> str:
        return (
            "🤖 TG-Agent 使用帮助\n\n"
            "直接发消息即可对话（AI 自动调用工具）。\n"
            "斜杠命令快速通道：\n"
            "• /status 系统状态总览\n"
            "• /ports 局域网服务端口\n"
            "• /mihomo 节点测速\n"
            "• /switch 节点名 切换节点\n"
            "• /wake 发送 WOL 唤醒包\n"
            "• /backup 数据与配置备份\n"
            "• /reboot /shutdown 重启/关机（需二次确认）\n"
            "• /watchdog 看门狗管理\n"
            "• /ai 你的问题 直接进入 AI 模式\n"
            f"• 版本: v{__version__}"
        )

    registry.register(Tool("system_status", "获取服务器状态总览（CPU/内存/磁盘/网络/Docker/Mihomo）", status_handler))
    registry.register(Tool("ports", "列出局域网各服务端口与访问地址", ports_handler))
    registry.register(Tool("mihomo_nodes", "列出 Mihomo 可用节点并测速（≤800ms）", mihomo_nodes_handler))
    registry.register(
        Tool(
            "mihomo_switch",
            "切换 Mihomo 代理节点",
            mihomo_switch_handler,
            [{"name": "name", "type": "string", "required": True, "description": "完整节点名"}],
        )
    )
    registry.register(
        Tool(
            "wol",
            "发送 WOL 魔包远程开机局域网设备",
            wol_handler,
            [{"name": "mac", "type": "string", "required": True, "description": "目标 MAC 地址"}],
        )
    )
    registry.register(Tool("backup", "备份数据库与配置文件（SHA-256 校验）", backup_handler))
    registry.register(
        Tool(
            "execute",
            "执行只读命令（白名单限制）并返回输出",
            execute_handler,
            [{"name": "command", "type": "string", "required": True, "description": "只读命令，如 df -h"}],
        )
    )
    registry.register(
        Tool(
            "reboot",
            "重启服务器（危险操作，需 confirm=true）",
            reboot_handler,
            [{"name": "confirm", "type": "boolean", "required": False, "description": "确认执行"}],
        )
    )
    registry.register(
        Tool(
            "shutdown",
            "关闭服务器（危险操作，需 confirm=true）",
            shutdown_handler,
            [{"name": "confirm", "type": "boolean", "required": False, "description": "确认执行"}],
        )
    )
    registry.register(
        Tool(
            "watchdog",
            "查看或切换看门狗状态（action: status/on/off）",
            watchdog_handler,
            [{"name": "action", "type": "string", "required": False, "description": "status/on/off"}],
        )
    )
    registry.register(Tool("help", "显示使用帮助", help_handler))
