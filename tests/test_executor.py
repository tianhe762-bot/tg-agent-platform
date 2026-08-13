import sys

import pytest

from src.config import AppConfig
from src.tools.executor import CommandDeniedError, Executor, RateLimitError


def make_executor(tmp_path, **overrides):
    config = AppConfig()
    config.data_dir = tmp_path
    config.log_dir = tmp_path / "logs"
    for key, value in overrides.items():
        setattr(config, key, value)
    return Executor(config)


@pytest.mark.asyncio
async def test_run_echo(tmp_path):
    ex = make_executor(tmp_path)
    result = await ex.run([sys.executable, "-c", "print('hello world')"])
    assert result.returncode == 0
    assert "hello world" in result.stdout


@pytest.mark.asyncio
async def test_timeout_kills(tmp_path):
    ex = make_executor(tmp_path, exec_default_timeout=60)
    result = await ex.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert result.timed_out is True
    assert result.returncode != 0


@pytest.mark.asyncio
async def test_rate_limit(tmp_path):
    ex = make_executor(tmp_path, exec_calls_per_min=2)
    await ex.run([sys.executable, "-c", "print(1)"])
    await ex.run([sys.executable, "-c", "print(2)"])
    with pytest.raises(RateLimitError):
        await ex.run([sys.executable, "-c", "print(3)"])


@pytest.mark.asyncio
async def test_output_truncated(tmp_path):
    ex = make_executor(tmp_path, exec_max_output_bytes=100)
    result = await ex.run([sys.executable, "-c", "print('x' * 10000)"])
    assert result.truncated is True


@pytest.mark.asyncio
async def test_sudo_denied_off_whitelist(tmp_path):
    ex = make_executor(tmp_path)
    with pytest.raises(CommandDeniedError):
        await ex.run_sudo(["/bin/rm", "-rf", "/"])


def test_allowlist(tmp_path):
    ex = make_executor(tmp_path)
    assert ex.check_allowlist(["df", "-h"])
    assert not ex.check_allowlist(["rm", "-rf", "/"])
