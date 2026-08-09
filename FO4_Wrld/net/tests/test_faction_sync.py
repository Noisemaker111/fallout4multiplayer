"""B6.7 / Tier B1 — player faction-rank sync tests.

Coverage:
  - ServerState.record_faction_rank (last-write-wins, reject zero form)
  - FACTION_RANK_SET → BCAST to other peers
  - Bootstrap on peer join: FACTION_STATE_BOOT
  - Wire encode/decode for SET / BCAST / BOOT
"""
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
    FactionRankSetPayload, FactionRankBroadcastPayload,
    FactionStateBootPayload, FactionRankStateEntry,
    encode_frame, decode_frame,
)
from server.state import ServerState  # noqa: E402
from server.main import ServerProtocol  # noqa: E402


# ------------------------------------------------------------------ state

class TestFactionRankStateUnit:
    def test_first_set_creates_entry(self):
        s = ServerState()
        f = s.record_faction_rank(0x1C21C, 1, "alice", 1000.0)
        assert f is not None
        assert f.faction_form_id == 0x1C21C
        assert f.rank == 1
        assert f.last_owner_peer_id == "alice"

    def test_overwrite(self):
        s = ServerState()
        s.record_faction_rank(0x1C21C, 0, "alice", 1000.0)
        f = s.record_faction_rank(0x1C21C, 3, "bob", 2000.0)
        assert f.rank == 3
        assert f.last_owner_peer_id == "bob"

    def test_negative_rank_accepted(self):
        s = ServerState()
        f = s.record_faction_rank(0xABC, -1, "alice", 0.0)
        assert f is not None
        assert f.rank == -1

    def test_reject_zero_form_id(self):
        s = ServerState()
        assert s.record_faction_rank(0, 1, "alice", 0.0) is None

    def test_all_faction_ranks(self):
        s = ServerState()
        s.record_faction_rank(0x100, 1, "a", 0.0)
        s.record_faction_rank(0x200, 2, "b", 0.0)
        assert len(s.all_faction_ranks()) == 2


# ------------------------------------------------------------------ wire

class TestFactionRankWire:
    def test_set_roundtrip(self):
        p = FactionRankSetPayload(
            faction_form_id=0x1C21C, rank=2, timestamp_ms=123456)
        assert FactionRankSetPayload.decode(p.encode()) == p

    def test_set_negative_rank(self):
        p = FactionRankSetPayload(
            faction_form_id=0xABC, rank=-1, timestamp_ms=0)
        assert FactionRankSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = FactionRankBroadcastPayload(
            peer_id="player_A", faction_form_id=0xDEAD,
            rank=5, timestamp_ms=999)
        p2 = FactionRankBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "player_A"
        assert p2.faction_form_id == 0xDEAD
        assert p2.rank == 5

    def test_boot_roundtrip(self):
        entries = tuple(
            FactionRankStateEntry(faction_form_id=0x100 + i, rank=i)
            for i in range(5)
        )
        p = FactionStateBootPayload(
            entries=entries, chunk_index=0, total_chunks=1)
        assert FactionStateBootPayload.decode(p.encode()) == p

    def test_boot_empty(self):
        p = FactionStateBootPayload(entries=(), chunk_index=0, total_chunks=1)
        assert FactionStateBootPayload.decode(p.encode()) == p

    def test_boot_too_many_raises(self):
        over = FactionStateBootPayload.MAX_ENTRIES_PER_FRAME + 1
        entries = tuple(FactionRankStateEntry(i, i) for i in range(over))
        with pytest.raises(Exception):
            FactionStateBootPayload(entries=entries).encode()


# ------------------------------------------------------------------ helpers (mirror test_quest_sync)

async def _start_server_with_state(state: ServerState, port: int):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ServerProtocol(state),
        local_addr=("127.0.0.1", port),
    )
    from net.tests.test_server_integration import _periodic_tick_driver
    loop.create_task(_periodic_tick_driver(protocol, 20))
    return transport, protocol


async def _raw_peer_hello(sock: socket.socket, server_addr, client_id: str) -> int:
    hello = HelloPayload(
        client_id=client_id, client_version_major=1, client_version_minor=0)
    raw = encode_frame(MessageType.HELLO, 0, hello, reliable=True)
    sock.sendto(raw, server_addr)
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
            assert frame.payload.accepted
            return frame.payload.session_id
    raise AssertionError("HELLO -> WELCOME timed out")


async def _collect_frames(sock: socket.socket, duration_s: float) -> list:
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


# ------------------------------------------------------------------ integration

@pytest.mark.asyncio
async def test_faction_rank_set_broadcasts_to_other_peers():
    port = 31530
    state = ServerState()
    transport, _ = await _start_server_with_state(state, port)
    try:
        server_addr = ("127.0.0.1", port)
        sock_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_a.bind(("127.0.0.1", 0))
        sock_b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(
                _raw_peer_hello(sock_a, server_addr, "peer_fA"),
                _raw_peer_hello(sock_b, server_addr, "peer_fB"),
            )
            await _collect_frames(sock_b, 0.3)
            await _collect_frames(sock_a, 0.0)

            op = FactionRankSetPayload(
                faction_form_id=0x1C21C, rank=1, timestamp_ms=100)
            sock_a.sendto(
                encode_frame(MessageType.FACTION_RANK_SET, 1, op, reliable=True),
                server_addr)

            frames_b = await _collect_frames(sock_b, 0.8)
            frames_a = await _collect_frames(sock_a, 0.1)

            bcasts_b = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.FACTION_RANK_BCAST)]
            bcasts_a = [
                f.payload for f, _ in frames_a
                if f.header.msg_type == int(MessageType.FACTION_RANK_BCAST)]

            assert len(bcasts_b) == 1, f"B should receive 1 bcast, got {bcasts_b}"
            assert len(bcasts_a) == 0, f"A should receive NO echo, got {bcasts_a}"
            assert bcasts_b[0].peer_id == "peer_fA"
            assert bcasts_b[0].faction_form_id == 0x1C21C
            assert bcasts_b[0].rank == 1

            f = state.faction_rank(0x1C21C)
            assert f is not None and f.rank == 1
            assert f.last_owner_peer_id == "peer_fA"
        finally:
            sock_a.close()
            sock_b.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_faction_bootstrap_on_join():
    port = 31531
    state = ServerState()
    state.record_faction_rank(0x1111, 2, "seeder", 0.0)
    state.record_faction_rank(0x2222, -1, "seeder", 0.0)
    transport, _ = await _start_server_with_state(state, port)
    try:
        server_addr = ("127.0.0.1", port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        try:
            await _raw_peer_hello(sock, server_addr, "newbie")
            frames = await _collect_frames(sock, 0.8)
            boot = [
                f.payload for f, _ in frames
                if f.header.msg_type == int(MessageType.FACTION_STATE_BOOT)]
            assert boot, "expected FACTION_STATE_BOOT on join"
            entries = [e for p in boot for e in p.entries]
            m = {e.faction_form_id: e.rank for e in entries}
            assert m.get(0x1111) == 2
            assert m.get(0x2222) == -1
        finally:
            sock.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_faction_rank_zero_form_rejected():
    port = 31532
    state = ServerState()
    transport, _ = await _start_server_with_state(state, port)
    try:
        server_addr = ("127.0.0.1", port)
        sock_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_a.bind(("127.0.0.1", 0))
        sock_b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(
                _raw_peer_hello(sock_a, server_addr, "peer_zA"),
                _raw_peer_hello(sock_b, server_addr, "peer_zB"),
            )
            await _collect_frames(sock_b, 0.3)
            await _collect_frames(sock_a, 0.0)

            op = FactionRankSetPayload(
                faction_form_id=0, rank=1, timestamp_ms=0)
            sock_a.sendto(
                encode_frame(MessageType.FACTION_RANK_SET, 1, op, reliable=True),
                server_addr)

            frames_b = await _collect_frames(sock_b, 0.5)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.FACTION_RANK_BCAST)]
            assert bcasts == []
            assert state.all_faction_ranks() == []
        finally:
            sock_a.close()
            sock_b.close()
    finally:
        transport.close()
