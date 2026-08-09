# POS hot-path microbench

Tiny extract of the FO4_Wrld **Python server** position path, ported to several languages.

## What is measured (one "op")

For each peer packet:

1. **Decode** `POS_STATE` wire body (`<6fQI` = 36 bytes LE)
2. **Validate** (same rules as `net/server/validator.py`):
   - finite / bounded coords  
   - min interval 20 ms  
   - timestamp non-decreasing  
   - cell-change teleport bypass  
   - 3D speed cap 2500 u/s  
   - vertical speed cap 5000 u/s  
3. **Encode** `POS_BROADCAST` to the other 3 peers (16-byte peer id + 36-byte body)

Default: **4 peers × 500 000 rounds = 2 000 000 ops**.

This is **not** a full server (no asyncio, UDP, reliability, NPC brains). It answers:  
“Is **language** the reason 4 players feel bad?”

## Run

```powershell
cd FO4_Wrld\tools\pos_hotpath_bench
powershell -ExecutionPolicy Bypass -File .\run_all.ps1 500000
```

## Results (this machine, 2026-08-02)

| Language | ns/op | ops/s | vs Python |
|----------|------:|------:|----------:|
| **Rust** | 7.6 | 131 M | **~544×** |
| **C++ (-O3)** | 9.8 | 102 M | **~423×** |
| **C (-O3)** | 11.2 | 89.5 M | **~371×** |
| **Go** | 60 | 16.6 M | **~69×** |
| **Node (V8)** | 512 | 1.95 M | **~8×** |
| **Python 3.12** | 4147 | 241 k | 1× |

All runs: `accepts=2000000 rejects=0` (same synthetic walk stream).

## What this means for 4-player smoothness

Budget at **50 Hz POS** from each of 4 peers:

- Packets in: `4 × 50 = 200 /s`
- Each packet does 1 validate + 3 fan-out encodes → still **O(200–800) ops/s** of this hot path

Python cleared **~241 000 ops/s** on this slice alone → **~1000× headroom** vs that budget  
before counting asyncio, sockets, or NPC logic.

So for **4 people**:

| Concern | Verdict |
|---------|---------|
| POS decode/validate/fan-out in pure CPU | **Python is fine** |
| Smoothness at 4 peers | Bottleneck is almost certainly **game/hooks/net latency**, not Python |
| 32–64 peers / 100 Hz / heavy pose bones | Revisit (Go/Rust server becomes attractive) |

## Why people still rewrite servers

- Lower tail latency under GC / GIL  
- One binary deploy  
- Easier hard real-time budgets at large N  
- Shared code with a native client  

Those are real, but **not required for 4-player FO4 at 20–50 Hz** if the rest of the stack is clean.

## Files

| Path | Lang |
|------|------|
| `python/bench.py` | Python (reference, mirrors validator) |
| `rust/` | Rust |
| `cpp/bench.cpp` | C++17 |
| `c/bench.c` | C11 |
| `go/bench.go` | Go |
| `node/bench.js` | Node.js |
| `run_all.ps1` | Build + run + write `results/` |
