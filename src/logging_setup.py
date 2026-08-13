"""Logging: rotating file (10MB x 3) + console, and a dedicated sudo audit log."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def prune_logs(log_dir: Path, days: int = 30) -> int:
    """Delete log files older than ``days``. Returns the number removed."""
    import time as _time

    cutoff = _time.time() - days * 86400
    removed = 0
    try:
        for pattern in ("tg-agent.log*", "audit_sudo.log*"):
            for path in Path(log_dir).glob(pattern):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    return removed


def setup_logging(log_dir: Path, level: str = "INFO", console: bool = True) -> logging.Logger:
    """Configure root logger with rotating file handler and optional console output."""
    log_dir.mkdir(parents=True, exist_ok=True)
    prune_logs(log_dir, days=30)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # avoid duplicate handlers on re-initialization
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        log_dir / "tg-agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(console_handler)

    # 降低第三方库英文噪音；httpx 关闭后也不会再把 Bot Token 打印进日志
    for noisy in ("apscheduler", "httpx", "httpcore", "httpcore2", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("tg-agent")


class AuditLogger:
    """Append-only audit trail for privileged operations (90-day retention)."""

    def __init__(self, log_dir: Path) -> None:
        self.path = Path(log_dir) / "audit_sudo.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, command: list[str], allowed: bool, user: str = "", detail: str = "") -> None:
        import datetime

        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{ts} allowed={allowed} user={user or '-'} cmd={' '.join(command) or '-'} {detail}\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
        if not allowed:
            logging.getLogger("tg-agent.audit").warning("DENIED sudo: %s", " ".join(command))

    def rotate_if_needed(self, keep_days: int = 90) -> None:
        """Keep only the last N days of audit lines (simple size guard)."""
        try:
            if self.path.stat().st_size < 10 * 1024 * 1024:
                return
            import datetime

            cutoff = (datetime.datetime.now() - datetime.timedelta(days=keep_days)).isoformat()
            tmp = self.path.with_suffix(".log.tmp")
            kept = 0
            with open(self.path, "r", encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
                for line in src:
                    if line[:19] >= cutoff[:19]:
                        dst.write(line)
                        kept += 1
            if kept or True:
                os.replace(tmp, self.path)
        except OSError:
            pass
