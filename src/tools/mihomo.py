"""Mihomo (Clash) external-controller client, ported from the bash platform."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import quote

import aiohttp

logger = logging.getLogger("tg-agent.mihomo")

GROUP_TYPES = {
    "Selector", "URLTest", "Fallback", "LoadBalance", "Relay",
    "Direct", "Reject", "RejectDrop", "Compatible", "Pass", "GLOBAL",
}
BUILTIN = {"GLOBAL", "DIRECT", "REJECT", "REJECT-DROP", "PASS"}
MAX_CHAIN_DEPTH = 10


class MihomoError(RuntimeError):
    pass


HttpGet = Callable[[str], Any]


class MihomoClient:
    def __init__(
        self,
        base_url: str,
        test_url: str = "https://www.gstatic.com/generate_204",
        delay_max: int = 800,
        delay_timeout: int = 3000,
        node_limit: int = 80,
        http_get: Optional[HttpGet] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.test_url = test_url
        self.delay_max = delay_max
        self.delay_timeout = delay_timeout
        self.node_limit = node_limit
        self._http_get = http_get

    # ------------------------------------------------------------------
    async def _get(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        if self._http_get is not None:
            return await self._http_get(url)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def _put(self, path: str, payload: Dict[str, str]) -> int:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.base_url}{path}", json=payload, timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    return resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return 0

    async def _delete(self, path: str) -> int:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(f"{self.base_url}{path}", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    return resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return 0

    async def proxies(self) -> Optional[Dict[str, Any]]:
        return await self._get("/proxies")

    async def connections(self) -> List[Dict[str, Any]]:
        data = await self._get("/connections")
        if not data:
            return []
        return data.get("connections", []) or []

    # ------------------------------------------------------------------
    @staticmethod
    def leaf_nodes(proxies: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for key, value in (proxies.get("proxies") or {}).items():
            if value.get("type") not in GROUP_TYPES:
                out.append(key)
        return list(dict.fromkeys(out))

    @staticmethod
    def main_group(proxies: Dict[str, Any]) -> Optional[str]:
        best: Optional[tuple[int, int, int, str]] = None
        for key, value in (proxies.get("proxies") or {}).items():
            if key in BUILTIN or value.get("type") not in {"Selector", "URLTest", "Fallback", "LoadBalance"}:
                continue
            all_ = value.get("all") or []
            leaves = sum(1 for n in all_ if (proxies["proxies"].get(n, {}).get("type")) not in GROUP_TYPES)
            groups = sum(1 for n in all_ if (proxies["proxies"].get(n, {}).get("type")) in GROUP_TYPES)
            score = (0 if value.get("type") == "Selector" else 1, -groups, -leaves, key)
            if best is None or score < best:
                best = score
        return best[3] if best else None

    @staticmethod
    def resolve_leaf(proxies: Dict[str, Any], name: str) -> str:
        current = name
        for _ in range(MAX_CHAIN_DEPTH):
            value = (proxies.get("proxies") or {}).get(current) or {}
            if value.get("type") in {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay", "Compatible"}:
                nxt = value.get("now")
                if not nxt:
                    break
                current = nxt
            else:
                break
        return current

    @staticmethod
    def real_node(proxies: Dict[str, Any], connections: Sequence[Dict[str, Any]]) -> Optional[str]:
        latest: Optional[tuple[float, str]] = None
        for conn in connections:
            chains = conn.get("chains") or []
            if not chains:
                continue
            node = chains[0]
            if node in BUILTIN:
                continue
            start = conn.get("start") or 0
            if latest is None or start > latest[0]:
                latest = (start, node)
        if latest:
            return latest[1]
        for conn in connections:
            chains = conn.get("chains") or []
            if chains and chains[0] not in BUILTIN:
                return chains[0]
        return None

    def current_node(self, proxies: Dict[str, Any], connections: Sequence[Dict[str, Any]]) -> Optional[str]:
        node = self.real_node(proxies, connections)
        if node:
            return node
        main = self.main_group(proxies)
        if not main:
            return None
        now = (proxies.get("proxies") or {}).get(main, {}).get("now")
        return self.resolve_leaf(proxies, now) if now else None

    # ------------------------------------------------------------------
    @staticmethod
    def _uri(name: str) -> str:
        return quote(name, safe="")

    async def delay(self, name: str) -> Optional[int]:
        path = f"/proxies/{self._uri(name)}/delay"
        url = f"{self.base_url}{path}?url={quote(self.test_url, safe='')}&timeout={self.delay_timeout}"
        if self._http_get is not None:
            data = await self._http_get(url)
            if isinstance(data, dict):
                return data.get("delay")
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)
                    return data.get("delay")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    # ------------------------------------------------------------------
    async def nodes_text(self) -> str:
        proxies = await self.proxies()
        if not proxies:
            return "❌ Mihomo 未开启或无法连接"
        conns = await self.connections()
        current = self.current_node(proxies, conns)
        leaves = self.leaf_nodes(proxies)

        delays: Dict[str, int] = {}
        sem = asyncio.Semaphore(20)

        async def probe(name: str) -> None:
            async with sem:
                d = await self.delay(name)
                if d is not None and d <= self.delay_max:
                    delays[name] = d

        await asyncio.gather(*(probe(n) for n in leaves))
        usable = sorted(delays.items(), key=lambda kv: kv[1])
        if not usable:
            return "🚀 Mihomo 可用节点（0 个）\n\n当前没有可用节点"

        lines = [f"🚀 Mihomo 可用节点（{len(usable)} 个）\n"]
        for name, d in usable[: self.node_limit]:
            mark = "▶" if name == current else "•"
            lines.append(f"{mark} {name} — {d}ms")
        if len(usable) > self.node_limit:
            lines.append(f"…共 {len(usable)} 个可用节点")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    async def switch(self, name: str) -> str:
        proxies = await self.proxies()
        if not proxies:
            return "❌ Mihomo 未开启，无法切换节点"
        leaves = self.leaf_nodes(proxies)

        # exact match
        if name in leaves:
            d = await self.delay(name)
            if d is None or d > self.delay_max:
                return "❌ 该节点当前不可用（延迟超过 800ms 或无法连通），请从 /mihomo 可用列表中选择"
            target = name
        else:
            lower = name.lower()
            matches = [k for k in leaves if lower in k.lower()]
            if not matches:
                return "❌ 未找到可用的匹配节点，请从 /mihomo 复制完整节点名（含国旗前缀）"
            delays = {}
            for m in matches:
                d = await self.delay(m)
                if d is not None and d <= self.delay_max:
                    delays[m] = d
            if not delays:
                return "❌ 未找到可用的匹配节点（匹配到的节点当前都不可用）"
            if len(delays) > 1:
                names = "\n".join(f"• {m} — {d}ms" for m, d in sorted(delays.items(), key=lambda kv: kv[1])[:10])
                first = sorted(delays, key=lambda m: delays[m])[0]
                return f"❓ 找到多个匹配节点，请发送完整节点名：\n\n{names}\n\n例如:\n/switch {first}"
            target = next(iter(delays))

        group = self._switch_group(proxies, target)
        if not group:
            auto = self._auto_group(proxies, target)
            if auto:
                return "❌ 该节点位于自动选择组，无法手动切换"
            return "❌ 未找到该节点，请从 /mihomo 复制完整节点名"

        code = await self._put(f"/proxies/{self._uri(group)}", {"name": target})
        if code not in (200, 204):
            return f"❌ 切换失败（HTTP {code}）：请确认节点名称是否正确"

        route = await self._route_main_to(proxies, group)
        await self._delete("/connections")

        fresh = await self.proxies() or proxies
        effective_group = route or group
        effective = (fresh.get("proxies") or {}).get(effective_group, {}).get("now") or effective_group
        effective = self.resolve_leaf(fresh, effective)
        d = await self.delay(target)
        delay_txt = f"{d}ms" if d is not None else "超时"
        if effective == target:
            return f"✅ 已切换至: {target}\n📶 当前延迟: {delay_txt}"
        return f"⚠️ 已发送切换（{target}），但当前生效节点仍是 {effective}（可能被自动选择/分流规则接管）"

    def _switch_group(self, proxies: Dict[str, Any], name: str) -> Optional[str]:
        best: Optional[tuple[int, str]] = None
        for key, value in (proxies.get("proxies") or {}).items():
            if key in BUILTIN or value.get("type") != "Selector":
                continue
            all_ = value.get("all") or []
            if name not in all_:
                continue
            leaves = sum(1 for n in all_ if (proxies["proxies"].get(n, {}).get("type")) not in GROUP_TYPES)
            score = (-leaves, key)
            if best is None or score < best:
                best = score
        return best[1] if best else None

    def _auto_group(self, proxies: Dict[str, Any], name: str) -> Optional[str]:
        for key, value in (proxies.get("proxies") or {}).items():
            if key in BUILTIN or value.get("type") not in {"URLTest", "Fallback", "LoadBalance", "Relay"}:
                continue
            if name in (value.get("all") or []):
                return key
        return None

    async def _route_main_to(self, proxies: Dict[str, Any], group: str) -> Optional[str]:
        conns = await self.connections()
        route: Optional[str] = None
        for conn in conns:
            chains = conn.get("chains") or []
            for g in chains:
                if g == group:
                    continue
                value = (proxies.get("proxies") or {}).get(g) or {}
                if value.get("type") == "Selector" and group in (value.get("all") or []):
                    route = g
                    break
            if route:
                break
        if not route:
            main = self.main_group(proxies)
            if main and main != group:
                value = (proxies.get("proxies") or {}).get(main) or {}
                if value.get("type") == "Selector" and group in (value.get("all") or []):
                    route = main
        if not route:
            return None
        code = await self._put(f"/proxies/{self._uri(route)}", {"name": group})
        return route if code in (200, 204) else None
