"""A2 party warp + A4 XP grant wire/server tests (protocol v20)."""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol import (  # noqa: E402
    MessageType,
    HelloPayload, AckPayload,
    XpGrantSetPayload, XpGrantBroadcastPayload,
    PartyWarpPayload, PartyWarpBroadcastPayload,
    encode_frame, decode_frame,
)
from server.state import ServerState  # noqa: E402
from server.main import ServerProtocol  # noqa: E402


class TestXpGrantWire:
    def test_set_roundtrip(self):
        p = XpGrantSetPayload(amount=150, timestamp_ms=1)
        assert XpGrantSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = XpGrantBroadcastPayload(
            peer_id="alice", amount=50, timestamp_ms=9)
        p2 = XpGrantBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "alice"
        assert p2.amount == 50


class TestPartyWarpWire:
    def test_set_roundtrip(self):
        p = PartyWarpPayload(
            x=1.0, y=2.0, z=3.0, yaw_rad=0.5,
            cell_id=0xABC, timestamp_ms=10)
        assert PartyWarpPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = PartyWarpBroadcastPayload(
            peer_id="bob", x=10.0, y=20.0, z=30.0,
            yaw_rad=1.0, cell_id=0x14, timestamp_ms=11)
        p2 = PartyWarpBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "bob"
        assert p2.x == pytest.approx(10.0)
        assert p2.cell_id == 0x14


async def _start_server(state: ServerState, port: int):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ServerProtocol(state),
        local_addr=("127.0.0.1", port),
    )
    from net.tests.test_server_integration import _periodic_tick_driver
    loop.create_task(_periodic_tick_driver(protocol, 20))
    return transport, protocol


async def _hello(sock, server_addr, client_id: str):
    hello = HelloPayload(
        client_id=client_id, client_version_major=1, client_version_minor=0)
    sock.sendto(encode_frame(MessageType.HELLO, 0, hello, reliable=True),
                server_addr)
    sock.setblocking(False)
    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        try:
            data, _ = sock.recvfrom(4096)
        except BlockingIOError:
            await asyncio.sleep(0.02)
            continue
        frame = decode_frame(data)
        if frame.header.msg_type == int(MessageType.WELCOME):
            ack = AckPayload(
                highest_contiguous_seq=frame.header.seq, sack_bitmap=0)
            sock.sendto(
                encode_frame(MessageType.ACK, 0, ack, reliable=False),
                server_addr)
            return
    raise AssertionError("WELCOME timeout")


async def _collect(sock, duration_s: float):
    out = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_s
    sock.setblocking(False)
    while loop.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        try:
            frame = decode_frame(data)
        except Exception:
            continue
        out.append((frame, addr))
        if frame.header.flags & 0x01:
            ack = AckPayload(
                highest_contiguous_seq=frame.header.seq, sack_bitmap=0)
            sock.sendto(
                encode_frame(MessageType.ACK, 0, ack, reliable=False), addr)
    return out


@pytest.mark.asyncio
async def test_xp_grant_broadcasts():
    port = 31540
    state = ServerState()
    transport, _ = await _start_server(state, port)
    try:
        server_addr = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(
                _hello(a, server_addr, "xpA"),
                _hello(b, server_addr, "xpB"),
            )
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = XpGrantSetPayload(amount=100, timestamp_ms=1)
            a.sendto(
                encode_frame(MessageType.XP_GRANT_SET, 1, op, reliable=True),
                server_addr)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.XP_GRANT_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].peer_id == "xpA"
            assert bcasts[0].amount == 100
        finally:
            a.close()
            b.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_party_warp_broadcasts():
    port = 31541
    state = ServerState()
    transport, _ = await _start_server(state, port)
    try:
        server_addr = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(
                _hello(a, server_addr, "warpA"),
                _hello(b, server_addr, "warpB"),
            )
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = PartyWarpPayload(
                x=100.0, y=200.0, z=50.0, yaw_rad=0.25,
                cell_id=0x3C, timestamp_ms=2)
            a.sendto(
                encode_frame(MessageType.PARTY_WARP, 1, op, reliable=True),
                server_addr)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.PARTY_WARP_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].peer_id == "warpA"
            assert bcasts[0].x == pytest.approx(100.0)
            assert bcasts[0].cell_id == 0x3C
        finally:
            a.close()
            b.close()
    finally:
        transport.close()
