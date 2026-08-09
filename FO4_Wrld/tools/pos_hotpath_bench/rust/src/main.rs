//! POS hot-path microbench — same algorithm as python/bench.py
use std::env;
use std::time::Instant;

const MAX_SPEED: f64 = 2500.0;
const MAX_VSPEED: f64 = 5000.0;
const MIN_DT_MS: f64 = 20.0;
const COORD_BOUND: f64 = 1e7;
const PEER_ID_LEN: usize = 16;
const BODY_LEN: usize = 36;

const OK: u8 = 0;
const TOO_FAST: u8 = 5;
const SPEED: u8 = 2;
const VERTICAL: u8 = 4;
const NON_FINITE: u8 = 10;
const TS_INV: u8 = 3;

struct Peer {
    peer_id: [u8; PEER_ID_LEN],
    last_x: f32,
    last_y: f32,
    last_z: f32,
    last_ts: u64,
    last_cell: u32,
    last_at_ms: f64,
    has: bool,
}

#[inline]
fn pack_pos(buf: &mut [u8], x: f32, y: f32, z: f32, rx: f32, ry: f32, rz: f32, ts: u64, cell: u32) {
    buf[0..4].copy_from_slice(&x.to_le_bytes());
    buf[4..8].copy_from_slice(&y.to_le_bytes());
    buf[8..12].copy_from_slice(&z.to_le_bytes());
    buf[12..16].copy_from_slice(&rx.to_le_bytes());
    buf[16..20].copy_from_slice(&ry.to_le_bytes());
    buf[20..24].copy_from_slice(&rz.to_le_bytes());
    buf[24..32].copy_from_slice(&ts.to_le_bytes());
    buf[32..36].copy_from_slice(&cell.to_le_bytes());
}

#[inline]
fn unpack_pos(buf: &[u8]) -> (f32, f32, f32, f32, f32, f32, u64, u32) {
    let x = f32::from_le_bytes(buf[0..4].try_into().unwrap());
    let y = f32::from_le_bytes(buf[4..8].try_into().unwrap());
    let z = f32::from_le_bytes(buf[8..12].try_into().unwrap());
    let rx = f32::from_le_bytes(buf[12..16].try_into().unwrap());
    let ry = f32::from_le_bytes(buf[16..20].try_into().unwrap());
    let rz = f32::from_le_bytes(buf[20..24].try_into().unwrap());
    let ts = u64::from_le_bytes(buf[24..32].try_into().unwrap());
    let cell = u32::from_le_bytes(buf[32..36].try_into().unwrap());
    (x, y, z, rx, ry, rz, ts, cell)
}

#[inline]
fn finite(v: f32) -> bool {
    v.is_finite()
}

#[inline]
fn validate(peer: &Peer, x: f32, y: f32, z: f32, rx: f32, ry: f32, rz: f32, ts: u64, cell: u32, now_ms: f64) -> u8 {
    if !finite(x) || !finite(y) || !finite(z) || !finite(rx) || !finite(ry) || !finite(rz) {
        return NON_FINITE;
    }
    if x.abs() as f64 > COORD_BOUND || y.abs() as f64 > COORD_BOUND || z.abs() as f64 > COORD_BOUND {
        return NON_FINITE;
    }
    if !peer.has {
        return OK;
    }
    let dt_ms = now_ms - peer.last_at_ms;
    if dt_ms < MIN_DT_MS {
        return TOO_FAST;
    }
    if ts < peer.last_ts {
        return TS_INV;
    }
    if cell != 0 && peer.last_cell != 0 && cell != peer.last_cell {
        return OK;
    }
    let dt_s = dt_ms / 1000.0;
    let dx = (x - peer.last_x) as f64;
    let dy = (y - peer.last_y) as f64;
    let dz = (z - peer.last_z) as f64;
    let dist = (dx * dx + dy * dy + dz * dz).sqrt();
    if dist / dt_s > MAX_SPEED {
        return SPEED;
    }
    if dz.abs() / dt_s > MAX_VSPEED {
        return VERTICAL;
    }
    OK
}

fn run(iters: usize, n_peers: usize) {
    let mut peers: Vec<Peer> = (0..n_peers)
        .map(|i| {
            let mut id = [0u8; PEER_ID_LEN];
            let s = format!("player_{i}");
            id[..s.len()].copy_from_slice(s.as_bytes());
            Peer {
                peer_id: id,
                last_x: 0.0,
                last_y: 0.0,
                last_z: 0.0,
                last_ts: 0,
                last_cell: 0,
                last_at_ms: 0.0,
                has: false,
            }
        })
        .collect();

    let mut bodies: Vec<[u8; BODY_LEN]> = (0..n_peers)
        .map(|i| {
            let mut b = [0u8; BODY_LEN];
            pack_pos(
                &mut b,
                -76000.0 + i as f32 * 100.0,
                93000.0,
                7700.0,
                0.0,
                0.0,
                0.1,
                1_000_000 + i as u64,
                0x0002_4A02,
            );
            b
        })
        .collect();

    let mut out = vec![0u8; (PEER_ID_LEN + BODY_LEN) * (n_peers - 1)];
    let mut accepts = 0u64;
    let mut rejects = 0u64;
    let mut now = 1_000_000.0f64;

    let t0 = Instant::now();
    for _ in 0..iters {
        for i in 0..n_peers {
            let (mut x, y, z, rx, ry, rz, mut ts, cell) = unpack_pos(&bodies[i]);
            x += 6.0;
            ts += 20;
            pack_pos(&mut bodies[i], x, y, z, rx, ry, rz, ts, cell);
            now += 20.0;
            let reason = validate(&peers[i], x, y, z, rx, ry, rz, ts, cell, now);
            if reason != OK {
                rejects += 1;
                continue;
            }
            accepts += 1;
            {
                let p = &mut peers[i];
                p.last_x = x;
                p.last_y = y;
                p.last_z = z;
                p.last_ts = ts;
                p.last_cell = cell;
                p.last_at_ms = now;
                p.has = true;
            }
            let mut o = 0;
            let body = bodies[i];
            let pid = peers[i].peer_id;
            for j in 0..n_peers {
                if j == i {
                    continue;
                }
                out[o..o + PEER_ID_LEN].copy_from_slice(&pid);
                out[o + PEER_ID_LEN..o + PEER_ID_LEN + BODY_LEN].copy_from_slice(&body);
                o += PEER_ID_LEN + BODY_LEN;
            }
        }
    }
    let elapsed = t0.elapsed().as_secs_f64();
    let ops = (iters * n_peers) as f64;
    println!("lang=rust peers={n_peers} iters={iters} ops={}", ops as u64);
    println!(
        "elapsed_s={elapsed:.6} ns_per_op={:.1} ops_per_s={:.0}",
        elapsed * 1e9 / ops,
        ops / elapsed
    );
    println!("accepts={accepts} rejects={rejects}");
}

fn main() {
    let iters = env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(500_000usize);
    run(iters, 4);
}
