"""B6.9 cell-cleared wire/server tests (protocol v22)."""
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
    CellClearedSetPayload, CellClearedBroadcastPayload,
    CellClearedBootPayload, CellClearedStateEntry,
    encode_frame, decode_frame,
)
from server.state import ServerState  # noqa: E402
from server.main import ServerProtocol  # noqa: E402


class TestCellClearedWire:
    def test_set_roundtrip(self):
        p = CellClearedSetPayload(
            cell_form_id=0x3C, cleared=1, timestamp_ms=1)
        assert CellClearedSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = CellClearedBroadcastPayload(
            peer_id="a", cell_form_id=0xAB, cleared=1, timestamp_ms=2)
        p2 = CellClearedBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "a" and p2.cleared == 1

    def test_boot_roundtrip(self):
        entries = (
            CellClearedStateEntry(0x111, 1),
            CellClearedStateEntry(0x222, 0),
        )
        p = CellClearedBootPayload(entries=entries)
        assert CellClearedBootPayload.decode(p.encode()) == p


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
async def test_cell_cleared_set_broadcasts_and_bootstraps():
    port = 31560
    state = ServerState()
    transport, _ = await _start(state, port)
    try:
        server = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(_hello(a, server, "clA"), _hello(b, server, "clB"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = CellClearedSetPayload(
                cell_form_id=0x1EAF, cleared=1, timestamp_ms=9)
            a.sendto(
                encode_frame(MessageType.CELL_CLEARED_SET, 1, op, reliable=True),
                server)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.CELL_CLEARED_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].cell_form_id == 0x1EAF
            assert bcasts[0].cleared == 1
            assert dict(state.all_cleared_cells())[0x1EAF] is True

            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.bind(("127.0.0.1", 0))
            try:
                await _hello(c, server, "clC")
                frames_c = await _collect(c, 0.8)
                boots = [
                    f.payload for f, _ in frames_c
                    if f.header.msg_type == int(MessageType.CELL_CLEARED_BOOT)]
                assert boots
                entries = [e for p in boots for e in p.entries]
                m = {e.cell_form_id: e.cleared for e in entries}
                assert m.get(0x1EAF) == 1
            finally:
                c.close()
        finally:
            a.close()
            b.close()
    finally:
        transport.close()
