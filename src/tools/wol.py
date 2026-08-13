"""Wake-on-LAN magic packet support."""

from __future__ import annotations

import asyncio
import socket


def normalize_mac(mac: str) -> bytes:
    clean = str(mac).strip().replace("-", "").replace(":", "").replace(".", "")
    if len(clean) != 12 or not all(c in "0123456789abcdefABCDEF" for c in clean):
        raise ValueError(f"无效 MAC 地址: {mac}")
    return bytes.fromhex(clean)


def build_magic_packet(mac: str) -> bytes:
    mac_bytes = normalize_mac(mac)
    return b"\xff" * 6 + mac_bytes * 16


async def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = build_magic_packet(mac)
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        await loop.sock_sendto(sock, packet, (broadcast, port))
    finally:
        sock.close()
