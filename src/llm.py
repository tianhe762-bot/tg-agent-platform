"""LLM client with 429 exponential backoff and a three-state circuit breaker.

Talks to any OpenAI-compatible ``/chat/completions`` endpoint over aiohttp, so it
works with OpenAI, one-api proxies, vLLM and Ollama without the official SDK.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger("tg-agent.llm")


class CircuitOpenError(RuntimeError):
    """Raised when the LLM circuit breaker is open (too many failures)."""


class CircuitBreaker:
    """Closed -> Open (2 min) -> Half-Open (1 probe) state machine."""

    def __init__(self, failure_threshold: int = 3, open_seconds: float = 120.0) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._state = "CLOSED"
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    @property
    def state(self) -> str:
        if self._state == "OPEN" and time.monotonic() >= self._open_until:
            self._state = "HALF_OPEN"
        return self._state

    def allow_request(self) -> bool:
        state = self.state
        if state == "OPEN":
            return False
        if state == "HALF_OPEN" and self._probing:
            return False
        if state == "HALF_OPEN":
            self._probing = True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._probing = False
        self._state = "CLOSED"

    def record_failure(self) -> None:
        self._probing = False
        if self._state == "HALF_OPEN":
            self._state = "OPEN"
            self._open_until = time.monotonic() + self.open_seconds
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = "OPEN"
            self._open_until = time.monotonic() + self.open_seconds
            self._failures = 0


HttpPost = Callable[[str, Dict[str, Any]], Awaitable[tuple[int, Dict[str, Any]]]]


class LLMClient:
    """OpenAI-compatible chat client with backoff + circuit breaker."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 20.0,
        max_retries: int = 3,
        circuit: Optional[CircuitBreaker] = None,
        http_post: Optional[HttpPost] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.circuit = circuit or CircuitBreaker()
        self._http_post = http_post  # injectable for tests

    async def _post(self, url: str, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        if self._http_post is not None:
            return await self._http_post(url, payload)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                data = await resp.json(content_type=None)
                return resp.status, data

    async def complete(self, messages: List[Dict[str, str]], temperature: float = 0.2, max_tokens: Optional[int] = None) -> str:
        """Returns the assistant text content. Raises CircuitOpenError when breaker is open."""
        if not self.circuit.allow_request():
            raise CircuitOpenError("LLM 熔断器已打开，请稍后再试")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                status, data = await self._post(url, payload)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                logger.warning("LLM request failed (attempt %s): %s", attempt + 1, exc)
                self.circuit.record_failure()
                raise CircuitOpenError("LLM 请求失败") from exc if self.circuit.state == "OPEN" else None

            if status == 429:
                retry_after = 1.0
                if isinstance(data, dict) and data.get("error", {}).get("retry_after"):
                    retry_after = float(data["error"]["retry_after"])
                wait = max(delay, retry_after)
                logger.warning("LLM 429, backoff %.1fs (attempt %s)", wait, attempt + 1)
                await asyncio.sleep(wait)
                delay *= 2
                continue

            if status >= 500:
                self.circuit.record_failure()
                if self.circuit.state == "OPEN":
                    raise CircuitOpenError("LLM 服务连续失败，熔断已打开")
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if status != 200:
                err = ""
                if isinstance(data, dict):
                    err = str(data.get("error", data))[:200]
                raise RuntimeError(f"LLM HTTP {status}: {err}")

            self.circuit.record_success()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError("LLM 响应格式异常") from exc
            return content or ""

        raise RuntimeError("LLM 429 重试次数用尽")
