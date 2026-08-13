"""SQLite persistence: WAL mode, tiered writes, safe VACUUM and verified backups."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import aiosqlite

logger = logging.getLogger("tg-agent.db")


def vacuum_decision(
    freelist: int,
    page_count: int,
    size_bytes: int,
    last_vacuum_ts: float,
    now: float,
    force: bool = False,
) -> bool:
    """freelist/page_count > 0.30 and file > 50MB, at most once per week."""
    if not force:
        if now - last_vacuum_ts < 7 * 24 * 3600:
            return False
    if page_count <= 0:
        return False
    ratio = freelist / page_count
    return ratio > 0.30 and size_bytes > 50 * 1024 * 1024


class AgentDB:
    """Async SQLite database with a critical/bulk tiered write path."""

    def __init__(self, path: Path, flush_interval: float = 10.0, bulk_max: int = 50) -> None:
        self.path = Path(path)
        self.flush_interval = flush_interval
        self.bulk_max = bulk_max
        self._conn: Optional[aiosqlite.Connection] = None
        self._bulk: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue(maxsize=bulk_max)
        self._bulk_bytes = 0
        self._lock = asyncio.Lock()
        self._flush_event = asyncio.Event()
        self._flush_task: Optional[asyncio.Task] = None
        self._closed = False
        self.dropped_bulk = 0

    # ------------------------------------------------------------------
    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._create_schema()
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def _create_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, user_id INTEGER, text TEXT, ts TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT, status TEXT, result TEXT,
                created_at TEXT, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT, message TEXT, fingerprint TEXT, ts REAL
            );
            CREATE INDEX IF NOT EXISTS idx_events_fp ON events(fingerprint, ts);
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, load1 REAL, mem_avail_mb REAL, io_wait_ms REAL, disk_free_mb REAL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, command TEXT, allowed INTEGER, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY, value TEXT
            );
            """
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    async def execute(self, sql: str, params: Sequence = ()) -> int:
        """Critical-path write: immediate commit + passive WAL checkpoint."""
        assert self._conn is not None
        async with self._lock:
            cur = await self._conn.execute(sql, tuple(params))
            rowid = cur.lastrowid or 0
            await cur.close()
            await self._conn.commit()
            try:
                cp = await self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                await cp.close()
            except Exception:
                pass
            return rowid

    async def query(self, sql: str, params: Sequence = ()) -> List[Dict[str, Any]]:
        assert self._conn is not None
        cur = await self._conn.execute(sql, tuple(params))
        try:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]
        finally:
            await cur.close()

    async def query_one(self, sql: str, params: Sequence = ()) -> Optional[Dict[str, Any]]:
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    async def enqueue_bulk(self, sql: str, params: Sequence = ()) -> None:
        """Non-critical write: merged, low priority. Drops when the queue overflows."""
        payload = (sql, tuple(params))
        approx = len(sql) + sum(len(str(p)) for p in params)
        try:
            self._bulk.put_nowait(payload)
            self._bulk_bytes += approx
        except asyncio.QueueFull:
            self.dropped_bulk += 1
            logger.warning("bulk write queue full, dropped metric (total=%s)", self.dropped_bulk)
            return
        if self._bulk.qsize() >= self.bulk_max or self._bulk_bytes >= 5 * 1024 * 1024:
            self._flush_event.set()

    async def _flush_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.wait_for(self._flush_event.wait(), timeout=self.flush_interval)
            except asyncio.TimeoutError:
                pass
            self._flush_event.clear()
            await self.flush_bulk()

    async def flush_bulk(self) -> int:
        """Flush the merged write queue in a single transaction. Returns row count."""
        assert self._conn is not None
        items: List[tuple[str, tuple]] = []
        while not self._bulk.empty():
            try:
                items.append(self._bulk.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not items:
            return 0
        async with self._lock:
            try:
                await self._conn.execute("BEGIN")
                for sql, params in items:
                    await self._conn.execute(sql, params)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
        self._bulk_bytes = 0
        return len(items)

    # ------------------------------------------------------------------
    async def kv_set(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def kv_get(self, key: str) -> Optional[str]:
        row = await self.query_one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else None

    async def record_message(self, chat_id: int, user_id: int, text: str, ts: str) -> None:
        await self.execute(
            "INSERT INTO messages(chat_id, user_id, text, ts) VALUES(?,?,?,?)",
            (chat_id, user_id, text, ts),
        )

    async def record_task(
        self,
        command: str,
        status: str,
        result: str = "",
        created: Optional[str] = None,
        finished: Optional[str] = None,
    ) -> None:
        await self.execute(
            "INSERT INTO tasks(command, status, result, created_at, finished_at) VALUES(?,?,?,?,?)",
            (command, status, result, created or time.strftime("%Y-%m-%d %H:%M:%S"), finished),
        )

    async def record_audit(self, ts: str, command: str, allowed: bool, detail: str) -> None:
        await self.execute(
            "INSERT INTO audit(ts, command, allowed, detail) VALUES(?,?,?,?)",
            (ts, command, 1 if allowed else 0, detail),
        )

    # ------------------------------------------------------------------
    async def maybe_vacuum(self, force: bool = False) -> bool:
        """VACUUM when fragmentation > 30% and size > 50MB (at most weekly)."""
        assert self._conn is not None
        row = await self.query_one("PRAGMA freelist_count")
        page_row = await self.query_one("PRAGMA page_count")
        freelist = int(row.get("freelist_count", 0)) if row else 0
        page_count = int(page_row.get("page_count", 0)) if page_row else 0
        size = self.path.stat().st_size if self.path.exists() else 0
        last_ts = float(await self.kv_get("last_vacuum") or 0)

        if not vacuum_decision(freelist, page_count, size, last_ts, time.time(), force=force):
            return False
        async with self._lock:
            await self._conn.execute("VACUUM")
            await self._conn.commit()
        await self.kv_set("last_vacuum", str(time.time()))
        logger.info("VACUUM executed (freelist=%s pages)", freelist)
        return True

    # ------------------------------------------------------------------
    async def backup(self, dest_dir: Optional[Path] = None, external_dir: str = "", retries: int = 2) -> Path:
        """Consistent copy via VACUUM INTO, SHA-256 verified, optional external sync."""
        assert self._conn is not None
        dest_dir = Path(dest_dir or self.path.parent / "backup")
        dest_dir.mkdir(parents=True, exist_ok=True)

        db_size = self.path.stat().st_size if self.path.exists() else 0
        free = shutil.disk_usage(dest_dir).free
        if free < db_size * 2 + 16 * 1024 * 1024:
            raise RuntimeError(f"备份目录剩余空间不足（需约 {db_size * 2 // 1024 // 1024}MB）")

        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = dest_dir / f"agent-{ts}.db"
        attempt = 0
        while True:
            try:
                async with self._lock:
                    safe = str(dest).replace("'", "''")
                    cur = await self._conn.execute(f"VACUUM INTO '{safe}'")
                    await cur.close()
                    await self._conn.commit()
                digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                (dest_dir / f"{dest.name}.sha256").write_text(f"{digest}  {dest.name}\n", encoding="utf-8")
                break
            except Exception as exc:
                attempt += 1
                if attempt > retries:
                    raise RuntimeError(f"数据库备份失败（重试 {retries} 次）: {exc}") from exc
                logger.warning("backup attempt %s failed: %s", attempt, exc)
                await asyncio.sleep(1)

        if external_dir:
            ext = Path(external_dir)
            ext.mkdir(parents=True, exist_ok=True)
            ext_dest = ext / dest.name
            shutil.copy2(dest, ext_dest)
            ext_hash = hashlib.sha256(ext_dest.read_bytes()).hexdigest()
            if ext_hash != digest:
                raise RuntimeError("外置备份 SHA-256 校验不一致")
        return dest

    # ------------------------------------------------------------------
    async def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for table in ("messages", "tasks", "events", "metrics"):
            row = await self.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            out[table] = int(row["n"]) if row else 0
        return out

    async def heartbeat(self, value: Optional[str] = None) -> Optional[str]:
        if value is not None:
            await self.kv_set("heartbeat", value)
            return value
        return await self.kv_get("heartbeat")

    async def close(self) -> None:
        self._closed = True
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        await self.flush_bulk()
        if self._conn:
            await self._conn.close()
            self._conn = None
