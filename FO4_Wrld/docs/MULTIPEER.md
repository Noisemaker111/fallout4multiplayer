# Scaling past 2 peers

Target: **4+ concurrent players** (the README's stated ceiling is 10).

## Where the ceiling actually is

Not where the README implies. The server is already there.

`net/tests/test_multipeer.py` runs the real asyncio server and verifies, at
N=4 and again at N=10:

- all sessions accepted with distinct session ids
- the join mesh is complete and symmetric — every peer learns every other peer,
  exactly once, with no duplicates
- `POS_BROADCAST` and `POSE_BROADCAST` fan out to exactly N-1 peers and never
  echo to the sender
- concurrent traffic from all N peers stays isolated (peer X's payload never
  surfaces in peer Y's stream — the classic shared-broadcast-buffer bug that is
  invisible at N=2 because there is only one possible destination)
- `PEER_LEAVE` reaches every survivor

All pass. **The entire remaining gap is client-side**, and it is narrower than
it looks: the pieces that are hard to write (equipment, OMOD, mesh blobs) were
already written peer-keyed.

## What is already N-peer

| Layer | State |
|---|---|
| Python server (`net/server/`) | ✅ built on `all_sessions()` / `other_sessions()`; verified to 10 |
| Wire protocol | ✅ every broadcast payload carries `peer_id` |
| Equipment / armor / weapon attach (`scene_inject`) | ✅ already keyed `(peer_id, form_id)` |
| Mesh-blob reassembly (`client.h`) | ✅ already keyed `(peer_id, equip_seq)` |
| Net-layer position snapshots (`client.cpp`) | ✅ **done** — `remote_snapshots_` is a `peer_id → snapshot` map |
| Pose + crouch stash (`scene_inject.cpp`) | ✅ **done** — `g_remote_poses` / `g_remote_crouches` keyed by peer |
| Ghost slot registry (`native/ghost_registry.*`) | ✅ **done** — per-peer body + form_id + bone tables, 70 unit checks |
| Peer lifecycle → registry | ✅ **done** — `PEER_JOIN` calls `ghosts::ensure`, `PEER_LEAVE` calls `ghosts::forget` |

## What is still single-ghost

### 1. Body injection is still one body — `scene_inject.cpp`

The registry now exists and can hold N bodies, but only one is ever *created*.
`inject_debug_cube()` early-returns if a body already exists, and its two
writers go through `ghosts::set_primary_body()` / `take_primary_body()`.

Around 24 readers still use the `g_injected_cube` atomic. That atomic is
deliberately kept as a lock-free read cache for the primary peer, because
several of those readers sit inside SEH `__try` blocks where a `std::lock_guard`
is a hard compile error (C2712). Each reader that becomes peer-aware moves to
`ghosts::body_for(peer)`; the cache shrinks toward deletion as they do.

**Remaining change:** inject one body per peer instead of one globally, and
populate `ghosts::set_bones(peer, …)` from that peer's own skin instance
instead of the file-scope `g_canonical_names` / `g_ghost_bone_ptrs`
(`scene_inject.cpp:68-69`). The registry already stores bone tables per peer
and clears them on `take_body`; nothing reads them yet.

Also still single: `g_bone_pairs` / `g_cached_ghost_body`
(`scene_inject.cpp:~10270`) and `g_ghost_map_cached` (`~10430`).

Spawning is already dynamic, so N bodies means allocating
`GHOST_FORMID_BASE + n` per peer. No pre-placed actors are needed.

Budget the bulk of the work here. `scene_inject.cpp` is 12k lines.

### 1b. `g_ghost_actor` — dead code, ignore it

`actor_hijack.cpp:74` holds a single `g_ghost_actor`, but the proxy-Actor spawn
is **disabled** (`g_proxy_spawn_disabled{true}`, Build 65.c.34) because it
rooted a reload crash. The visible ghost is the injected NiNode body, not this
Actor. Don't spend effort making it multi-peer — it is never non-null today.

### 2. Pose apply drives one body

The *stash* is now per-peer (`store_remote_pose(peer_id, …)`), so no peer's
pose is lost. But `on_pose_apply_message()` still applies only the most
recently updated peer to the single body, and reads the file-scope
`g_ghost_bone_ptrs`. Once bodies are per-peer, change it to iterate dirty peers
and use `ghosts::bone_ptrs_for(peer)`.

### 3. Ghost position apply — `engine_calls.cpp:2200`

`apply_ghost_pos(x, y, z)` drives *the* ghost duplicate. Needs a `peer_id` (or
a resolved ghost handle) so it drives the right one. One call site,
`client.cpp:1450`.

### 4. Config — `config.h:22-25`

```
std::string   ghost_map_peer_id;
std::uint32_t ghost_map_form_id = 0;   // "Single entry for MVP"
```

**Change:** drop it. Assign synthetic form ids per peer at join time instead of
configuring one mapping. The legacy `ghost_map` consumer in `client.cpp` is
already commented out (dead since 2026-04-29), so nothing depends on it.

### 5. Launcher — `launcher/`, `fw_config.py`

Hardwired to Side A / Side B: `SideConfig.other_peer_id` is singular and
`fw_config.ini` writes one `ghost_map` line.

**Change:** parameterise by index (`--side 0..N`) and emit one config per
instance. Only needed to run several clients *on one machine* for testing —
four players on four machines don't need it. Note local multi-instance also
needs the single-instance bypass patch (README, RVA `0xC2FB62`) applied per
instance.

## Suggested order

Done so far (all with the build green and behaviour unchanged at N=1):

1. ~~Pose/crouch peer-keying~~ ✅
2. ~~`GhostSlot` registry, wired to peer join/leave~~ ✅

Remaining:

3. **Inject one body per peer** (#1) — the big one. Do it behind a peer count
   cap (start at 2, raise to 4) so a regression is one constant away from being
   reverted.
4. **Per-peer bone tables** — populate `ghosts::set_bones` at inject and switch
   `on_pose_apply_message` to iterate. Storage already exists and is tested.
5. **`apply_ghost_pos` peer-keying** (#3) and **config cleanup** (#4).
6. **Launcher N-instance** (#5) — only when local N-client testing is wanted.

Steps 3-4 are the first ones that change behaviour, and they are the first that
genuinely need the game running to validate.

## Testing reality

Three tiers, and it is worth being precise about which covers what:

- **Server** — `net/tests/` (346 tests). Real coverage, including N=4 and N=10.
- **Engine-independent client logic** — `fw_native/tests/run_tests.bat`
  (70 checks over the registry). Real coverage, no game needed.
- **Anything touching the scene graph or a hooked address** — **no automated
  coverage is possible off-target.** Needs `Fallout4.exe` 1.11.191 (see
  [GAME_VERSION.md](GAME_VERSION.md)) and N running instances.

For that third tier the only signals are a green build and code review, so
prefer small staged commits over one large refactor.

## Known unknowns at N > 2

Called out in the README and unresolved:

- **Bandwidth.** Pose is the fat channel and fan-out is O(N²) at the server.
  20 Hz × 4 peers is fine on LAN; 10 peers over the internet is untested and
  probably wants interest management (only send peers in the same cell — the
  `cell_id` already on the wire is enough to filter on) plus receiver-side
  interpolation.
- **NPC ownership election** assumes a small peer set. `net/server/ownership.py`
  has hysteresis tuned against 2 peers; 4 peers trading fire on one raider may
  thrash. Worth re-tuning once 4-peer combat actually runs.
