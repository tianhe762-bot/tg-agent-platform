import asyncio
import hashlib
import time

import pytest

from src.tools.db import AgentDB, vacuum_decision


def test_vacuum_decision():
    now = time.time()
    assert not vacuum_decision(10, 100, 60 * 1024 * 1024, now, now)  # ratio low
    assert not vacuum_decision(40, 100, 10 * 1024 * 1024, now, now)  # size low
    assert not vacuum_decision(40, 100, 60 * 1024 * 1024, now, now)  # done this week
    assert vacuum_decision(40, 100, 60 * 1024 * 1024, now - 8 * 86400, now)
    assert vacuum_decision(40, 100, 60 * 1024 * 1024, 0, now, force=True)


@pytest.mark.asyncio
async def test_critical_write_and_query(tmp_path):
    db = AgentDB(tmp_path / "test.db")
    await db.init()
    await db.kv_set("foo", "bar")
    assert await db.kv_get("foo") == "bar"
    await db.record_task("test", "SUCCESS", "ok")
    counts = await db.counts()
    assert counts["tasks"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_tiered_bulk_write(tmp_path):
    db = AgentDB(tmp_path / "test.db", flush_interval=0.2)
    await db.init()
    for i in range(10):
        await db.enqueue_bulk(
            "INSERT INTO metrics(ts, load1, mem_avail_mb, io_wait_ms, disk_free_mb) VALUES(?,?,?,?,?)",
            (i, 0.1, 100, 1, 100),
        )
    assert db._bulk.qsize() == 10
    flushed = await db.flush_bulk()
    assert flushed == 10
    rows = await db.query("SELECT COUNT(*) AS n FROM metrics")
    assert rows[0]["n"] == 10
    await db.close()


@pytest.mark.asyncio
async def test_bulk_overflow_drops(tmp_path):
    db = AgentDB(tmp_path / "test.db", bulk_max=3)
    await db.init()
    for i in range(6):
        await db.enqueue_bulk(
            "INSERT INTO metrics(ts, load1, mem_avail_mb, io_wait_ms, disk_free_mb) VALUES(?,?,?,?,?)",
            (i, 0.1, 100, 1, 100),
        )
    assert db._bulk.qsize() <= 3
    assert db.dropped_bulk > 0
    await db.close()


@pytest.mark.asyncio
async def test_backup_verified(tmp_path):
    db = AgentDB(tmp_path / "test.db")
    await db.init()
    await db.kv_set("key", "value")
    dest_dir = tmp_path / "backup"
    dest = await db.backup(dest_dir=dest_dir, retries=1)
    assert dest.exists()
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    sha_file = dest_dir / f"{dest.name}.sha256"
    assert digest in sha_file.read_text(encoding="utf-8")
    await db.close()


@pytest.mark.asyncio
async def test_flush_loop_auto(tmp_path):
    db = AgentDB(tmp_path / "test.db", flush_interval=0.1)
    await db.init()
    await db.enqueue_bulk("INSERT INTO metrics(ts, load1, mem_avail_mb, io_wait_ms, disk_free_mb) VALUES(?,?,?,?,?)", (1, 0.1, 100, 1, 100))
    await asyncio.sleep(0.4)
    rows = await db.query("SELECT COUNT(*) AS n FROM metrics")
    assert rows[0]["n"] == 1
    await db.close()
