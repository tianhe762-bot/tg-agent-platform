"""tg-agent-cli: status / reload / soft-reset / kill-subprocesses / backup / restart."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from src import __version__
from src.config import AppConfig


def _flag(config: AppConfig, name: str) -> Path:
    path = config.control_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def cmd_status(config: AppConfig) -> int:
    pid = "未运行"
    if config.pid_file.exists():
        try:
            pid = config.pid_file.read_text(encoding="utf-8").strip() or "未知"
        except OSError:
            pid = "未知"
    print(f"TG-Agent v{__version__}")
    print(f"PID: {pid}")
    print(f"配置: {config.env_path}")
    print(f"数据库: {config.db_path}")
    print(f"Mihomo: {config.mihomo_api}")
    print(f"模型: {config.llm_model}")
    try:
        conn = sqlite3.connect(str(config.db_path))
        cur = conn.cursor()
        for table in ("messages", "tasks", "events", "metrics"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"{table}: {cur.fetchone()[0]}")
        cur.execute("SELECT value FROM kv WHERE key='degrade_level'")
        row = cur.fetchone()
        print(f"降级级别: {row[0] if row else '未知'}")
        cur.execute("SELECT value FROM kv WHERE key='heartbeat'")
        row = cur.fetchone()
        if row:
            age = time.time() - float(row[0])
            print(f"心跳: {age:.0f}s 前")
        conn.close()
    except sqlite3.Error as exc:
        print(f"数据库读取失败: {exc}")
    return 0


def cmd_reload(config: AppConfig) -> int:
    _flag(config, "reload").write_text(str(int(time.time())), encoding="utf-8")
    print("已发送 reload 信号")
    return 0


def cmd_soft_reset(config: AppConfig) -> int:
    _flag(config, "soft_reset").write_text(str(int(time.time())), encoding="utf-8")
    print("已发送 soft-reset 信号")
    return 0


def cmd_backup(config: AppConfig) -> int:
    _flag(config, "backup").write_text(str(int(time.time())), encoding="utf-8")
    print("已发送 backup 信号")
    return 0


def cmd_kill_subprocesses(config: AppConfig) -> int:
    pgid_file = Path(config.data_dir) / "subprocesses.pid"
    killed = 0
    if pgid_file.exists():
        for line in pgid_file.read_text(encoding="utf-8").splitlines():
            try:
                pid = int(line.split(" ", 1)[0])
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
                else:
                    import os
                    import signal

                    os.killpg(pid, signal.SIGKILL)
                killed += 1
            except (OSError, ValueError):
                continue
    print(f"已向 {killed} 个进程组发送 SIGKILL")
    return 0


def cmd_restart(config: AppConfig) -> int:
    if sys.platform == "win32":
        print("Windows 下请手动重启服务")
        return 1
    try:
        subprocess.run(["systemctl", "restart", "tg-agent.service"], timeout=30, check=False)
        print("已执行 systemctl restart tg-agent.service")
        return 0
    except FileNotFoundError:
        print("systemctl 不可用（非 systemd 环境）")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tg-agent-cli", description="TG-Agent 运维 CLI")
    parser.add_argument("--config", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="查看运行状态与数据库统计")
    sub.add_parser("reload", help="配置原子热重载")
    sub.add_parser("soft-reset", help="温和重置（清空任务队列与确认状态）")
    sub.add_parser("kill-subprocesses", help="强杀所有活跃子进程组")
    sub.add_parser("backup", help="触发数据库与配置备份")
    sub.add_parser("restart", help="重启 tg-agent systemd 服务")

    args = parser.parse_args(argv)
    config = AppConfig.from_env(args.config)
    handlers = {
        "status": cmd_status,
        "reload": cmd_reload,
        "soft-reset": cmd_soft_reset,
        "kill-subprocesses": cmd_kill_subprocesses,
        "backup": cmd_backup,
        "restart": cmd_restart,
    }
    return handlers[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
