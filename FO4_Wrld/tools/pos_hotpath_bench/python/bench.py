"""
POS hot-path microbench — extract of FO4_Wrld server POS path.

Mirrors (stripped of asyncio/logging/OOP):
  protocol.PosStatePayload decode  (<6fQI)
  validator.validate_pos_state     (finite, dt, speed, vertical, cell)
  POS_BROADCAST encode to N-1 peers (16B peer_id + body)

This is the per-packet work on the server for every walking peer.
At 4 peers × 50 Hz = 200 packets/s. If Python clears millions ops/s,
the language is not the 4-player smoothness bottleneck.
"""
from __future__ import annotations

import math
import struct
import time
import sys

MAX_SPEED = 2500.0
MAX_VSPEED = 5000.0
MIN_DT_MS = 20.0
COORD_BOUND = 1e7
POS_FMT = struct.Struct("<6fQI")  # 36 bytes
PEER_ID_LEN = 16  # FixedClientId = 15 + NUL

OK = 0
TOO_FAST = 5
SPEED = 2
VERTICAL = 4
NON_FINITE = 10


class Peer:
    __slots__ = ("peer_id", "last_x", "last_y", "last_z", "last_ts", "last_cell", "last_at_ms", "has")

    def __init__(self, peer_id: bytes):
        self.peer_id = peer_id  # 16 bytes
        self.last_x = self.last_y = self.last_z = 0.0
        self.last_ts = 0
        self.last_cell = 0
        self.last_at_ms = 0.0
        self.has = False


def unpack_pos(buf: memoryview, off: int):
    return POS_FMT.unpack_from(buf, off)


def validate(peer: Peer, x, y, z, rx, ry, rz, ts, cell, now_ms: float) -> int:
    coords = (x, y, z, rx, ry, rz)
    if not all(math.isfinite(v) for v in coords) or any(abs(v) > COORD_BOUND for v in (x, y, z)):
        return NON_FINITE
    if not peer.has:
        return OK
    dt_ms = now_ms - peer.last_at_ms
    if dt_ms < MIN_DT_MS:
        return TOO_FAST
    if ts < peer.last_ts:
        return 3  # TIMESTAMP_INVERTED
    if cell != 0 and peer.last_cell != 0 and cell != peer.last_cell:
        return OK  # cell teleport bypass
    dt_s = dt_ms / 1000.0
    dx = x - peer.last_x
    dy = y - peer.last_y
    dz = z - peer.last_z
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist / dt_s > MAX_SPEED:
        return SPEED
    if abs(dz) / dt_s > MAX_VSPEED:
        return VERTICAL
    return OK


def encode_bcast(out: bytearray, off: int, peer_id: bytes, body: bytes) -> int:
    out[off : off + PEER_ID_LEN] = peer_id
    out[off + PEER_ID_LEN : off + PEER_ID_LEN + 36] = body
    return PEER_ID_LEN + 36


def run(iters: int, n_peers: int = 4) -> None:
    peers = [Peer(f"player_{i}".encode("ascii").ljust(PEER_ID_LEN, b"\0")) for i in range(n_peers)]
    # Prebuild lawful POS packets: 50 Hz, ~300 u/s walk
    bodies = []
    for i, p in enumerate(peers):
        x = -76000.0 + i * 100.0
        y = 93000.0
        z = 7700.0
        ts = 1_000_000 + i
        body = POS_FMT.pack(x, y, z, 0.0, 0.0, 0.1, ts, 0x00024A02)
        bodies.append(bytearray(body))

    out = bytearray((PEER_ID_LEN + 36) * (n_peers - 1))
    accepts = rejects = 0
    now = 1_000_000.0

    t0 = time.perf_counter()
    for k in range(iters):
        for i, peer in enumerate(peers):
            # advance walk a little each tick
            b = bodies[i]
            x, y, z, rx, ry, rz, ts, cell = unpack_pos(b, 0)
            x += 6.0  # ~300 u/s at 50Hz
            ts += 20
            b[:] = POS_FMT.pack(x, y, z, rx, ry, rz, ts, cell)
            now += 20.0  # one peer step; multi-peer interleaved
            reason = validate(peer, x, y, z, rx, ry, rz, ts, cell, now)
            if reason != OK:
                rejects += 1
                # still update baseline on TOO_FAST? real server doesn't; skip update
                if reason == TOO_FAST:
                    continue
                continue
            accepts += 1
            peer.last_x, peer.last_y, peer.last_z = x, y, z
            peer.last_ts = ts
            peer.last_cell = cell
            peer.last_at_ms = now
            peer.has = True
            # fan-out encode to other peers
            o = 0
            body = bytes(b)
            for j, other in enumerate(peers):
                if j == i:
                    continue
                o += encode_bcast(out, o, peer.peer_id, body)
    t1 = time.perf_counter()

    elapsed = t1 - t0
    ops = iters * n_peers
    print(f"lang=python peers={n_peers} iters={iters} ops={ops}")
    print(f"elapsed_s={elapsed:.6f} ns_per_op={elapsed * 1e9 / ops:.1f} ops_per_s={ops / elapsed:,.0f}")
    print(f"accepts={accepts} rejects={rejects}")


if __name__ == "__main__":
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 500_000
    run(iters)
