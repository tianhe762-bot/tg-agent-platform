"""ReAct agent: LLM-driven tool orchestration plus direct slash-command routing."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.config import AppConfig
from src.llm import LLMClient
from src.tools import ToolRegistry
from src.tools.db import AgentDB
from src.tools.monitor import Monitor

logger = logging.getLogger("tg-agent.agent")


def build_system_prompt(registry: ToolRegistry, degrade_level: int) -> str:
    tools = "\n".join(
        f"- {t.name}: {t.description} 参数: {json.dumps(t.parameters, ensure_ascii=False)}"
        for t in registry.list()
    )
    degrade_note = ""
    if degrade_level >= 2:
        degrade_note = "\n当前系统处于二级降级：禁止调用工具，直接给出保守回答。"
    return (
        "你是部署在 Debian 个人服务器上的 AI 运维助手 TG-Agent。\n"
        "你可以调用以下工具获取真实数据或执行操作：\n"
        f"{tools}\n"
        "规则：\n"
        "1. 输出必须是合法 JSON，且只能是两种格式之一：\n"
        '   {"action": {"name": "<工具名>", "arguments": {...}}} 表示调用工具\n'
        '   {"answer": "最终回答"} 表示给出最终答案\n'
        "2. 需要数据时先调用工具，根据观察结果继续；信息足够后输出最终答案。\n"
        "3. 回答使用中文，简洁直接。\n"
        "4. 重启/关机是危险操作：调用 reboot/shutdown 工具时 confirm 必须为 false，"
        "并提示用户发送 /reboot 或 /shutdown 二次确认。\n"
        "5. 禁止编造系统数据；工具不可用时如实说明。" + degrade_note
    )


class Agent:
    def __init__(self, config: AppConfig, llm: LLMClient, registry: ToolRegistry, db: AgentDB, monitor: Monitor) -> None:
        self.config = config
        self.llm = llm
        self.registry = registry
        self.db = db
        self.monitor = monitor

    # ------------------------------------------------------------------
    async def handle(self, user_id: int, chat_id: int, text: str, pure: bool = False) -> str:
        """AI 入口。``pure=True`` 时为纯聊天模式：不调用工具、不解析命令。"""
        if pure:
            messages: List[Dict[str, str]] = [
                {
                    "role": "system",
                    "content": "你是 TG-Agent，一个友善的中文 AI 助手。当前为纯 AI 对话模式："
                    "只聊天，不调用任何工具，不执行任何命令。请直接、自然地回答用户的问题。",
                },
                {"role": "user", "content": text},
            ]
            return await self.llm.complete(messages, temperature=self.config.llm_temperature)

        if self.monitor.degrade >= 2:
            return "⚠️ 系统处于二级降级模式，已禁用工具调用。请稍后再试或使用 /status 查看基础状态。"

        messages = [
            {"role": "system", "content": build_system_prompt(self.registry, self.monitor.degrade)},
            {"role": "user", "content": text},
        ]
        for _ in range(self.config.llm_max_steps):
            content = await self.llm.complete(messages, temperature=self.config.llm_temperature)
            kind, value, args = self._parse_response(content)
            if kind == "answer":
                return value
            name = value
            logger.info("tool call: %s %s", name, args)
            observation = await self.registry.call(name, chat_id, args)
            observation = observation[:2000]
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {"role": "user", "content": f"观察结果:\n{observation}\n\n根据观察继续。如果需要更多信息请再次调用工具，否则输出最终答案。"}
            )
        return "⚠️ 已达到最大步骤数，请简化问题或直接使用斜杠命令。"

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_response(content: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
        """Returns (kind, value, args); kind is 'answer' or 'action'."""
        text = content.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fence:
            text = fence.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return "answer", content, {}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return "answer", content, {}

        if not isinstance(data, dict):
            return "answer", content, {}
        if "answer" in data:
            return "answer", str(data["answer"]), {}
        action = data.get("action")
        if isinstance(action, dict):
            name = action.get("name")
            args = action.get("arguments") or {}
            if name:
                return "action", str(name), args if isinstance(args, dict) else {}
        if "name" in data and "arguments" in data:
            return "action", str(data["name"]), data["arguments"] if isinstance(data["arguments"], dict) else {}
        if "tool" in data:
            name = data["tool"] if isinstance(data["tool"], str) else data["tool"].get("name")
            args = data.get("arguments") or data.get("tool", {}).get("arguments") or {}
            if name:
                return "action", str(name), args if isinstance(args, dict) else {}
        return "answer", content, {}

    # ------------------------------------------------------------------
    async def handle_command(self, cmd: str, arg_text: str, chat_id: int) -> str:
        """Direct routing for slash commands (fast path, no LLM required)."""
        cmd = cmd.lower().split("@")[0]
        mapping = {
            "start": "help",
            "help": "help",
            "status": "system_status",
            "ports": "ports",
            "mihomo": "mihomo_nodes",
            "backup": "backup",
        }
        if cmd in mapping:
            return await self.registry.call(mapping[cmd], chat_id, {})
        if cmd == "switch":
            return await self.registry.call("mihomo_switch", chat_id, {"name": arg_text})
        if cmd == "wake":
            mac = arg_text.strip() or self.config.wol_mac
            return await self.registry.call("wol", chat_id, {"mac": mac})
        if cmd == "watchdog":
            return await self.registry.call("watchdog", chat_id, {"action": arg_text.strip() or "status"})
        if cmd in ("reboot", "shutdown"):
            return await self.registry.call(cmd, chat_id, {"confirm": True})
        return f"❌ 未知命令: /{cmd}\n发送 /help 查看可用命令。"
