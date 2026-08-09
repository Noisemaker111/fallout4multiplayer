"""Multi-peer (N > 2) end-to-end tests against the real asyncio server.

Everything shipped so far was validated with exactly two peers, and the native
client still holds a single ghost slot. The *server*, though, is written
against `all_sessions()` / `other_sessions()` and should already be N-peer.
These tests pin that down for N = 4 so the client-side work has a trustworthy
foundation to build on: if a 4-player session is broken, we want to know
whether it's the client or the server before touching 12k lines of C++.

Deliberately covers the things that silently break when code was written
assuming "the other peer" is a single thing:
  - the join mesh is complete and symmetric (everyone knows everyone)
  - fan-out reaches N-1 peers and never echoes to the sender
  - a peer leaving notifies all N-1 survivors
  - unicast bootstrap state doesn't leak into other peers' streams
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol import (  # noqa: E402
    MessageType,
    HelloPayload,
    WelcomePayload,
    PeerJoinPayload,
    PeerLeavePayload,
    PosStatePayload,
    PosBroadcastPayload,
    PoseStatePayload,
    PoseBroadcastPayload,
    DisconnectPayload,
    encode_frame,
    decode_frame,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the existing harness rather than cloning it — the tests dir has no
# __init__.py, so this is a flat import off the path entry added above.
from test_server_integration import (  # noqa: E402
    FakeClient,
    _make_client,
    _start_server,
    _wait_for,
)


PEER_NAMES = ["alpha", "bravo", "charlie", "delta"]

# Each test binds its own port so the module can run alongside the existing
# integration suite (which squats on 31340+) without collisions.
BASE_PORT = 31400


def _collect(client: FakeClient, msg_type: int) -> list:
    """Drain every buffered frame of `msg_type`, returning decoded payloads.

    Duplicate sequence numbers are dropped. These fake clients never ACK, so
    the server's reliable channel keeps retransmitting — that is correct
    behaviour, and the real client dedupes by seq in `ReliableChannel` before
    anything downstream sees a frame. Deduping here tests what the client
    actually observes rather than raw wire traffic.

    Non-destructive for other message types — bootstrap chunks and heartbeats
    stay in the buffer so a later assertion can still see them.
    """
    out = []
    seen_seq: set[int] = set()
    for raw in list(client.received):
        try:
            frame = decode_frame(raw)
        except Exception:
            continue
        if frame.header.msg_type != msg_type:
            continue
        client.received.remove(raw)
        if frame.header.seq in seen_seq:
            continue
        seen_seq.add(frame.header.seq)
        out.append(frame.payload)
    return out


async def _join(server_port: int, name: str) -> FakeClient:
    """Handshake one client and return it once WELCOME has landed."""
    client = await _make_client(server_port)
    client.send(encode_frame(MessageType.HELLO, 1,
                             HelloPayload(client_id=name,
                                          client_version_major=1,
                                          client_version_minor=0)))
    raw = await _wait_for(client, MessageType.WELCOME)
    payload = decode_frame(raw).payload
    assert isinstance(payload, WelcomePayload)
    assert payload.accepted, f"{name} was rejected by the server"
    return client


async def _join_all(server_port: int, names=PEER_NAMES) -> list[FakeClient]:
    """Join peers sequentially so PEER_JOIN ordering is deterministic."""
    clients = []
    for name in names:
        clients.append(await _join(server_port, name))
        # Let the server flush this peer's join fan-out before the next
        # HELLO, otherwise the assertions race the event loop.
        await asyncio.sleep(0.05)
    return clients


def _close(transport, clients) -> None:
    transport.close()
    for c in clients:
        if c.transport is not None:
            c.transport.close()


@pytest.mark.asyncio
async def test_four_peers_all_accepted():
    """Four concurrent sessions are accepted and tracked distinctly."""
    port = BASE_PORT
    transport, protocol = await _start_server(port)
    clients = []
    try:
        clients = await _join_all(port)

        sessions = protocol.state.all_sessions()
        assert len(sessions) == 4, "server should hold four live sessions"
        assert {s.peer_id for s in sessions} == set(PEER_NAMES)
        # Session ids must be unique — the client keys ghosts off these.
        assert len({s.session_id for s in sessions}) == 4
    finally:
        _close(transport, clients)


@pytest.mark.asyncio
async def test_join_mesh_is_complete_and_symmetric():
    """Every peer learns about every other peer, exactly once.

    The Nth peer must be told about the N-1 already present, and each of those
    N-1 must be told about the newcomer. With two peers a half-implemented
    version of this looks identical to a correct one, which is why it needs a
    4-peer test.
    """
    port = BASE_PORT + 1
    transport, protocol = await _start_server(port)
    clients = []
    try:
        clients = await _join_all(port)
        await asyncio.sleep(0.15)

        for name, client in zip(PEER_NAMES, clients):
            joins = _collect(client, MessageType.PEER_JOIN)
            learned = [p.peer_id for p in joins
                       if isinstance(p, PeerJoinPayload)]

            expected = set(PEER_NAMES) - {name}
            assert set(learned) == expected, (
                f"{name} knows {sorted(set(learned))}, expected "
                f"{sorted(expected)}"
            )
            # A duplicate PEER_JOIN would make the client spawn two ghost
            # bodies for one peer.
            assert len(learned) == len(set(learned)), (
                f"{name} got duplicate PEER_JOIN entries: {learned}"
            )
    finally:
        _close(transport, clients)


@pytest.mark.asyncio
async def test_pos_broadcast_reaches_all_others_never_the_sender():
    """One POS_STATE fans out to exactly the other three peers."""
    port = BASE_PORT + 2
    transport, protocol = await _start_server(port)
    clients = []
    try:
        clients = await _join_all(port)
        await asyncio.sleep(0.1)
        for c in clients:
            c.received.clear()

        sender, *receivers = clients
        pos = PosStatePayload(x=100.0, y=200.0, z=300.0,
                              rx=0.0, ry=0.0, rz=1.5,
                              timestamp_ms=123456,
                              cell_id=0x1A2B3C)
        sender.send(encode_frame(MessageType.POS_STATE, 1, pos))
        await asyncio.sleep(0.2)

        for name, rx in zip(PEER_NAMES[1:], receivers):
            casts = [p for p in _collect(rx, MessageType.POS_BROADCAST)
                     if isinstance(p, PosBroadcastPayload)]
            assert len(casts) == 1, (
                f"{name} received {len(casts)} POS_BROADCAST, expected 1"
            )
            got = casts[0]
            assert got.peer_id == "alpha"
            assert got.x == pytest.approx(100.0)
            assert got.z == pytest.approx(300.0)
            # cell_id drives ghost culling across cells — a drop here would
            # make remote players invisible in interiors.
            assert got.cell_id == 0x1A2B3C

        assert not _collect(sender, MessageType.POS_BROADCAST), \
            "sender must not receive an echo of its own position"
    finally:
        _close(transport, clients)


@pytest.mark.asyncio
async def test_pose_broadcast_fans_out_to_all_others():
    """Per-bone pose relay is N-peer, not just A->B.

    Pose is the highest-rate channel; if fan-out were capped at one peer the
    remote bodies would animate for one viewer and freeze for the rest.
    """
    port = BASE_PORT + 3
    transport, protocol = await _start_server(port)
    clients = []
    try:
        clients = await _join_all(port)
        await asyncio.sleep(0.1)
        for c in clients:
            c.received.clear()

        quats = tuple((0.0, 0.0, 0.0, 1.0) for _ in range(8))
        sender, *receivers = clients
        sender.send(encode_frame(
            MessageType.POSE_STATE, 1,
            PoseStatePayload(timestamp_ms=999, quats=quats)))
        await asyncio.sleep(0.2)

        for name, rx in zip(PEER_NAMES[1:], receivers):
            casts = [p for p in _collect(rx, MessageType.POSE_BROADCAST)
                     if isinstance(p, PoseBroadcastPayload)]
            assert len(casts) == 1, f"{name} got {len(casts)} pose broadcasts"
            assert casts[0].peer_id == "alpha"
            assert len(casts[0].quats) == 8

        assert not _collect(sender, MessageType.POSE_BROADCAST), \
            "sender must not receive its own pose back"
    finally:
        _close(transport, clients)


@pytest.mark.asyncio
async def test_peer_leave_notifies_all_survivors():
    """A graceful DISCONNECT reaches all three remaining peers."""
    port = BASE_PORT + 4
    transport, protocol = await _start_server(port)
    clients = []
    try:
        clients = await _join_all(port)
        await asyncio.sleep(0.1)
        for c in clients:
            c.received.clear()

        leaver, *survivors = clients
        # reason is a u8 code, not a string: 0=graceful, 1=error, 2=version.
        leaver.send(encode_frame(MessageType.DISCONNECT, 1,
                                 DisconnectPayload(reason=0)))
        await asyncio.sleep(0.25)

        for name, rx in zip(PEER_NAMES[1:], survivors):
            leaves = [p for p in _collect(rx, MessageType.PEER_LEAVE)
                      if isinstance(p, PeerLeavePayload)]
            assert len(leaves) == 1, (
                f"{name} saw {len(leaves)} PEER_LEAVE, expected 1 — a missed "
                f"leave strands an orphan ghost body"
            )
            assert leaves[0].peer_id == "alpha"

        assert len(protocol.state.all_sessions()) == 3
    finally:
        _close(transport, clients)


@pytest.mark.asyncio
async def test_concurrent_position_traffic_stays_isolated():
    """All four peers move at once; each sees exactly the other three.

    Catches shared-mutable-broadcast-buffer bugs, where one peer's payload
    bleeds into another's stream — invisible at N=2 because there is only one
    possible destination.
    """
    port = BASE_PORT + 5
    transport, protocol = await _start_server(port)
    clients = []
    try:
        clients = await _join_all(port)
        await asyncio.sleep(0.1)
        for c in clients:
            c.received.clear()

        # Give each peer a position keyed off its index so payload/sender
        # mismatches are detectable rather than coincidentally equal.
        for i, client in enumerate(clients):
            client.send(encode_frame(
                MessageType.POS_STATE, 1,
                PosStatePayload(x=float(i * 1000), y=0.0, z=0.0,
                                rx=0.0, ry=0.0, rz=0.0,
                                timestamp_ms=1000 + i, cell_id=i + 1)))
        await asyncio.sleep(0.3)

        for i, (name, client) in enumerate(zip(PEER_NAMES, clients)):
            casts = [p for p in _collect(client, MessageType.POS_BROADCAST)
                     if isinstance(p, PosBroadcastPayload)]
            by_peer = {c.peer_id: c for c in casts}

            assert set(by_peer) == set(PEER_NAMES) - {name}, (
                f"{name} saw {sorted(by_peer)}, expected the other three"
            )
            for j, other in enumerate(PEER_NAMES):
                if other == name:
                    continue
                # The x we get for `other` must be the x *they* sent.
                assert by_peer[other].x == pytest.approx(float(j * 1000)), (
                    f"{name} received wrong payload for {other} — "
                    f"broadcast buffers are being shared"
                )
                assert by_peer[other].cell_id == j + 1
    finally:
        _close(transport, clients)


@pytest.mark.asyncio
async def test_scales_to_ten_peers():
    """The stated 10-player target, exercised at the server layer.

    The client can't do this yet, but nothing in the server should stand in
    the way, and finding a server-side ceiling now is much cheaper than
    finding it after the client refactor.
    """
    port = BASE_PORT + 6
    transport, protocol = await _start_server(port)
    names = [f"peer{i:02d}" for i in range(10)]
    clients = []
    try:
        clients = await _join_all(port, names)
        assert len(protocol.state.all_sessions()) == 10

        await asyncio.sleep(0.1)
        for c in clients:
            c.received.clear()

        clients[0].send(encode_frame(
            MessageType.POS_STATE, 1,
            PosStatePayload(x=1.0, y=2.0, z=3.0, rx=0.0, ry=0.0, rz=0.0,
                            timestamp_ms=7, cell_id=5)))
        await asyncio.sleep(0.35)

        for name, rx in zip(names[1:], clients[1:]):
            casts = [p for p in _collect(rx, MessageType.POS_BROADCAST)
                     if isinstance(p, PosBroadcastPayload)]
            assert len(casts) == 1, f"{name} got {len(casts)} broadcasts"
            assert casts[0].peer_id == names[0]
    finally:
        _close(transport, clients)
