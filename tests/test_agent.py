import pytest

from src.agent import Agent
from src.config import AppConfig
from src.tools import Tool, ToolRegistry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, messages, temperature=0.2, max_tokens=None):
        self.calls.append(messages)
        return self.responses.pop(0)


class FakeDB:
    async def kv_set(self, *a):
        return None

    async def kv_get(self, *a):
        return None


class FakeMonitor:
    def __init__(self):
        self.degrade = 0


def make_agent(responses, registry=None):
    config = AppConfig()
    llm = FakeLLM(responses)
    registry = registry or ToolRegistry()
    return Agent(config, llm, registry, FakeDB(), FakeMonitor())


def test_parse_action():
    agent = make_agent([])
    kind, value, args = agent._parse_response('{"answer": "完成"}')
    assert (kind, value) == ("answer", "完成")
    kind, name, args = agent._parse_response('{"action": {"name": "echo", "arguments": {"x": 1}}}')
    assert (kind, name, args) == ("action", "echo", {"x": 1})
    kind, name, args = agent._parse_response('```json\n{"action": {"name": "echo", "arguments": {}}}\n```')
    assert (kind, name) == ("action", "echo")
    kind, value, args = agent._parse_response("直接回答")
    assert kind == "answer" and value == "直接回答"


@pytest.mark.asyncio
async def test_react_loop_calls_tool_then_answers():
    registry = ToolRegistry()

    async def echo(chat_id, args):
        return f"echo:{args['x']}"

    registry.register(Tool("echo", "echo tool", echo, [{"name": "x", "type": "string"}]))
    agent = make_agent(
        [
            '{"action": {"name": "echo", "arguments": {"x": "hi"}}}',
            '{"answer": "完成，回显 hi"}',
        ],
        registry,
    )
    result = await agent.handle(1, 1, "测试")
    assert result == "完成，回显 hi"
    assert len(agent.llm.calls) == 2


@pytest.mark.asyncio
async def test_degrade_level2_disables_tools():
    registry = ToolRegistry()

    async def boom(chat_id, args):
        raise AssertionError("should not be called")

    registry.register(Tool("boom", "boom", boom))
    agent = make_agent(['{"action": {"name": "boom", "arguments": {}}}'], registry)
    agent.monitor.degrade = 2
    result = await agent.handle(1, 1, "测试")
    assert "二级降级" in result
    assert agent.llm.calls == []


@pytest.mark.asyncio
async def test_pure_mode_no_tools_single_call():
    registry = ToolRegistry()

    async def boom(chat_id, args):
        raise AssertionError("pure mode must not call tools")

    registry.register(Tool("boom", "boom", boom))
    agent = make_agent(['{"action": {"name": "boom", "arguments": {}}}'], registry)
    result = await agent.handle(1, 1, "你好呀", pure=True)
    assert result == '{"action": {"name": "boom", "arguments": {}}}'
    assert len(agent.llm.calls) == 1


@pytest.mark.asyncio
async def test_handle_command_routing():
    registry = ToolRegistry()

    async def hello(chat_id, args):
        return "hello"

    registry.register(Tool("help", "help", hello))
    agent = make_agent([], registry)
    assert await agent.handle_command("help", "", 1) == "hello"
    assert "未知命令" in await agent.handle_command("nope", "", 1)
