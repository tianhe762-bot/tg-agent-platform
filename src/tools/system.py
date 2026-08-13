"""System inspection tools: status report, port scanning, privileged actions."""

from __future__ import annotations

import logging
import re
import shutil
import socket
import time
from typing import Dict, List, Optional, Tuple

import psutil

from src.config import AppConfig
from src.tools.db import AgentDB
from src.tools.executor import Executor
from src.tools.mihomo import MihomoClient

logger = logging.getLogger("tg-agent.system")


def hostname() -> str:
    return socket.gethostname()


def os_release() -> str:
    try:
        info: Dict[str, str] = {}
        with open("/etc/os-release", "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    key, _, value = line.partition("=")
                    info[key] = value.strip().strip('"')
        name = info.get("PRETTY_NAME") or info.get("NAME") or "Linux"
        if info.get("VERSION_ID"):
            name = f"{name} ({info['VERSION_ID']})"
        return name
    except OSError:
        return f"{psutil.LINUX or 'Linux'}"


def uptime_text() -> str:
    seconds = int(time.time() - psutil.boot_time())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"


def cpu_temp() -> str:
    if not hasattr(psutil, "sensors_temperatures"):
        return ""
    temps = psutil.sensors_temperatures()
    for key in ("coretemp", "cpu_thermal", "k10temp"):
        if temps.get(key):
            current = temps[key][0].current
            return f"{current:.0f}°C"
    return ""


def network_counters() -> Tuple[str, int, int]:
    per_nic = psutil.net_io_counters(pernic=True)
    for name in sorted(per_nic):
        if name.startswith(("eth", "en", "wlan")):
            counter = per_nic[name]
            return name, counter.bytes_recv, counter.bytes_sent
    for name, counter in sorted(per_nic.items()):
        return name, counter.bytes_recv, counter.bytes_sent
    return "", 0, 0


def format_bytes(value: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}T"


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    for name, addrs in psutil.net_if_addrs().items():
        if name.startswith(("eth", "en", "wlan")):
            for addr in addrs:
                ip = addr.address
                if ip and "." in ip and not ip.startswith("127."):
                    return ip
    return "127.0.0.1"


async def system_status(config: AppConfig, executor: Executor, mihomo: Optional[MihomoClient] = None) -> str:
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    try:
        load = os_load()
    except Exception:
        load = ""
    disk = psutil.disk_usage(str(config.data_dir))
    nic, recv, sent = network_counters()
    lines = [
        "📋 服务器状态汇报",
        f"🖥️ 主机: {hostname()}",
        f"💻 系统: {os_release()}",
        f"⏱️ 已持续运行: {uptime_text()}",
        f"📊 CPU 使用率: {cpu:.0f}%" + (f" · 温度: {cpu_temp()}" if cpu_temp() else ""),
        f"🧠 内存: {format_bytes(mem.used)} / {format_bytes(mem.total)}（{mem.percent:.0f}%）",
        f"💾 数据目录: {format_bytes(disk.used)} / {format_bytes(disk.total)}（{disk.percent:.0f}%）",
    ]
    if load:
        lines.append(f"⚖️ 负载: {load}")
    if nic:
        lines.append(f"🌐 网络: {nic} · 下载: {format_bytes(recv)} · 上传: {format_bytes(sent)}")

    docker = await _docker_ps(executor)
    if docker:
        lines.append(f"🐳 Docker: {len(docker)} 个容器")
        for name, status in docker[:8]:
            lines.append(f"• {name} : {status}")
    elif docker is not None:
        lines.append("🐳 Docker: 未检测到容器")

    if mihomo:
        proxies = await mihomo.proxies()
        if not proxies:
            lines.append("🛰️ Mihomo: ❌ 未开启")
        else:
            conns = await mihomo.connections()
            node = mihomo.current_node(proxies, conns)
            if not node:
                lines.append("🛰️ Mihomo: ✅ 已开启")
            else:
                d = await mihomo.delay(node) if node not in ("DIRECT", "REJECT", "GLOBAL", "REJECT-DROP", "PASS") else None
                delay_txt = f"{d}ms" if d is not None else "超时"
                lines.append(f"🛰️ Mihomo: ✅ 已开启 · 当前节点: {node} · 延迟: {delay_txt}")

    from src import __version__

    lines.append(f"🤖 Agent: v{__version__}")
    return "\n".join(lines)


def os_load() -> str:
    load1, load5, load15 = __import__("os").getloadavg()
    return f"{load1:.2f} / {load5:.2f} / {load15:.2f}"


async def _docker_ps(executor: Executor) -> Optional[List[Tuple[str, str]]]:
    try:
        result = await executor.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
            timeout=15,
            max_output_bytes=20_000,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return []
    out: List[Tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "|" in line:
            name, _, status = line.partition("|")
            out.append((name.strip(), status.strip()))
    return out


async def ports(config: AppConfig, executor: Executor) -> str:
    ip = lan_ip()
    lines = ["🌐 局域网服务端口", f"服务器IP: {ip}", ""]

    docker_map: Dict[int, str] = {}
    try:
        result = await executor.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Ports}}"],
            timeout=15,
            max_output_bytes=20_000,
        )
        for line in result.stdout.splitlines():
            if "|" not in line:
                continue
            name, _, ports_str = line.partition("|")
            for m in re.finditer(r"0\.0\.0\.0:(\d+)->", ports_str):
                docker_map[int(m.group(1))] = name.strip()
    except Exception:
        pass

    if docker_map:
        lines.append("🐳 Docker:")
        for port in sorted(docker_map):
            lines.append(f"• {docker_map[port]} — http://{ip}:{port}")
        lines.append("")

    other: Dict[int, str] = {}
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            port = conn.laddr.port
            if port in docker_map:
                continue
            name = "未知进程"
            try:
                name = psutil.Process(conn.pid).name() if conn.pid else "系统"
            except (psutil.Error, AttributeError):
                pass
            other[port] = name
    except (psutil.Error, AttributeError):
        pass

    if other:
        lines.append("💻 其他程序:")
        for port in sorted(other):
            lines.append(f"• {other[port]} — http://{ip}:{port}")
        lines.append("")

    manual: List[str] = []
    for entry in config.panel_services:
        if "=" in entry:
            label, _, port = entry.partition("=")
            manual.append(f"{label.strip()} — http://{ip}:{port.strip()}")
    if manual:
        lines.append("📝 手动配置:")
        lines.extend(f"• {m}" for m in manual)
        lines.append("")
    return "\n".join(lines)


async def reboot(config: AppConfig, executor: Executor) -> str:
    await executor.run_sudo(["/usr/sbin/reboot"], user="tg-agent", timeout=30)
    return "✅ 正在重启服务器..."


async def shutdown(config: AppConfig, executor: Executor) -> str:
    await executor.run_sudo(["/usr/sbin/shutdown", "-h", "now"], user="tg-agent", timeout=30)
    return "✅ 正在关闭服务器..."


async def backup_all(config: AppConfig, db: AgentDB, executor: Executor) -> str:
    try:
        dest = await db.backup(
            dest_dir=config.backup_dir,
            external_dir=config.backup_external_dir,
            retries=config.backup_retries,
        )
    except Exception as exc:
        return f"❌ 数据库备份失败: {exc}"

    copied: List[str] = [f"• 数据库: {dest.name}（SHA-256 已校验）"]
    env_file = config.env_path
    if env_file and env_file.exists():
        try:
            target = config.backup_dir / "config.env"
            shutil.copy2(env_file, target)
            copied.append("• 配置: config.env")
        except OSError:
            pass
    if config.backup_external_dir:
        copied.append(f"• 已同步到外置目录: {config.backup_external_dir}")
    return "✅ 备份完成\n" + "\n".join(copied)
