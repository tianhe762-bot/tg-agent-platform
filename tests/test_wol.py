import pytest

from src.tools.wol import build_magic_packet, normalize_mac, send_wol


def test_normalize_mac():
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == bytes.fromhex("AABBCCDDEEFF")
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == bytes.fromhex("AABBCCDDEEFF")
    with pytest.raises(ValueError):
        normalize_mac("not-a-mac")


def test_magic_packet():
    packet = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:].count(bytes.fromhex("AABBCCDDEEFF")) == 16


@pytest.mark.asyncio
async def test_send_wol_local():
    # UDP broadcast to loopback: send should complete without error
    await send_wol("AA:BB:CC:DD:EE:FF", broadcast="127.0.0.1", port=9)
