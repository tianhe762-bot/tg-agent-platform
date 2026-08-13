import asyncio

import pytest

from src.llm import CircuitBreaker, CircuitOpenError, LLMClient


def test_circuit_breaker_transitions():
    cb = CircuitBreaker(failure_threshold=2, open_seconds=60)
    assert cb.allow_request()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"
    assert not cb.allow_request()
    cb._open_until = 0  # force expiry
    assert cb.state == "HALF_OPEN"
    assert cb.allow_request()
    assert not cb.allow_request()  # probing in flight
    cb.record_success()
    assert cb.state == "CLOSED"


@pytest.mark.asyncio
async def test_llm_success_and_429_backoff():
    calls = []

    async def fake_post(url, payload):
        calls.append((url, payload))
        if len(calls) == 1:
            return 429, {"error": {"retry_after": 0.001}}
        return 200, {"choices": [{"message": {"content": "你好"}}]}

    client = LLMClient("http://llm.test/v1", model="m", http_post=fake_post)
    out = await client.complete([{"role": "user", "content": "hi"}])
    assert out == "你好"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_llm_5xx_opens_circuit():
    async def fake_post(url, payload):
        return 500, {}

    client = LLMClient("http://llm.test/v1", model="m", http_post=fake_post)
    with pytest.raises(CircuitOpenError):
        await client.complete([{"role": "user", "content": "hi"}])
    with pytest.raises(CircuitOpenError):
        await client.complete([{"role": "user", "content": "hi"}])
    with pytest.raises(CircuitOpenError):
        await client.complete([{"role": "user", "content": "hi"}])
    assert client.circuit.state == "OPEN"


@pytest.mark.asyncio
async def test_llm_http_error():
    async def fake_post(url, payload):
        raise asyncio.TimeoutError("timeout")

    client = LLMClient("http://llm.test/v1", model="m", http_post=fake_post)
    with pytest.raises(CircuitOpenError):
        await client.complete([{"role": "user", "content": "hi"}])
