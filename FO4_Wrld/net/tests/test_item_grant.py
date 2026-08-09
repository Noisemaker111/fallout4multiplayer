"""v25: ITEM_GRANT_* wire and server fan-out tests."""
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
    ItemGrantSetPayload, ItemGrantBroadcastPayload,
    encode_frame, decode_frame,
    PROTOCOL_VERSION,
)
from server.state import ServerState  # noqa: E402
from server.main import ServerProtocol  # noqa: E402


class TestItemGrantWire:
    def test_set_roundtrip(self):
        p = ItemGrantSetPayload(
            item_form_id=0x1A4D7, count=1, silent=1, timestamp_ms=10)
        assert ItemGrantSetPayload.decode(p.encode()) == p
        assert len(p.encode()) == 17

    def test_bcast_roundtrip(self):
        p = ItemGrantBroadcastPayload(
            peer_id="alice", item_form_id=0xABC, count=3, silent=1,
            timestamp_ms=20)
        p2 = ItemGrantBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "alice"
        assert p2.item_form_id == 0xABC
        assert p2.count == 3

    def test_protocol_version_is_25(self):
        assert PROTOCOL_VERSION == 25


async def _start(state: ServerState, port: int):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ServerProtocol(state), local_addr=("127.0.0.1", port))
    from net.tests.test_server_integration import _periodic_tick_driver
    loop.create_task(_periodic_tick_driver(protocol, 20))
    return transport, protocol


async def _hello(sock, addr, cid: str):
    hello = HelloPayload(
        client_id=cid, client_version_major=1, client_version_minor=0)
    sock.sendto(encode_frame(MessageType.HELLO, 0, hello, reliable=True), addr)
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
                encode_frame(MessageType.ACK, 0, ack, reliable=False), addr)
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
async def test_item_grant_broadcasts():
    port = 31580
    state = ServerState()
    transport, _ = await _start(state, port)
    try:
        server = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(
                _hello(a, server, "gA"), _hello(b, server, "gB"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = ItemGrantSetPayload(
                item_form_id=0x1A4D7, count=1, silent=1, timestamp_ms=99)
            a.sendto(
                encode_frame(MessageType.ITEM_GRANT_SET, 1, op, reliable=True),
                server)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.ITEM_GRANT_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].peer_id == "gA"
            assert bcasts[0].item_form_id == 0x1A4D7
            assert bcasts[0].count == 1
        finally:
            a.close()
            b.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_item_grant_rejects_caps_and_bad_count():
    port = 31581
    state = ServerState()
    transport, protocol = await _start(state, port)
    try:
        server = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(
                _hello(a, server, "bad"), _hello(b, server, "ok"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            seq = 1
            for fid, cnt in ((0xF, 1), (0xABC, 0), (0xABC, 100), (0, 1)):
                op = ItemGrantSetPayload(
                    item_form_id=fid, count=cnt, silent=1, timestamp_ms=1)
                a.sendto(
                    encode_frame(MessageType.ITEM_GRANT_SET, seq, op,
                                 reliable=True),
                    server)
                seq += 1

            frames_b = await _collect(b, 0.6)
            bcasts = [
                f for f, _ in frames_b
                if f.header.msg_type == int(MessageType.ITEM_GRANT_BCAST)]
            assert not bcasts
            assert protocol._counters.get("rejections", 0) >= 4
        finally:
            a.close()
            b.close()
    finally:
        transport.close()
