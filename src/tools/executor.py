"""Async subprocess executor: timeouts, PGID kills, byte/rate limits, sudo whitelist."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.config import AppConfig
from src.logging_setup import AuditLogger


class ExecError(RuntimeError):
    """Base class for executor errors."""


class RateLimitError(ExecError):
    """Too many executions within the sliding window."""


class CommandDeniedError(ExecError):
    """Command was denied by the allowlist / sudo whitelist."""


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    cmd: str

    def combined(self, limit: int = 4000) -> str:
        out = self.stdout.strip()
        if self.stderr.strip():
            out = f"{out}\n[stderr] {self.stderr.strip()}" if out else self.stderr.strip()
        if self.timed_out:
            out = f"{out}\n[超时终止]" if out else "[超时终止]"
        if self.truncated:
            out = f"{out}\n[输出过长已截断]" if out else "[输出过长已截断]"
        return out[:limit]


class Executor:
    """Run commands with resource and policy limits."""

    def __init__(self, config: AppConfig, audit: Optional[AuditLogger] = None) -> None:
        self.config = config
        self.audit = audit or AuditLogger(config.log_dir)
        self._call_times: deque[float] = deque()
        self._lock = asyncio.Lock()
        self.pgid_file = Path(config.data_dir) / "subprocesses.pid"

    # ------------------------------------------------------------------
    async def check_rate(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._call_times and now - self._call_times[0] > 60:
                self._call_times.popleft()
            if len(self._call_times) >= self.config.exec_calls_per_min:
                raise RateLimitError("命令执行频率超限，请稍后再试")
            self._call_times.append(now)

    # ------------------------------------------------------------------
    async def run(
        self,
        argv: Sequence[str],
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        nice: int = 0,
        ionice: Optional[str] = None,
        max_output_bytes: Optional[int] = None,
    ) -> ExecResult:
        """Execute ``argv`` as a subprocess with limits."""
        argv = [str(a) for a in argv]
        if not argv:
            raise ExecError("空命令")
        timeout = min(timeout or self.config.exec_default_timeout, self.config.exec_max_timeout)
        max_output_bytes = max_output_bytes or self.config.exec_max_output_bytes
        await self.check_rate()

        kwargs: Dict = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
            if nice:
                kwargs["preexec_fn"] = lambda: os.nice(int(nice))

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            **kwargs,
        )

        pgid = process.pid if sys.platform != "win32" else None
        if pgid is not None:
            self._record_pgid(pgid, argv)
            if ionice:
                # best-effort idle I/O class for the child process group leader
                try:
                    await asyncio.create_subprocess_exec(
                        "ionice", "-c", str(ionice), "-p", str(process.pid),
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                except OSError:
                    pass

        async def _reader(stream, limit: int) -> tuple[bytes, bool]:
            chunks: List[bytes] = []
            total = 0
            truncated = False
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                total += len(chunk)
                if total <= limit:
                    chunks.append(chunk)
                else:
                    truncated = True
            return b"".join(chunks), truncated

        out_task = asyncio.create_task(_reader(process.stdout, max_output_bytes))
        err_task = asyncio.create_task(_reader(process.stderr, max_output_bytes))

        timed_out = False
        try:
            returncode = await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await self._kill_group(process)
            returncode = await process.wait()

        stdout, out_trunc = await out_task
        stderr, err_trunc = await err_task
        self._prune_pgids()
        return ExecResult(
            returncode=returncode,
            stdout=stdout.decode("utf-8", errors="replace").strip(),
            stderr=stderr.decode("utf-8", errors="replace").strip(),
            timed_out=timed_out,
            truncated=out_trunc or err_trunc,
            cmd=shlex.join(argv),
        )

    # ------------------------------------------------------------------
    async def _kill_group(self, process: asyncio.subprocess.Process) -> None:
        if sys.platform == "win32":
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    # ------------------------------------------------------------------
    def _matches_whitelist(self, argv: Sequence[str]) -> bool:
        return any(list(argv) == list(entry) for entry in self.config.sudo_whitelist)

    def _matches_whitelist_realpath(self, argv: Sequence[str]) -> bool:
        """Second assertion: every absolute path must resolve to the whitelisted path."""
        for entry in self.config.sudo_whitelist:
            if len(entry) != len(argv):
                continue
            ok = True
            for expected, actual in zip(entry, argv):
                if str(actual).startswith("/") and str(expected).startswith("/"):
                    exp_real = os.path.realpath(expected)
                    act_real = os.path.realpath(actual)
                    if exp_real != act_real:
                        ok = False
                        break
                elif expected != actual:
                    ok = False
                    break
            if ok:
                return True
        return False

    async def run_sudo(
        self,
        argv: Sequence[str],
        user: str = "",
        timeout: Optional[int] = None,
    ) -> ExecResult:
        """Run a command via ``sudo -n`` only when it matches the dead whitelist exactly."""
        argv = [str(a) for a in argv]
        if sys.platform == "win32":
            self.audit.write(list(argv), False, user, "platform: windows unsupported")
            raise CommandDeniedError("sudo 仅支持 Linux")
        if not self._matches_whitelist(argv) or not self._matches_whitelist_realpath(argv):
            self.audit.write(list(argv), False, user, "whitelist mismatch")
            raise CommandDeniedError("命令不在 sudo 白名单中")
        self.audit.write(list(argv), True, user, "sudo -n")
        return await self.run(["sudo", "-n", *argv], timeout=timeout)

    # ------------------------------------------------------------------
    def _record_pgid(self, pgid: int, argv: Sequence[str]) -> None:
        try:
            with open(self.pgid_file, "a", encoding="utf-8") as fh:
                fh.write(f"{pgid} {int(time.time())} {shlex.join(argv)}\n")
        except OSError:
            pass

    def _prune_pgids(self) -> None:
        try:
            if not self.pgid_file.exists():
                return
            alive: List[str] = []
            for line in self.pgid_file.read_text(encoding="utf-8").splitlines():
                parts = line.split(" ", 2)
                if not parts:
                    continue
                try:
                    pid = int(parts[0])
                    os.kill(pid, 0)
                    alive.append(line)
                except (OSError, ValueError):
                    continue
            self.pgid_file.write_text("\n".join(alive) + ("\n" if alive else ""), encoding="utf-8")
        except OSError:
            pass

    def active_pgids(self) -> List[int]:
        result: List[int] = []
        try:
            if self.pgid_file.exists():
                for line in self.pgid_file.read_text(encoding="utf-8").splitlines():
                    pid = int(line.split(" ", 1)[0])
                    try:
                        os.kill(pid, 0)
                        result.append(pid)
                    except (OSError, ValueError):
                        continue
        except (OSError, ValueError):
            pass
        return result

    def kill_active(self) -> int:
        """SIGKILL every tracked process group. Returns the number signalled."""
        killed = 0
        for pgid in self.active_pgids():
            try:
                if sys.platform == "win32":
                    os.kill(pgid, signal.SIGTERM)
                else:
                    os.killpg(pgid, signal.SIGKILL)
                killed += 1
            except (OSError, ValueError):
                continue
        try:
            if self.pgid_file.exists():
                self.pgid_file.unlink()
        except OSError:
            pass
        return killed

    def check_allowlist(self, argv: Sequence[str]) -> bool:
        """Whether a non-privileged command is on the read-only allowlist."""
        return bool(argv) and argv[0] in self.config.exec_allowlist
