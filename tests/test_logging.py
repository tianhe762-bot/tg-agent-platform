import os
import time

from src.logging_setup import prune_logs


def test_prune_logs(tmp_path):
    old = tmp_path / "tg-agent.log.1"
    old.write_text("old")
    fresh = tmp_path / "tg-agent.log"
    fresh.write_text("new")
    audit = tmp_path / "audit_sudo.log"
    audit.write_text("audit")
    unrelated = tmp_path / "other.log"
    unrelated.write_text("other")

    ts = time.time() - 40 * 86400
    os.utime(old, (ts, ts))
    os.utime(audit, (ts, ts))

    removed = prune_logs(tmp_path, days=30)
    assert removed == 2
    assert not old.exists()
    assert not audit.exists()
    assert fresh.exists()
    assert unrelated.exists()
