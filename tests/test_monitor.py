import pytest

from src.config import AppConfig
from src.tools.db import AgentDB
from src.tools.monitor import AlertCenter, DegradeEvaluator, Monitor, SystemSample


def sample(load=0.5, mem_mb=4096, io=10.0, disk_mb=50 * 1024):
    return SystemSample(load1=load, mem_avail_mb=mem_mb, io_wait_ms=io, cpu_percent=10.0, disk_free_mb=disk_mb)


def test_degrade_hysteresis():
    cfg = AppConfig()
    ev = DegradeEvaluator(cfg)
    assert ev.evaluate(sample(load=0.5), 0) == 0
    assert ev.evaluate(sample(load=3.0), 0) == 1
    # still high -> stays 1
    assert ev.evaluate(sample(load=2.0), 1) == 1
    # recovered below level-1 recovery threshold
    assert ev.evaluate(sample(load=1.0, mem_mb=4096, io=50), 1) == 0
    # escalate to 2
    assert ev.evaluate(sample(load=4.0), 1) == 2
    # level 2 stays when I/O wait still above level-2 recovery threshold
    assert ev.evaluate(sample(load=2.0, mem_mb=2000, io=250), 2) == 2
    # recovers to level 1 only when all level-2 recovery thresholds are met
    assert ev.evaluate(sample(load=2.0, mem_mb=2000, io=150), 2) == 1


@pytest.mark.asyncio
async def test_alert_dedup(tmp_path):
    cfg = AppConfig()
    cfg.alert_cooldown_min = 30
    db = AgentDB(tmp_path / "test.db")
    await db.init()
    sent = []

    async def send(text):
        sent.append(text)

    center = AlertCenter(cfg, db, send)
    assert await center.emit("disk", "磁盘空间不足")
    assert not await center.emit("disk", "磁盘空间不足")  # dedup within cooldown
    await center.flush()
    assert len(sent) == 1
    await db.close()


@pytest.mark.asyncio
async def test_alert_aggregation(tmp_path):
    cfg = AppConfig()
    cfg.alert_cooldown_min = 30
    db = AgentDB(tmp_path / "test.db")
    await db.init()
    sent = []

    async def send(text):
        sent.append(text)

    center = AlertCenter(cfg, db, send)
    assert await center.emit("load", "负载偏高 1")
    assert await center.emit("load", "负载偏高 2")
    await center.flush()
    assert len(sent) == 1
    assert "2 条" in sent[0]
    await db.close()


@pytest.mark.asyncio
async def test_monitor_tick_writes_metrics(tmp_path):
    cfg = AppConfig()
    cfg.data_dir = tmp_path
    db = AgentDB(tmp_path / "test.db")
    await db.init()

    class FakeSampler:
        async def sample(self):
            return sample()

    monitor = Monitor(cfg, db, sampler=FakeSampler())
    await monitor.tick()
    await db.flush_bulk()
    rows = await db.query("SELECT COUNT(*) AS n FROM metrics")
    assert rows[0]["n"] == 1
    assert monitor.degrade == 0
    assert (await db.kv_get("degrade_level")) == "0"
    await db.close()
