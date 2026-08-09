# START HERE — A→Z to a playable game

Handoff document. Written 2026-07-28. If you are a new session with no context,
read this file first and nothing else.

---

## 0. Locked decisions — do not reopen

- **Target build: Fallout 4 `1.10.163`.** Settled. Do not propose next-gen, do
  not propose obtaining another build, do not re-litigate. The reasons are the
  mod ecosystem and that 1.10.163 is frozen and will never be patched again.
- **Analysis binary:** `C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe`
  (Steamless output, DRM stripped, section RVAs preserved — already verified).
- The game keeps its normal install. A second game directory is only needed for
  running two clients on one machine (step **N**).

---

## 1. Orientation in 60 seconds

`FO4_Wrld` is a Fallout 4 multiplayer client: a `dxgi.dll` proxy injected into
the game, plus a Python authoritative server. It is ~86k lines and genuinely
advanced — per-bone animation replication, equipment/weapon-mod sync,
containers, doors, locks, NPC co-op combat with a shared HP pool.

It was developed against Fallout 4 **1.11.191** and hardcodes **383 raw
addresses** for that build. On 1.10.163 those addresses point into the middle
of unrelated functions. **That is the entire reason the game does not run.**
Everything else about the project works.

The job is: re-derive the addresses for 1.10.163, get two clients talking,
then raise the player count to four.

---

## 2. Environment (already set up)

```bash
# Python (venv already exists at FO4_Wrld/.venv)
./.venv/Scripts/python.exe -m pytest net/tests -q        # 346 tests, all pass

# Native build (MSVC Build Tools 2022 installed; MinHook auto-clones)
cd fw_native && ./build.bat                              # -> build/dxgi.dll, 84/84

# Native unit tests (engine-independent logic only)
cd fw_native/tests && ./run_tests.bat                    # 70 checks, all pass

# Port-tooling tests (FW_MINIMAL scoping logic)
./.venv/Scripts/python.exe -m pytest tools/tests -q      # 15 checks, all pass
```

Note: `run_tests.bat` compiles fine but the executable it produces may be
blocked from running by a Device Guard / WDAC policy on some machines. That is
an environment restriction, not a code failure.

Note: `vcvars64.bat` prints `'vswhere.exe' is not recognized` on this machine.
It is Microsoft's own script doing that, it is harmless, and the build
succeeds. Do not chase it.

---

## 3. What is already done

| Area | State |
|---|---|
| Build toolchain, MinHook, portable `build.bat` | ✅ builds clean from a bare tree |
| Python server | ✅ 346 tests; **verified N-peer to 10 players** |
| Net-layer per-peer position snapshots | ✅ `remote_snapshots_` map in `client.cpp` |
| Per-peer pose + crouch stashes | ✅ `g_remote_poses` / `g_remote_crouches` |
| Ghost registry (`native/ghost_registry.*`) | ✅ per-peer body + form_id + bone tables, 70 unit checks |
| Peer join/leave → registry | ✅ wired |
| Port tooling | ✅ see §7 |

**The server and protocol are not the bottleneck and need no work for 4
players.** All remaining work is the client.

---

## 4. THE KEY INSIGHT — you do not need all 383 addresses

You need roughly a third of them to *play*. The address set splits by
subsystem, and most of it is optional for a first playable build:

**Required for minimum playable co-op (two players seeing each other move):**

| Subsystem | Where | Why |
|---|---|---|
| Core form/REFR access | `offsets.h` lines ~13-45 | player singleton, form lookup, pos/rot/cell fields |
| NIF loader + scene graph | `native/ni_offsets.h` (41 addrs) | injecting the ghost body |
| Skin pipeline / bone tables | `native/scene_inject.cpp` (22 addrs) | animating it |
| Engine helpers | `engine/engine_calls.cpp` (66 addrs, subset) | allocation, node attach, transforms |
| Player position read | `hooks/player_pos_hook.cpp` | sending your position |

**Deferrable — compile these hooks out for the first playable build:**

- every `hooks/ghost_ai_*.cpp` (14 files) — NPC combat sync
- `npc_ai_suppress`, `ownership_manager`, `puppet_update_perframe`,
  `npc_hp_probe`, `hp_bar_hook`, `ghost_combat_force`
- `ghost_hostility_guard*`, `ghost_vt197_guard`, `ghost_global_walker_guards`
  (13 addrs), `ghost_is_attachable`, `cross_cell_gate`, `event_dispatch_guard`
- `container_hook`, `put_hook`, `pickup_hook`, `door_hook`, `lock_hook`,
  `worldstate_hook`, `equip_*` — all nice-to-have, none needed to walk around
- `render/*` — mostly archived dead code already

**The skin/scene-graph work is also the best documented part of the project:**
`re/M8P*.txt` is 5,030 lines of dossiers covering exactly the NIF loader, the
`BSSkin::Instance` layout and the bone pointer cache. That is the hard RE, and
it is the part you do *not* have to rediscover — only re-locate on 1.10.163.

⚠️ Conversely, every dossier for the combat/AI/ghost layer is **missing from
the repo** (`re/B6.6w0_pair_AGENT_*` — 0 of 10, `c34_preflight_safety_AGENT.md`,
`B6.6w5_player_ctor_audit.md`, `reference_fo4_offsets.md`, `stradaB_*`). They
were never committed. For those ~153 addresses only inline code comments
survive. This is the strongest argument for deferring that whole layer.

---

## 5. The plan, A→Z

### Milestone 1 — the game launches with the DLL loaded

**A. Cut the address set down.** ✅ **DONE 2026-07-28.**
`FW_MINIMAL` compiles out 43 modules — the whole NPC-combat/AI layer, its crash
guards, containers/doors/locks/worldstate, equipment sync, and the M9 RE
diagnostics.

```bash
cd fw_native && ./build.bat --minimal      # -> build-minimal/dxgi.dll
cd fw_native && ./build.bat                # -> build/dxgi.dll, unchanged
./deploy.bat --minimal                     # ships the minimal DLL
```

Measured: 41 translation units / 451 KB, against 79 / 632 KB for a full build.
Both flavours build clean and have separate build trees, so switching back and
forth costs no reconfigure.

- `fw_native/minimal_exclude.txt` is the **single source of truth** for what
  "minimal" means. CMake and `tools/port_assess.py` both read it. Keep it pure
  ASCII — CMake's `file(STRINGS)` splits a line at the first non-ASCII byte,
  so one em dash in a comment silently turns into a bogus module stem.
- Modules whose header is included by something that stays carry an
  `#if FW_MINIMAL` stub block (pattern: `hooks/npc_ai_suppress.h`), so call
  sites compile unchanged. Modules reached only from `install_all.cpp` are
  guarded there.
- One caveat is recorded at the `install_ghost_target_resolve_guard` call site:
  it guards a player-death crash that is *not* ghost-specific, but it shares a
  file with the combat guards. First suspect if a minimal build dies ~8s after
  the local player dies.

**B. Regenerate the work-list for the reduced set.** ✅ **DONE 2026-07-28.**
```bash
./.venv/Scripts/python.exe tools/port_assess.py --minimal --csv docs/port_worklist_minimal.csv
```

|  | full | FW_MINIMAL |
|---|---|---|
| total pinned constants | 541 | **244** |
| FIELD (public lookup) | 158 | 50 |
| KNOWN (Address Library) | 43 | 26 |
| **NOVEL (hand RE in IDA)** | **340** | **168** |

297 constants dropped; `hooks/` NOVEL fell from 83 to 11, `engine/` from 70 to
42, and FIELD from 158 to 50 (most of that tier is AIProcess / CombatController
/ aim-controller internals that go with the combat layer). Work-lists:
`docs/port_worklist.csv` (full), `docs/port_worklist_minimal.csv` (the one that
matters for Milestone 1).

**B.1. Dead spawn paths in `engine/engine_calls.cpp` — done as part of B.**
`spawn_ghost_player` and `spawn_ghost_proxy` are `#if !FW_MINIMAL`, together
with the 16 engine function pointers `init()` only resolved for them, and the
now-unreachable tail of `actor_hijack.cpp:on_spawn_message()`. Neither spawn
path runs today: `spawn_ghost_player` is deprecated with no callers, and
`spawn_ghost_proxy` sits behind `g_proxy_spawn_disabled{true}` (§6).

⚠️ **How to extend this safely.** A guarded `init()` assignment leaves a null
function pointer. That does **not** fail the build — it crashes in-game at the
first call. Before guarding any further pointer, enumerate its readers across
the whole tree with `#if !FW_MINIMAL` regions stripped and confirm every one is
gone. Two pointers in that same block look like part of the spawn cluster and
are *not*: `g_bsfs_make` (read by `init()` itself, lines ~1111-1143) and
`g_set_position_engine` (read by `apply_ghost_pos`, which drives the visible
ghost). Both are commented `KEEP` in place.

**B.2. The combat tier — done as part of B.**
Two guarded ranges in `engine_calls.cpp` (weapon fire → aim resolution →
EnterCombat → perception trigger, and combat-extension synthesis →
`disable_actor_movement`), plus `apply_npc_combat_target`. Unblocked by cutting
the three NPC combat paths in `main_thread_dispatch.cpp` — fire, perception and
death — whose enqueue side survives as a no-op so `net/client.cpp` still
compiles and simply drops NPC packets it has no layer to apply.

Three things inside those ranges are deliberately **kept**, each marked in
place: `is_actor_sneaking` / `read_actor_posture_byte` (POS_BROADCAST encodes
local posture), `aim_actor_at_target_rotation_only` (a plain yaw setter the NPC
*position* drain calls next to `apply_npc_pos` — its placement among the combat
functions is historical), and `apply_ghost_pos`.

The compiler caught two dependencies the tree-wide caller scan missed, both
internal to `engine_calls.cpp`: `apply_npc_combat_target` calls into the
guarded tier, and the owner-state drain needed the yaw setter. Guard, build,
read the errors, adjust — that loop is the method; a caller scan alone is not
enough.

**B-next. What is left.**
`engine/` still holds 42 NOVEL, now in `init()` itself (~56 constants resolving
pointers for everything) and the fixed-selector / form-table deregister
cluster. Thinner returns from here, and `init()` is where a wrong guard yields
a null pointer rather than a build error.

`native/` (79 NOVEL) is the floor: the skin/scene-graph pipeline Milestone 2
genuinely needs. It is also the best-documented part of the project
(`re/M8P*.txt`), so it is re-location work, not discovery.

**C. Recover the FIELD tier.** 🟡 **IN PROGRESS 2026-08-02 — 38 confirmed / 0 mismatched (of 158 full; ~30 of 50 minimal).**

The tier is 50 constants in minimal scope, not 158. CommonLibF4's `master`
branch targets 1.10.163 exactly (`community` is the next-gen one — do **not**
point tooling at it), and annotates every member with its hex offset, so this
is mechanical:

```bash
git clone --depth 1 --branch master \
    https://github.com/Ryan-rsm-McKenzie/CommonLibF4.git
./.venv/Scripts/python.exe tools/field_verify.py --commonlib ./CommonLibF4
```

CommonLibF4 is a *reference*, not a build dependency — don't vendor it
(`.gitignore` already excludes `CommonLibF4/`).

**38 CONFIRMED unchanged** (was 20). Core block for minimum playable co-op is
fully covered: `FORMID_OFF` `FLAGS_OFF` `ROT_OFF` `POS_OFF` `PARENT_CELL_OFF`
`BASE_FORM_OFF` `REFR_INV_LIST_OFF`, inventory-list offsets, scene-graph
`NI_AV_{LOCAL,WORLD}_{TRANSLATE,ROTATE,SCALE}_OFF`, PlayerCamera buffer/state
slots, TESObjectARMO addon/priority/biped layout, `IANIMGRAPHHOLDER_OFF`,
`LOADED_REF_DATA_OFF`, `ACTOR_AIPROCESS_PTR_OFF` (real AIProcess* at `0x300`).
Next-gen did **not** move `TESObjectREFR` core fields on the way back to
1.10.163.

**Naming debt (CONFIRMED, do not "fix" the number):**
`ACTOR_AIPROCESS_OFF = 0x328` is **`Actor::combatController`**, not AIProcess.
CommonLibF4 puts `currentProcess` at `0x300` (already exposed here as
`ACTOR_AIPROCESS_PTR_OFF`) and `combatController` at `0x328`. Combat readers
want the controller pointer — leave the value, rename when convenient.
`PLAYER_CAMERA_ACTIVE_OFF = 0x1A0` is similarly mislabeled
(`furnitureCollisionGroup` in CommonLib); offset is right.

**Still UNMAPPED in minimal scope** (~20 of 50): NiCamera/frustum internals
(no `NiCamera` header in this CommonLib checkout), `LOADED_REF_DATA_3D_OFF`
(`LOADED_REF_DATA` is forward-declared only), AIProcess/CombatController
internals, and bag-index constants. Those need IDA or F4SE-adjacent headers.

Add new mappings to `MAP` / `SIZE_MAP` in `tools/field_verify.py` as you go —
the tool re-checks every mapped entry on demand.

**D. Recover the KNOWN tier (43 engine APIs).** 🟡 **STARTED 2026-08-02.**

Public Address Library dump for 1.10.163 (not the Nexus binary — a CSV dump
from the FO4 VR address-lib repo):

```bash
# already downloaded under tools/addresslib/ (gitignored, ~24 MB)
./.venv/Scripts/python.exe tools/known_lookup.py
```

Hand-mapped CommonLibF4 `REL::ID`s so far (6 of ~26 minimal KNOWN):

| Constant | ID | 1.10.163 RVA | Note |
|---|---|---|---|
| `PLAYER_SINGLETON_RVA` | 303410 | `0x05AA4388` | PlayerCharacter** slot |
| `PLAYER_CAMERA_SINGLETON_RVA` | 1171980 | `0x058CEB28` | PlayerCamera** slot |
| `ENGINE_HEAP_ALLOC_RVA` | 652767 | `0x01B0EFD0` | MemoryManager::Allocate — **VERIFY** vs matcher exact `0x01B0EE10` |
| `FORM_CACHE_SINGLETON_RVA` | 422985 | `0x058D36C8` | allForms map**, related to lookup |
| `TESOBJECTREFR_VTABLE_RVA` | 179707 | `0x02C87AE8` | VTABLE_IDs primary entry |
| `NI_CAMERA_VTABLE_RVA` | 1305073 | `0x02E15E38` | VTABLE_IDs primary entry |

CommonLib does form lookup via the allForms hash map rather than our free
function `LOOKUP_BY_FORMID_RVA` (`sub_140311850`), so that one stays IDA /
matcher. Extend `MAP` in `tools/known_lookup.py` as more IDs are identified.
Exit: 26 minimal (43 full) addresses filled in the work-list.

**E. Apply the 22 auto-matched addresses.**
`docs/port_matched.csv` already holds them (6 `exact`, 3 `strong`, 13
`propagated`). **Spot-check each in IDA before trusting it** — `exact` rows are
the most reliable; `propagated` rows are only as good as the caller they came
from. Do **not** write them into `offsets.h` until IDA confirms function starts.

**F. Manually derive the remaining Milestone-1 addresses in IDA.**
Load `Fallout4.exe.unpacked.exe`. Work subsystem by subsystem, using
`re/M8P*.txt` as the specification for the skin/NIF pipeline. Record each in
`docs/port_worklist.csv` with a note on how it was identified.
Exit: every Milestone-1 address filled.

**Ordered residual checklist:** [docs/IDA_RESIDUAL.md](IDA_RESIDUAL.md) —
Tier A (NIF load + fixedstring + SSN) first, then Tier B (form lookup /
PlaceAtMe / single-instance). Skip Tier C until two players walk.

**New session handoff prompt:** [docs/NEXT_SESSION_PROMPT.md](NEXT_SESSION_PROMPT.md)
— paste into a fresh session when context is full.

**G. Flip the version gate.** ✅ **DONE 2026-08-02.**
`fw_native/src/version.h` → `EXPECTED = "1.10.163.0"`.

Also added `PORT_READY = false` in the same header. Even on a version match the
DLL stays inert (proxy only, no MinHook) until this is flipped to `true`. Do
that only in the same change that lands the residual IDA ports and clears
`offset_audit.py --minimal` at ≥95%. **Do not ship a build with
`PORT_READY=true` below that bar** — detours into mid-function addresses
corrupt the process.

**H. Audit.**
```bash
./.venv/Scripts/python.exe tools/offset_audit.py \
  "C:/Games/Steam/steamapps/common/Fallout 4/Fallout4.exe.unpacked.exe" \
  --include-inline --minimal
```
Exit: **≥95%**. Do not proceed below that. An address landing mid-function
writes a jump into a half-instruction and corrupts the game silently.

**Baseline, measured 2026-07-28: 5.0%** (9 of 181 code RVAs land on a function
start). That is the expected reading for an un-ported address set.

**Progress, measured 2026-08-02 after automated C/D/E + version flip:**

| Scope | Score | Notes |
|---|---|---|
| `--minimal` (named constants only) | **16.7%** (7/42 code) | up from ~5% |
| FIELD tier (struct offsets) | **38 confirmed, 0 mismatch** | no rewrite needed — layouts match |
| KNOWN applied + PE-verified | **10** | AddressLib + matcher-exact |
| exact/strong matcher inlined | **31 substitutions** across hooks/native |
| **ni_offsets vtables** | **12** | AddressLib VTABLE_IDs → 1.10.163 |
| **ni_offsets ctors / attach** | **10+** | ctor-from-vtable + live NiNode vt[58..60] |
| **FixedString create/release/SetName** | **done** | disasm of string-pool cluster + pure thunk |
| **Ni alloc pool** | **done** | recovered from live SSN alloc sites |

### Skin/NIF surface (2026-08-02 follow-up)

`fw_native/src/native/ni_offsets.h` is the Milestone-2 inject path. Progress:

- All primary **vtables** (NiNode, NiAVObject, BSFadeNode, BSSkin::Instance,
  BSGeometry, BSTriShape, BSDynamicTriShape, BSSubIndexTriShape, …) filled from
  Address Library.
- **Ctors** recovered by scanning `.text` for stores of those vtable VAs
  (`tools/ctor_from_vtable.py`) — first function-start hit per class.
- **Attach/Detach** taken from the live NiNode vtable slots 58/59/60 on
  1.10.163 (`tools/read_vtable_slots.py`) — more reliable than cross-build
  matcher for these.
- Still TODO in ni_offsets: NIF_LOAD_BY_PATH, FIXEDSTR_CREATE, SETNAME,
  materials walker, SSN singleton, pool init, most shader helpers.

`offset_audit.py --minimal` only scores `offsets.h` (still **16.7%**). The
ni_offsets wins do not move that number but **are** required for ghost bodies.

That is the **automated ceiling** without IDA or the original 1.11.191 binary.
Near-miss RVA heuristics against 1.11.221 scored 0 accepts (similar addresses,
different code — string Jaccard 0). Unique-string seeding from 1.11.221 also
scored 0 on the residual set (no transferable unique seeds). Step **F** (manual
IDA on the NOVEL residual) is now the only path to ≥95%.

Use `--minimal` to score the reduced set; without it you are grading yourself
on addresses the DLL no longer contains.

**I. Build and deploy.**
```bash
cd fw_native && ./build.bat && ./deploy.bat
```

**J. Launch once, alone.**
Confirm from the log that the DLL loads, the version gate passes, and hooks
install. Exit: game reaches the main menu without crashing.

### Milestone 2 — two players see each other

**K. Start the server.** Prefer `start_server.bat`, or
`./.venv/Scripts/python.exe -m net.server.main`. Multiplayer checklist:
`docs/MP_TEST_RUNBOOK.md` + `tools/mp_preflight.ps1`.

**L. Connect one client.** Confirm HELLO/WELCOME in the server log and that
position packets flow. No ghost body yet — just the network handshake.

**M. Ghost body injection.** This is the hard part of Milestone 2 and depends
on the skin-pipeline addresses from **F**. Expect crashes; the `re/M8P3_*`
dossiers describe the exact call sequence and the `bones_pri` pointer-cache
subtlety that makes it work.

**N. Two instances on one machine.** Needs the single-instance bypass — a
1-byte patch, at RVA `0xC2FB62` on next-gen, **which must be re-located on
1.10.163**. Plus a second game directory. Skip entirely if you have two PCs.

**O. Baseline test.** Two clients, both moving, each seeing the other's body
animate. **Record that this works before changing anything else** — it is the
reference point that makes every later regression attributable.

### Milestone 3 — four players

All of this is already written and compiles; it is gated on Milestone 2.

**P. Inject one body per peer.** `scene_inject.cpp:inject_debug_cube()`
currently early-returns if a body exists. Put the peer count behind a constant
so it can be reverted in one edit. Use `ghosts::set_body(peer, …)`.

**Q. Per-peer bone tables.** Populate `ghosts::set_bones(peer, …)` at inject
instead of the file-scope `g_canonical_names` / `g_ghost_bone_ptrs`
(`scene_inject.cpp:68-69`). The registry already stores and tests this.
**This is the one that matters most** — a shared bone table means every ghost
animates as whoever's packet arrived last.

**R. Pose apply iterates peers.** `on_pose_apply_message()` currently applies
only the most recent peer. Make it walk dirty peers and use
`ghosts::bone_ptrs_for(peer)`.

**S. `apply_ghost_pos` takes a peer.** One call site (`client.cpp`).

**T. Per-peer synthetic form ids.** `GHOST_FORMID_BASE + n`; registry already
stores `form_id`.

**U. Retire the `g_injected_cube` read cache.** ~24 readers. Several are inside
SEH `__try` blocks where `std::lock_guard` is a hard compile error (C2712) —
migrate those last, one at a time.

**V. Raise the cap 2 → 4 and soak test.** Movement, animation, equipment.

**W. Re-tune ownership hysteresis.** `net/server/ownership.py` is tuned for 2
peers; four players trading fire on one NPC will thrash the owner election.

### Milestone 4 — actually enjoyable

**X. Re-enable the deferred hooks** from §4, one subsystem at a time, deriving
their addresses as you go. Order by value: containers → doors/locks → equipment
→ NPC combat.

**Y. Playability fixes.** Ghosts spawn naked until the peer re-equips; ~1 s
idle on aggro hand-off; receiver-side interpolation between pose frames;
PipBoy pose contortion. Full list in `docs/MULTIPEER.md` and the README's
known-limitations section.

**Z. World state breadth.** The eight unfinished B6 wedges — lights,
cell-cleared, one-shot loot, weather/time, companions, power armor, workshop.
Independent of each other; pick by what you actually hit in play.

---

## 6. Landmines (learned the hard way — do not rediscover)

- **`.pdata` contains continuation chunks.** ~40% of `RUNTIME_FUNCTION`
  records are `UNW_FLAG_CHAININFO` continuations whose `BeginAddress` is
  mid-function. Treating them as function starts yields plausible-looking
  garbage at unaligned addresses. `fnfingerprint.py` filters them.
- **Automated cross-build matching recovers ~8%.** Measured, not guessed. Over
  half the pinned addresses have no direct caller and are not in any vtable, so
  there is nothing to propagate along. Do not spend more time on the matcher —
  take its 22 answers and do the rest by hand.
- **`g_ghost_actor` is dead code.** The proxy-Actor spawn is disabled
  (`g_proxy_spawn_disabled{true}`). The visible ghost is the injected NiNode.
  Do not port its addresses.
- **Batch files must be CRLF.** With LF endings `cmd` parses `REM` as `M` and
  emits bizarre errors. Most editing tools rewrite a whole file with LF —
  after touching a `.bat`, check it still has CRLF before trusting a build.
- **CMake's `file(STRINGS)` is `strings(1)`, not `readlines()`.** It splits a
  line at the first non-ASCII byte. An em dash inside a comment in
  `minimal_exclude.txt` turns one comment into two "lines", the second of
  which no longer starts with `#` and gets parsed as a module stem. Keep that
  file pure ASCII.
- **Never relax the version gate to "make it run".** It exists to stop exactly
  the failure mode that corrupts saves.

---

## 7. Tooling reference

| Tool | Purpose |
|---|---|
| `tools/offset_audit.py` | Score an exe against the pinned addresses. **The acceptance test — needs ≥95%.** `--minimal` scopes it to the FW_MINIMAL set |
| `tools/field_verify.py` | Check FIELD offsets against CommonLibF4's 1.10.163 layouts (step **C**) |
| `tools/known_lookup.py` | Resolve KNOWN-tier constants via Address Library ID dump (step **D**) |
| `tools/addresslib/offsets-1-10-163-0.csv` | Local dump of Address Library for 1.10.163 (gitignored; see known_lookup docstring) |
| `tools/vtable_port.py` | CommonLib VTABLE_IDs → 1.10.163 RVAs |
| `tools/ctor_from_vtable.py` | Find ctors by `.text` stores of a vtable VA |
| `tools/read_vtable_slots.py` | Dump live vtable function pointers |
| `docs/IDA_RESIDUAL.md` | Ordered IDA checklist for the remaining residual (step **F**) |
| `tools/verify_unpack.py` | Confirm a Steamless output is decrypted with RVAs intact |
| `tools/port_assess.py` | Classify all 541 constants FIELD / KNOWN / NOVEL; emits the work-list CSV. `--minimal` scopes it to what `FW_MINIMAL` compiles |
| `tools/port_match.py` | Cross-build matcher (seeds on strings, propagates via call graph) |
| `tools/fnfingerprint.py` | Function fingerprinting + `.pdata` parsing |
| `tools/strxref.py` | Fast string cross-reference index |
| `tools/callgraph.py` | Whole-image call graph via byte scan |
| `docs/port_worklist.csv` | 541 rows — fill `ported_addr` / `verified` as you go |
| `docs/port_worklist_minimal.csv` | 389 rows — the Milestone-1 subset |
| `docs/port_matched.csv` | The 22 auto-matched candidates |
| `fw_native/minimal_exclude.txt` | What `FW_MINIMAL` drops. Read by CMake **and** `port_assess.py` |

## 8. Other docs

- `docs/MULTIPEER.md` — per-symbol detail on the 4-player work (steps P-U)
- `docs/ROADMAP.md` — longer-horizon phases and measurement history
- `re/M8P*.txt` — the skin/NIF dossiers, essential for step **M**
