"""v23: RELATIONSHIP_* + TIME_PASS_* wire and server fan-out tests."""
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
    RelationshipSetPayload, RelationshipBroadcastPayload,
    RelationshipBootPayload, RelationshipStateEntry,
    TimePassSetPayload, TimePassBroadcastPayload,
    encode_frame, decode_frame,
    PROTOCOL_VERSION,
)
from server.state import ServerState  # noqa: E402
from server.main import ServerProtocol  # noqa: E402


class TestRelationshipWire:
    def test_set_roundtrip(self):
        p = RelationshipSetPayload(
            actor_a_form_id=0x14, actor_b_form_id=0x1D15C,
            rank=2, timestamp_ms=100)
        assert RelationshipSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = RelationshipBroadcastPayload(
            peer_id="alice", actor_a_form_id=0x14, actor_b_form_id=0x1D15C,
            rank=-1, timestamp_ms=200)
        p2 = RelationshipBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "alice"
        assert p2.actor_a_form_id == 0x14
        assert p2.actor_b_form_id == 0x1D15C
        assert p2.rank == -1
        assert p2.timestamp_ms == 200

    def test_set_size(self):
        p = RelationshipSetPayload(1, 2, 0, 0)
        assert len(p.encode()) == 20


class TestTimePassWire:
    def test_set_roundtrip(self):
        p = TimePassSetPayload(hours=8, timestamp_ms=50)
        assert TimePassSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = TimePassBroadcastPayload(
            peer_id="bob", hours=24, timestamp_ms=99)
        p2 = TimePassBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "bob" and p2.hours == 24

    def test_set_size(self):
        p = TimePassSetPayload(1, 0)
        assert len(p.encode()) == 12

    def test_protocol_version_at_least_24(self):
        assert PROTOCOL_VERSION >= 24

    def test_relationship_boot_roundtrip(self):
        entries = (
            RelationshipStateEntry(0x14, 0x1D15C, 2),
            RelationshipStateEntry(0x14, 0x2F1E, -1),
        )
        p = RelationshipBootPayload(entries=entries, chunk_index=0, total_chunks=1)
        p2 = RelationshipBootPayload.decode(p.encode())
        assert len(p2.entries) == 2
        assert p2.entries[0].rank == 2
        assert p2.entries[1].actor_b_form_id == 0x2F1E


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
async def test_relationship_set_broadcasts():
    port = 31570
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
                _hello(a, server, "relA"), _hello(b, server, "relB"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = RelationshipSetPayload(
                actor_a_form_id=0x14,
                actor_b_form_id=0x1D15C,
                rank=3,
                timestamp_ms=1000,
            )
            a.sendto(
                encode_frame(MessageType.RELATIONSHIP_SET, 1, op, reliable=True),
                server)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.RELATIONSHIP_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].peer_id == "relA"
            assert bcasts[0].actor_a_form_id == 0x14
            assert bcasts[0].actor_b_form_id == 0x1D15C
            assert bcasts[0].rank == 3
            assert len(state.all_relationships()) == 1

            # late join gets bootstrap
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.bind(("127.0.0.1", 0))
            try:
                await _hello(c, server, "relC")
                frames_c = await _collect(c, 0.8)
                boots = [
                    f.payload for f, _ in frames_c
                    if f.header.msg_type == int(
                        MessageType.RELATIONSHIP_STATE_BOOT)]
                assert boots
                entries = [e for p in boots for e in p.entries]
                m = {(e.actor_a_form_id, e.actor_b_form_id): e.rank
                     for e in entries}
                assert m.get((0x14, 0x1D15C)) == 3
            finally:
                c.close()
        finally:
            a.close()
            b.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_time_pass_set_broadcasts():
    port = 31571
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
                _hello(a, server, "sleeper"), _hello(b, server, "watcher"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = TimePassSetPayload(hours=8, timestamp_ms=2000)
            a.sendto(
                encode_frame(MessageType.TIME_PASS_SET, 1, op, reliable=True),
                server)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.TIME_PASS_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].peer_id == "sleeper"
            assert bcasts[0].hours == 8
        finally:
            a.close()
            b.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_time_pass_rejects_zero_and_huge():
    port = 31572
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
                _hello(a, server, "bad"), _hello(b, server, "good"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            seq = 1
            for hours in (0, 721, -721):
                op = TimePassSetPayload(hours=hours, timestamp_ms=1)
                a.sendto(
                    encode_frame(MessageType.TIME_PASS_SET, seq, op,
                                 reliable=True),
                    server)
                seq += 1

            frames_b = await _collect(b, 0.6)
            bcasts = [
                f for f, _ in frames_b
                if f.header.msg_type == int(MessageType.TIME_PASS_BCAST)]
            assert not bcasts, "invalid hours must not fan out"
            assert protocol._counters.get("rejections", 0) >= 3
        finally:
            a.close()
            b.close()
    finally:
        transport.close()
