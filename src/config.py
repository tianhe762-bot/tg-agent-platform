"""Application configuration.

Reads ``configs/.env`` (or a custom path) plus the process environment, exposes
typed settings, and supports atomic in-place hot reload for the running agent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import dotenv_values, load_dotenv


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Optional[str], default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: Optional[str], default: float) -> float:
    if value is None or not str(value).strip():
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _list(value: Optional[str], default: List[str]) -> List[str]:
    if value is None or not str(value).strip():
        return list(default)
    parts = str(value).replace(",", " ").split()
    return parts or list(default)


def _json_list(value: Optional[str], default: List[List[str]]) -> List[List[str]]:
    if value is None or not str(value).strip():
        return [list(x) for x in default]
    try:
        parsed = json.loads(str(value).strip())
    except json.JSONDecodeError:
        return [list(x) for x in default]
    if not isinstance(parsed, list):
        return [list(x) for x in default]
    return [[str(a) for a in item] for item in parsed if isinstance(item, list)]


DEFAULT_SUDO_WHITELIST: List[List[str]] = [
    ["/usr/bin/systemctl", "restart", "tg-agent.service"],
    ["/usr/bin/systemctl", "stop", "tg-agent.service"],
    ["/usr/sbin/reboot"],
    ["/usr/sbin/shutdown", "-h", "now"],
]

DEFAULT_EXEC_ALLOWLIST: List[str] = [
    "df", "free", "ps", "uptime", "uname", "du", "who", "last",
    "ss", "netstat", "docker",
]


@dataclass
class AppConfig:
    """Typed configuration snapshot for the agent process."""

    env_path: Optional[Path] = None
    root: Path = field(default_factory=Path.cwd)

    # telegram
    bot_token: str = ""
    admin_ids: List[int] = field(default_factory=list)
    admin_chat_id: Optional[int] = None
    tg_proxy: str = ""
    wol_mac: str = ""
    message_limit: int = 3900

    # llm
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 20
    llm_max_steps: int = 8
    llm_temperature: float = 0.2

    # paths
    data_dir: Path = field(default_factory=lambda: Path.cwd() / "data")
    log_dir: Path = field(default_factory=lambda: Path.cwd() / "logs")
    db_path: Path = field(default_factory=lambda: Path.cwd() / "data" / "agent.db")
    upload_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "uploads")
    control_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "control")
    pid_file: Path = field(default_factory=lambda: Path.cwd() / "data" / "agent.pid")

    # degradation thresholds
    degrade1_load: float = 2.8
    degrade1_mem_gb: float = 1.0
    degrade1_io_ms: float = 200.0
    degrade2_load: float = 3.6
    degrade2_mem_gb: float = 0.5
    degrade2_io_ms: float = 500.0
    recover_load: float = 1.8
    recover_mem_gb: float = 1.2
    recover_io_ms: float = 100.0
    recover2_load: float = 2.5
    recover2_mem_gb: float = 0.8
    recover2_io_ms: float = 200.0

    # execution
    exec_default_timeout: int = 60
    exec_max_timeout: int = 600
    exec_max_output_bytes: int = 100_000
    exec_calls_per_min: int = 20
    exec_allowlist: List[str] = field(default_factory=lambda: list(DEFAULT_EXEC_ALLOWLIST))
    sudo_whitelist: List[List[str]] = field(default_factory=lambda: [list(x) for x in DEFAULT_SUDO_WHITELIST])

    # mihomo
    mihomo_api: str = "http://127.0.0.1:9999"
    mihomo_test_url: str = "https://www.gstatic.com/generate_204"
    mihomo_delay_max: int = 800
    mihomo_delay_timeout: int = 3000
    mihomo_node_limit: int = 80

    # backup
    backup_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "backup")
    backup_external_dir: str = ""
    backup_retries: int = 2

    # monitor / alerts
    metrics_interval: int = 60
    alert_cooldown_min: int = 30
    disk_low_water_gb: float = 2.0
    offline_queue_max: int = 20
    watchdog_enabled: bool = False
    watchdog_fast_s: int = 30
    watchdog_regular_s: int = 120

    # uploads
    upload_quota_mb: int = 100
    upload_file_max_mb: int = 10

    # misc
    panel_services: List[str] = field(default_factory=list)
    log_level: str = "INFO"

    # reload bookkeeping
    _env_mtime_ns: int = 0

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, env_path: Optional[str | Path] = None) -> "AppConfig":
        """Build config from ``env_path`` (default ``configs/.env``) + os.environ."""
        root = Path(os.environ.get("TG_AGENT_ROOT", Path.cwd()))
        root = root.resolve()
        env_name = (os.environ.get("TG_AGENT_ENV") or "").strip()
        if env_path is None:
            env_path = Path(env_name) if env_name else root / "configs" / ".env"
        else:
            env_path = Path(env_path)

        if env_path.exists():
            load_dotenv(env_path, override=False, encoding="utf-8")

        def g(name: str) -> Optional[str]:
            return os.environ.get(name)

        data_dir = Path(g("DATA_DIR") or (root / "data"))
        log_dir = Path(g("LOG_DIR") or (root / "logs"))

        cfg = cls(
            env_path=env_path,
            root=root,
            bot_token=g("BOT_TOKEN") or "",
            admin_ids=[int(x) for x in _list(g("ADMIN_IDS"), []) if x.strip().lstrip("-").isdigit()],
            admin_chat_id=_int(g("ADMIN_CHAT_ID"), 0) or None,
            tg_proxy=g("TG_PROXY") or "",
            wol_mac=g("WOL_MAC") or g("WIN_MAC") or "",
            message_limit=_int(g("MESSAGE_LIMIT"), 3900),
            llm_base_url=g("LLM_BASE_URL") or "https://api.openai.com/v1",
            llm_api_key=g("LLM_API_KEY") or "",
            llm_model=g("LLM_MODEL") or "gpt-4o-mini",
            llm_timeout=_int(g("LLM_TIMEOUT"), 20),
            llm_max_steps=_int(g("LLM_MAX_STEPS"), 8),
            llm_temperature=_float(g("LLM_TEMPERATURE"), 0.2),
            data_dir=data_dir,
            log_dir=log_dir,
            db_path=Path(g("DATABASE_PATH") or (data_dir / "agent.db")),
            upload_dir=Path(g("UPLOAD_DIR") or (data_dir / "uploads")),
            control_dir=data_dir / "control",
            pid_file=data_dir / "agent.pid",
            degrade1_load=_float(g("DEGRADE1_LOAD"), 2.8),
            degrade1_mem_gb=_float(g("DEGRADE1_MEM_GB"), 1.0),
            degrade1_io_ms=_float(g("DEGRADE1_IO_MS"), 200.0),
            degrade2_load=_float(g("DEGRADE2_LOAD"), 3.6),
            degrade2_mem_gb=_float(g("DEGRADE2_MEM_GB"), 0.5),
            degrade2_io_ms=_float(g("DEGRADE2_IO_MS"), 500.0),
            recover_load=_float(g("RECOVER_LOAD"), 1.8),
            recover_mem_gb=_float(g("RECOVER_MEM_GB"), 1.2),
            recover_io_ms=_float(g("RECOVER_IO_MS"), 100.0),
            recover2_load=_float(g("RECOVER2_LOAD"), 2.5),
            recover2_mem_gb=_float(g("RECOVER2_MEM_GB"), 0.8),
            recover2_io_ms=_float(g("RECOVER2_IO_MS"), 200.0),
            exec_default_timeout=_int(g("EXEC_DEFAULT_TIMEOUT"), 60),
            exec_max_timeout=_int(g("EXEC_MAX_TIMEOUT"), 600),
            exec_max_output_bytes=_int(g("EXEC_MAX_OUTPUT_BYTES"), 100_000),
            exec_calls_per_min=_int(g("EXEC_CALLS_PER_MIN"), 20),
            exec_allowlist=_list(g("EXEC_ALLOWLIST"), DEFAULT_EXEC_ALLOWLIST),
            sudo_whitelist=_json_list(g("SUDO_WHITELIST"), DEFAULT_SUDO_WHITELIST),
            mihomo_api=g("MIHOMO_API") or "http://127.0.0.1:9999",
            mihomo_test_url=g("MIHOMO_TEST_URL") or "https://www.gstatic.com/generate_204",
            mihomo_delay_max=_int(g("MIHOMO_DELAY_MAX"), 800),
            mihomo_delay_timeout=_int(g("MIHOMO_DELAY_TIMEOUT"), 3000),
            mihomo_node_limit=_int(g("MIHOMO_NODE_LIMIT"), 80),
            backup_dir=Path(g("BACKUP_DIR") or (data_dir / "backup")),
            backup_external_dir=g("BACKUP_EXTERNAL_DIR") or "",
            backup_retries=_int(g("BACKUP_RETRIES"), 2),
            metrics_interval=max(5, _int(g("METRICS_INTERVAL"), 60)),
            alert_cooldown_min=_int(g("ALERT_COOLDOWN_MIN"), 30),
            disk_low_water_gb=_float(g("DISK_LOW_WATER_GB"), 2.0),
            offline_queue_max=_int(g("OFFLINE_QUEUE_MAX"), 20),
            watchdog_enabled=_bool(g("WATCHDOG_ENABLED"), False),
            watchdog_fast_s=_int(g("WATCHDOG_FAST_S"), 30),
            watchdog_regular_s=_int(g("WATCHDOG_REGULAR_S"), 120),
            upload_quota_mb=_int(g("UPLOAD_QUOTA_MB"), 100),
            upload_file_max_mb=_int(g("UPLOAD_FILE_MAX_MB"), 10),
            panel_services=_list(g("PANEL_SERVICES"), []),
            log_level=g("LOG_LEVEL") or "INFO",
        )
        cfg._env_mtime_ns = env_path.stat().st_mtime_ns if env_path.exists() else 0
        return cfg

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.log_dir, self.upload_dir, self.control_dir, self.backup_dir):
            path.mkdir(parents=True, exist_ok=True)

    def changed(self) -> bool:
        if not self.env_path or not self.env_path.exists():
            return False
        return self.env_path.stat().st_mtime_ns != self._env_mtime_ns

    def reload(self) -> bool:
        """In-place hot reload. Returns True when settings actually changed."""
        if self.env_path and self.env_path.exists():
            # drop stale values previously injected from the env file so the
            # file can win on reload (load_dotenv does not override by default)
            for key in dotenv_values(self.env_path):
                os.environ.pop(key, None)
        fresh = AppConfig.from_env(self.env_path)
        changed = fresh != self
        for key, value in fresh.__dict__.items():
            object.__setattr__(self, key, value)
        return changed

    def as_dict(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "db_path": str(self.db_path),
            "log_dir": str(self.log_dir),
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "mihomo_api": self.mihomo_api,
            "admin_ids": self.admin_ids,
            "watchdog_enabled": self.watchdog_enabled,
        }
