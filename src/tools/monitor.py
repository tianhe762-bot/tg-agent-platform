"""System sampling, three-dimensional degradation and alert dedup/aggregation."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

import psutil

from src.config import AppConfig
from src.tools.db import AgentDB

logger = logging.getLogger("tg-agent.monitor")


@dataclass
class SystemSample:
    load1: float
    mem_avail_mb: float
    io_wait_ms: float
    cpu_percent: float
    disk_free_mb: float


class SystemSampler:
    """Cross-platform sampler; uses /proc on Linux for precise I/O wait."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._prev_io_time: Optional[int] = None
        self._prev_ts: Optional[float] = None
        self._prev_cpu: Optional[tuple[int, int]] = None

    async def sample(self) -> SystemSample:
        try:
            load1 = float(os.getloadavg()[0])
        except (OSError, AttributeError, IndexError):
            load1 = 0.0
        mem = psutil.virtual_memory()
        mem_avail_mb = mem.available / (1024 * 1024)
        now = time.monotonic()
        io_wait = self._io_wait_ms(now)
        cpu = self._cpu_percent()
        try:
            disk_free_mb = psutil.disk_usage(str(self.data_dir)).free / (1024 * 1024)
        except OSError:
            disk_free_mb = 0.0
        return SystemSample(load1=load1, mem_avail_mb=mem_avail_mb, io_wait_ms=io_wait, cpu_percent=cpu, disk_free_mb=disk_free_mb)

    def _io_wait_ms(self, now: float) -> float:
        if not os.path.exists("/proc/diskstats"):
            return 0.0
        total = 0
        try:
            with open("/proc/diskstats", "r", encoding="utf-8") as fh:
                for line in fh:
                    fields = line.split()
                    if len(fields) >= 14:
                        total += int(fields[12])
        except (OSError, ValueError, IndexError):
            return 0.0
        if self._prev_io_time is None or self._prev_ts is None:
            self._prev_io_time, self._prev_ts = total, now
            return 0.0
        elapsed = now - self._prev_ts
        rate = (total - self._prev_io_time) / elapsed if elapsed > 0 else 0.0
        self._prev_io_time, self._prev_ts = total, now
        return max(0.0, rate)

    def _cpu_percent(self) -> float:
        if os.path.exists("/proc/stat"):
            try:
                with open("/proc/stat", "r", encoding="utf-8") as fh:
                    parts = fh.readline().split()
                nums = [int(x) for x in parts[1:9]]
                total = sum(nums)
                idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
                if self._prev_cpu is not None:
                    pt, pi = self._prev_cpu
                    dt = total - pt
                    if dt > 0:
                        self._prev_cpu = (total, idle)
                        return max(0.0, min(100.0, (dt - (idle - pi)) / dt * 100.0))
                self._prev_cpu = (total, idle)
                return 0.0
            except (OSError, ValueError, IndexError):
                pass
        return float(psutil.cpu_percent(interval=None))


class DegradeEvaluator:
    """Hysteresis state machine: level 0 / 1 / 2 based on load, memory and I/O wait."""

    def __init__(self, config: AppConfig) -> None:
        self.cfg = config

    def evaluate(self, sample: SystemSample, current: int) -> int:
        cfg = self.cfg
        load_high1 = sample.load1 >= cfg.degrade1_load
        mem_low1 = sample.mem_avail_mb <= cfg.degrade1_mem_gb * 1024
        io_high1 = sample.io_wait_ms >= cfg.degrade1_io_ms
        load_high2 = sample.load1 >= cfg.degrade2_load
        mem_low2 = sample.mem_avail_mb <= cfg.degrade2_mem_gb * 1024
        io_high2 = sample.io_wait_ms >= cfg.degrade2_io_ms

        recovered = (
            sample.load1 < cfg.recover_load
            and sample.mem_avail_mb > cfg.recover_mem_gb * 1024
            and sample.io_wait_ms < cfg.recover_io_ms
        )
        recovered2 = (
            sample.load1 < cfg.recover2_load
            and sample.mem_avail_mb > cfg.recover2_mem_gb * 1024
            and sample.io_wait_ms < cfg.recover2_io_ms
        )

        if current >= 2:
            return 1 if recovered2 else 2
        if current == 1:
            if load_high2 or mem_low2 or io_high2:
                return 2
            return 0 if recovered else 1
        return 1 if (load_high1 or mem_low1 or io_high1) else 0


class AlertCenter:
    """Alert dedup (fingerprint + cooldown) and 5-second window aggregation."""

    def __init__(self, config: AppConfig, db: AgentDB, send_cb: Callable[[str], Awaitable[None]]) -> None:
        self.config = config
        self.db = db
        self.send_cb = send_cb
        self._pending: Dict[str, List[tuple[float, str]]] = {}

    @staticmethod
    def fingerprint(kind: str, message: str) -> str:
        return hashlib.sha1(f"{kind}|{message}".encode("utf-8")).hexdigest()

    async def emit(self, kind: str, message: str) -> bool:
        fp = self.fingerprint(kind, message)
        cooldown = self.config.alert_cooldown_min * 60
        row = await self.db.query_one(
            "SELECT 1 FROM events WHERE fingerprint=? AND ts > ? LIMIT 1",
            (fp, time.time() - cooldown),
        )
        if row:
            return False
        await self.db.execute(
            "INSERT INTO events(kind, message, fingerprint, ts) VALUES(?,?,?,?)",
            (kind, message, fp, time.time()),
        )
        self._pending.setdefault(kind, []).append((time.time(), message))
        return True

    async def flush(self) -> None:
        now = time.time()
        for kind, items in list(self._pending.items()):
            recent = [msg for ts, msg in items if now - ts <= 5]
            if not recent:
                self._pending.pop(kind, None)
                continue
            if len(recent) == 1:
                text = f"⚠️ 系统告警\n{recent[0]}"
            else:
                text = f"⚠️ 系统告警（{len(recent)} 条，已聚合）\n" + "\n".join(f"• {m}" for m in recent)
            try:
                await self.send_cb(text)
            except Exception:
                logger.exception("alert send failed")
            self._pending.pop(kind, None)


class Monitor:
    """Periodic sampling, degradation tracking, alerting and heartbeat."""

    def __init__(
        self,
        config: AppConfig,
        db: AgentDB,
        send_cb: Optional[Callable[[str], Awaitable[None]]] = None,
        sampler: Optional[SystemSampler] = None,
    ) -> None:
        self.config = config
        self.db = db
        self.sampler = sampler or SystemSampler(config.data_dir)
        self.evaluator = DegradeEvaluator(config)
        self.degrade = 0
        self.alerts = AlertCenter(config, db, send_cb or (lambda text: _noop(text)))
        self._last_tick: float = 0.0
        self._tick_ok = True

    async def tick(self) -> SystemSample:
        sample = await self.sampler.sample()
        self.degrade = self.evaluator.evaluate(sample, self.degrade)
        await self.db.kv_set("degrade_level", str(self.degrade))
        await self.db.enqueue_bulk(
            "INSERT INTO metrics(ts, load1, mem_avail_mb, io_wait_ms, disk_free_mb) VALUES(?,?,?,?,?)",
            (time.time(), sample.load1, sample.mem_avail_mb, sample.io_wait_ms, sample.disk_free_mb),
        )
        await self.db.heartbeat(str(time.time()))

        if sample.disk_free_mb < self.config.disk_low_water_gb * 1024:
            await self.alerts.emit("disk", f"磁盘可用空间不足: {sample.disk_free_mb:.0f}MB（阈值 {self.config.disk_low_water_gb:.1f}GB）")
        if sample.mem_avail_mb < self.config.degrade1_mem_gb * 1024:
            await self.alerts.emit("memory", f"可用内存偏低: {sample.mem_avail_mb:.0f}MB")
        if sample.load1 >= self.config.degrade1_load:
            await self.alerts.emit("load", f"系统负载偏高: {sample.load1:.2f}（降级级别 {self.degrade}）")
        await self.alerts.flush()
        self._last_tick = time.time()
        self._tick_ok = True
        return sample

    def healthy(self, now: Optional[float] = None) -> bool:
        """False when the event loop failed to tick for longer than 2x the regular interval."""
        now = now or time.time()
        return self._last_tick > 0 and (now - self._last_tick) < self.config.watchdog_regular_s * 2


async def _noop(text: str) -> None:
    return None
