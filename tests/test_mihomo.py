import pytest

from src.tools.mihomo import MihomoClient


PROXIES = {
    "proxies": {
        "GLOBAL": {"type": "Selector", "now": "主组"},
        "主组": {"type": "Selector", "now": "子组", "all": ["子组", "leaf1", "leaf2"]},
        "子组": {"type": "Selector", "now": "leaf1", "all": ["leaf1", "leaf2"]},
        "leaf1": {"type": "http"},
        "leaf2": {"type": "http"},
        "自动组": {"type": "URLTest", "now": "leaf2", "all": ["leaf1", "leaf2"]},
        "REJECT": {"type": "Reject"},
    }
}

CONNECTIONS = [{"chains": ["leaf2", "主组", "GLOBAL"], "start": 100.0}]


def make_client(fake_get=None):
    return MihomoClient("http://mihomo.test", http_get=fake_get)


def test_leaf_nodes_and_groups():
    client = make_client()
    leaves = client.leaf_nodes(PROXIES)
    assert leaves == ["leaf1", "leaf2"]
    assert client.main_group(PROXIES) == "主组"
    assert client.resolve_leaf(PROXIES, "主组") == "leaf1"
    assert client.real_node(PROXIES, CONNECTIONS) == "leaf2"
    assert client.current_node(PROXIES, CONNECTIONS) == "leaf2"
    assert client._switch_group(PROXIES, "leaf1") == "主组"
    assert client._auto_group(PROXIES, "leaf1") == "自动组"


@pytest.mark.asyncio
async def test_nodes_text():
    calls = []

    async def fake_get(url):
        calls.append(url)
        if url.endswith("/proxies"):
            return PROXIES
        if url.endswith("/connections"):
            return {"connections": CONNECTIONS}
        if "/delay" in url:
            if "leaf1" in url:
                return {"delay": 100}
            return {"delay": 900}
        return None

    client = make_client(fake_get)
    text = await client.nodes_text()
    assert "Mihomo 可用节点（1 个）" in text
    assert "leaf1 — 100ms" in text
    assert "leaf2" not in text


@pytest.mark.asyncio
async def test_switch_success():
    puts = []
    deletes = []

    async def fake_get(url):
        if url.endswith("/proxies"):
            return PROXIES
        if url.endswith("/connections"):
            return {"connections": CONNECTIONS}
        if "/delay" in url:
            return {"delay": 120}
        return None

    client = make_client(fake_get)

    async def fake_put(path, payload):
        puts.append((path, payload))
        return 204

    async def fake_delete(path):
        deletes.append(path)
        return 204

    client._put = fake_put
    client._delete = fake_delete
    text = await client.switch("leaf1")
    assert "已切换至: leaf1" in text
    assert any(path == "/proxies/%E4%B8%BB%E7%BB%84" for path, _ in puts)
    assert deletes == ["/connections"]


@pytest.mark.asyncio
async def test_switch_fuzzy_and_unavailable():
    async def fake_get(url):
        if url.endswith("/proxies"):
            return PROXIES
        if url.endswith("/connections"):
            return {"connections": []}
        return {"delay": 9999}

    client = make_client(fake_get)
    text = await client.switch("leaf")
    assert "不可用" in text


@pytest.mark.asyncio
async def test_switch_not_found():
    async def fake_get(url):
        if url.endswith("/proxies"):
            return PROXIES
        return None

    client = make_client(fake_get)
    text = await client.switch("不存在的节点")
    assert "未找到" in text
