// POS hot-path microbench — same algorithm as python/bench.py
"use strict";

const MAX_SPEED = 2500.0;
const MAX_VSPEED = 5000.0;
const MIN_DT_MS = 20.0;
const COORD_BOUND = 1e7;
const PEER_ID_LEN = 16;
const BODY_LEN = 36;
const OK = 0,
  TOO_FAST = 5,
  SPEED = 2,
  VERTICAL = 4,
  NON_FINITE = 10,
  TS_INV = 3;
const N_PEERS = 4;

function packPos(buf, off, x, y, z, rx, ry, rz, ts, cell) {
  buf.writeFloatLE(x, off + 0);
  buf.writeFloatLE(y, off + 4);
  buf.writeFloatLE(z, off + 8);
  buf.writeFloatLE(rx, off + 12);
  buf.writeFloatLE(ry, off + 16);
  buf.writeFloatLE(rz, off + 20);
  buf.writeBigUInt64LE(BigInt(ts), off + 24);
  buf.writeUInt32LE(cell >>> 0, off + 32);
}

function unpackPos(buf, off) {
  return {
    x: buf.readFloatLE(off + 0),
    y: buf.readFloatLE(off + 4),
    z: buf.readFloatLE(off + 8),
    rx: buf.readFloatLE(off + 12),
    ry: buf.readFloatLE(off + 16),
    rz: buf.readFloatLE(off + 20),
    ts: Number(buf.readBigUInt64LE(off + 24)),
    cell: buf.readUInt32LE(off + 32),
  };
}

function validate(peer, x, y, z, rx, ry, rz, ts, cell, nowMs) {
  const coords = [x, y, z, rx, ry, rz];
  for (const v of coords) {
    if (!Number.isFinite(v)) return NON_FINITE;
  }
  if (Math.abs(x) > COORD_BOUND || Math.abs(y) > COORD_BOUND || Math.abs(z) > COORD_BOUND)
    return NON_FINITE;
  if (!peer.has) return OK;
  const dtMs = nowMs - peer.lastAtMs;
  if (dtMs < MIN_DT_MS) return TOO_FAST;
  if (ts < peer.lastTs) return TS_INV;
  if (cell !== 0 && peer.lastCell !== 0 && cell !== peer.lastCell) return OK;
  const dtS = dtMs / 1000.0;
  const dx = x - peer.lastX,
    dy = y - peer.lastY,
    dz = z - peer.lastZ;
  const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (dist / dtS > MAX_SPEED) return SPEED;
  if (Math.abs(dz) / dtS > MAX_VSPEED) return VERTICAL;
  return OK;
}

function run(iters) {
  const peers = [];
  const bodies = [];
  for (let i = 0; i < N_PEERS; i++) {
    const id = Buffer.alloc(PEER_ID_LEN);
    id.write(`player_${i}`);
    peers.push({
      peerId: id,
      lastX: 0,
      lastY: 0,
      lastZ: 0,
      lastTs: 0,
      lastCell: 0,
      lastAtMs: 0,
      has: false,
    });
    const b = Buffer.alloc(BODY_LEN);
    packPos(b, 0, -76000 + i * 100, 93000, 7700, 0, 0, 0.1, 1000000 + i, 0x00024a02);
    bodies.push(b);
  }
  const out = Buffer.alloc((PEER_ID_LEN + BODY_LEN) * (N_PEERS - 1));
  let accepts = 0,
    rejects = 0;
  let now = 1000000.0;
  const t0 = process.hrtime.bigint();
  for (let k = 0; k < iters; k++) {
    for (let i = 0; i < N_PEERS; i++) {
      let { x, y, z, rx, ry, rz, ts, cell } = unpackPos(bodies[i], 0);
      x += 6.0;
      ts += 20;
      packPos(bodies[i], 0, x, y, z, rx, ry, rz, ts, cell);
      now += 20.0;
      const reason = validate(peers[i], x, y, z, rx, ry, rz, ts, cell, now);
      if (reason !== OK) {
        rejects++;
        continue;
      }
      accepts++;
      const p = peers[i];
      p.lastX = x;
      p.lastY = y;
      p.lastZ = z;
      p.lastTs = ts;
      p.lastCell = cell;
      p.lastAtMs = now;
      p.has = true;
      let o = 0;
      for (let j = 0; j < N_PEERS; j++) {
        if (j === i) continue;
        peers[i].peerId.copy(out, o);
        bodies[i].copy(out, o + PEER_ID_LEN);
        o += PEER_ID_LEN + BODY_LEN;
      }
    }
  }
  const t1 = process.hrtime.bigint();
  const elapsed = Number(t1 - t0) / 1e9;
  const ops = iters * N_PEERS;
  console.log(`lang=node peers=${N_PEERS} iters=${iters} ops=${ops}`);
  console.log(
    `elapsed_s=${elapsed.toFixed(6)} ns_per_op=${((elapsed * 1e9) / ops).toFixed(1)} ops_per_s=${(ops / elapsed).toFixed(0)}`
  );
  console.log(`accepts=${accepts} rejects=${rejects}`);
}

const iters = process.argv[2] ? parseInt(process.argv[2], 10) : 500000;
run(iters);
