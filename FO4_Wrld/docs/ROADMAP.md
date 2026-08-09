# Roadmap — from here to done

Written 2026-07-28. Living document; update the status marks as phases land.

## Defining "done"

"Fallout 4 multiplayer, everything included" is unbounded, so this splits it
into three tiers that can each be declared finished:

| Tier | Meaning | Realistic horizon |
|---|---|---|
| **D1 — Playable co-op** | 4 players, same world, shooting the same enemies, without crashes or desync that ends the session | the goal; everything below is gravy |
| **D2 — Feature-complete co-op** | the world state a co-op playthrough actually touches: settlements, power armor, companions, quests, weather | large |
| **D3 — The 10-player persistent MMO** | the README's stated vision: 10 peers, persistent world, internet play, Rust server | open-ended |

**D1 is the target.** D2 and D3 are sequenced after it and can be stopped at
any wedge boundary without leaving things broken.

## The gate on everything: retarget to 1.10.163

**Decision (2026-07-28): target 1.10.163**, the build already installed.
Rationale:

- Biggest mod ecosystem by a wide margin.
- **It is a frozen target.** 1.10.163 will never be patched again, which
  permanently removes the "a Bethesda update bricks the client" risk. Next-gen
  is still moving — 1.11.191 → 1.11.221 already happened, and that is precisely
  why the offsets don't match anything on this machine.
- F4SE 0.6.23, Address Library and CommonLibF4 all cover it, so a large slice
  of the port is a lookup rather than an investigation.

The cost is retargeting 541 pinned constants. Measured, not estimated
(`tools/port_assess.py`, `tools/port_probe.py`, `tools/port_probe2.py`):

| Tier | Count | How it's recovered |
|---|---|---|
| **FIELD** struct layouts | 158 | public in F4SE / CommonLibF4 — mechanical |
| **KNOWN** engine APIs | 43 | Address Library lookup — mechanical |
| **NOVEL** original RE | 340 | see below |

Of the 340 novel entries, 291 anchor to an enclosing function in the 1.11.221
backup. Since every pinned code address is a *function start* and 1.11.221 is
the same code shifted by small deltas (+0x9 to +0x11E), that enclosing function
is very probably the same function — an anchor that never requires holding
1.11.191.

Call-graph propagation reach from those anchors:

| Depth | Count | Share |
|---|---|---|
| 0 — distinctive itself (strings / constants) | 65 | 22.3% |
| 1 — a direct neighbour is distinctive | 44 | 15.1% |
| 2 — two hops out | 81 | 27.8% |
| **reachable (automatable candidate)** | **190** | **65.3%** |
| unreachable | 101 | 34.7% |

### ⚠️ CORRECTION (measured 2026-07-28, after building the matcher)

**The 65% figure above was wrong, and the manual tail is much larger than the
60-130 it implied.** `port_probe2.py` counted fingerprint-based
"neighbourhoods" rather than verified call-graph edges, which flattered the
result. The matcher was built (`tools/port_match.py`, `strxref.py`,
`callgraph.py`) and self-tested by matching 1.11.221 **against itself** — a
case that must score ~100% if the algorithm is sound, since it measures purely
structural recovery with addresses ignored.

It scored **21.3%** (8 exact, 10 strong, 44 propagated, 229 unmatched).

Root cause, measured directly against the 276 anchor functions:

| Structural handle | Anchors having it |
|---|---|
| ≥1 direct `call rel32` caller | 106 / 276 (38.4%) |
| present in a vtable / data-section pointer | 32 / 276 (11.6%) |

Over half the pinned addresses have **no incoming structural edge at all** —
they are reached through computed pointers, jump tables, or tail calls. There
is nothing for a graph algorithm to propagate along, so ~38% is the hard
ceiling even with perfect matching, and 21% is what the real implementation
achieves.

### FINAL measured result (matcher run against the real unpacked 1.10.163)

Steamless output verified with `tools/verify_unpack.py`: `.text` entropy 6.218,
11.5% int3 padding, `.bind` gone, all section RVAs preserved. Clean input.

Matcher run 1.11.221 → 1.10.163 unpacked. First pass reported 14.1%; two
correctness bugs were then found and fixed, both of which had been inflating it
with **wrong** addresses:

1. **Continuation chunks counted as function starts.** ~40% of `.pdata`
   entries are `UNW_FLAG_CHAININFO` records for split functions, whose
   BeginAddress is mid-function. They produced "matches" at unaligned
   addresses like `0x01449355`. Function counts dropped 218k→129k and
   291k→199k once filtered.
2. **Non-injective mapping.** 64 target functions were claimed by more than
   one source (150 pairs). At least one side of each is wrong and there is no
   way to tell which, so all are now demoted to unresolved.

**Verified final: 7.6% — 22 of 291** (6 exact, 3 strong, 13 propagated).

**So the manual tail for a 1.10.163 port is ~269 addresses.** Automated
matching does not meaningfully dent it. The estimates that preceded this
(65% reachable, then 170-230 manual) were both too optimistic; this number
comes from a real run against real binaries with the verification in place.

This makes the choice sharper, not vaguer:

- **Port to 1.10.163** — buys the mod ecosystem and a frozen target, costs
  ~170-230 hand-derived addresses in IDA, made harder by the missing combat
  dossiers (below). This is months of solo RE.
- **Obtain 1.11.191** — reduces the problem to a *direct diff* against a
  near-identical build (deltas of +0x9 to +0x11E), which is a far easier and
  far more reliable job than cross-major-version structural matching. Then
  port onward to any build from a verified baseline.

Nothing about the 1.10.163 argument was wrong. It is just expensive, and the
number is now honest.

Until the port lands, work is limited to server/protocol, engine-independent
client logic, and tooling. Writing large volumes of untestable scene-graph code
is how this project would acquire a backlog of bugs nobody can find later.

### Phase 0′ — the port pipeline

| # | Task | Status |
|---|---|---|
| 0′.1 | Classify all 541 constants by tier | ✅ `tools/port_assess.py`, `docs/port_worklist.csv` |
| 0′.2 | Measure matching feasibility | ✅ `port_probe.py` / `port_probe2.py` |
| 0′.3 | Recover FIELD tier from CommonLibF4 / F4SE headers | ✅ 38 confirmed / 0 mismatch (mappable set done) |
| 0′.4 | Recover KNOWN tier from Address Library 1.10.163 | ✅ 10 PE-verified applied via `known_lookup` + `apply_ports` |
| 0′.5 | Build the matcher: seed on distinctive fns, propagate via call graph | ✅ built; real yield 7.6% (22 of 291) |
| 0′.6 | Verify every ported address (function-start oracle + spot checks) | 🟡 PE-verify gate in `apply_ports.py`; audit 16.7% |
| 0′.7 | Manual IDA pass on the residual tail | ⬅ **current** — automated ceiling hit |
| 0′.8 | Flip `version.h` EXPECTED to 1.10.163, re-run `offset_audit` for ≥95% | 🟡 gate flipped; audit 16.7% (need F for ≥95%) |

**A missing-knowledge risk to note.** Every RE dossier the README cites for the
combat / AI / ghost layer is absent from the repo — `re/B6.6w0_pair_AGENT_*`
(0 of 10), `re/c34_preflight_safety_AGENT.md`, `re/B6.6w5_player_ctor_audit.md`,
`re/reference_fo4_offsets.md`, `re/stradaB_*`. They are not gitignored; they
were never committed. Only the 9 M8 skin dossiers (5,030 lines) survive. For
~153 of the hardest addresses the sole surviving record is inline code
comments. Worth recovering from backups if they exist anywhere — it would
materially cut 0′.7.

---

## Phase 0 — Bring-up on 1.10.163  ⬅ **current**

Follows Phase 0′. Nothing here can start before the port verifies.

| # | Task | Status |
|---|---|---|
| 0.1 | `offset_audit.py` scores ≥95% against the 1.10.163 exe | ⏳ |
| 0.2 | Deploy `dxgi.dll`, confirm it loads and the version gate passes | ⏳ |
| 0.3 | Locate the single-instance patch site on 1.10.163 (was `0xC2FB62` on NG) | ⏳ |
| 0.4 | Stand up a 2-instance local test loop | ⏳ |
| 0.5 | Reproduce the existing 2-peer demo as a known-good baseline | ⏳ |

**Exit criteria:** two clients connect and see each other move and animate,
*before* any of my changes are trusted. 0.5 matters — it separates "the
refactor broke it" from "the port broke it" from "it was already broken".

Done already: toolchain, MinHook, clean build (84/84), 346 server tests, 70
registry checks, port assessment + feasibility probes.

---

## Phase 1 — 4-player co-op (D1)

Everything except body injection is already N-peer. See
[MULTIPEER.md](MULTIPEER.md) for the per-symbol detail.

| # | Task | Size | Notes |
|---|---|---|---|
| 1.1 | Inject one body per peer, behind a peer-count cap | **L** | start cap at 2 (parity), raise to 4 |
| 1.2 | Per-peer bone tables → `ghosts::set_bones` at inject | **M** | storage + tests already exist |
| 1.3 | `on_pose_apply_message` iterates dirty peers | **S** | depends on 1.2 |
| 1.4 | `apply_ghost_pos` takes a peer | **S** | one call site |
| 1.5 | Per-peer synthetic form ids (`GHOST_FORMID_BASE + n`) | **S** | registry already stores `form_id` |
| 1.6 | Drop single-entry `ghost_map` from config | **S** | consumer already dead code |
| 1.7 | Retire the `g_injected_cube` read cache | **M** | ~24 readers; SEH blocks can't take a lock — needs care |
| 1.8 | Launcher: N instances instead of Side A/B | **M** | only for local testing; 4 machines don't need it |
| 1.9 | 4-peer soak: movement, animation, equipment, containers, doors | **M** | the actual acceptance test |

**Exit criteria (D1):** four clients, one world, each seeing three correctly
animated others with correct equipment; shared enemies; a 30-minute session
with no crash and no permanent desync.

**Main risk:** 1.1 and 1.7. `scene_inject.cpp` is 12k lines of stateful
scene-graph code with SEH guards, and the failure mode is a crash 3 seconds
later in an unrelated system. Mitigation: peer-count cap as a one-constant
revert, and stage 1.7 reader-by-reader rather than in one pass.

---

## Phase 2 — Playability hardening

The known-limitations list. These don't block D1 but they're what makes a
session feel broken. Roughly in value-per-effort order.

| # | Item | Size | Notes |
|---|---|---|---|
| 2.1 | Ghosts spawn naked until the peer re-equips | M | `equip_announce.cpp` scaffold exists, needs BipedAnim RE |
| 2.2 | Raider anim/position glitches at first contact + post-mortem (N1 reopened) | M | |
| 2.3 | ~1 s idle on aggro hand-off | S | fix exists, disabled pending a safer guard |
| 2.4 | Receiver-side interpolation between pose frames | M | required before internet play |
| 2.5 | 1st-person sender → ghost T-pose stub | L | needs `PlayerCamera` singleton RE |
| 2.6 | PipBoy pose contortion on the ghost | S | detect + play a static placeholder |
| 2.7 | Ghost body casts no shadow | S | render flag investigation |
| 2.8 | ~50 ms weapon flicker on equip | S | side effect of the re-equip cycle |
| 2.9 | Container UI stale on observer | S | cosmetic; anti-dup already enforced server-side |
| 2.10 | Enemy HP bar is green, should be red | S | |
| 2.11 | Leveled-list divergence (raiders look/loot differently per client) | L | needs seeded RNG capture or a fixed-content ESL; currently parked |

---

## Phase 3 — World-state breadth (D2)

The eight unfinished B6 wedges plus the B4 remainder. Independent of each
other — pick by what you actually hit in play.

| # | Wedge | Size |
|---|---|---|
| 3.1 | **B4** QuestStage apply | S — ✅ shipped 2026-08-03 (observe + apply + boot; classic RVAs fixed) |
| 3.2 | **B6.2** Lights toggle | S — ✅ Activate filter expanded 2026-08-03 |
| 3.3 | **B6.9** Cell-cleared status | S — ✅ v22 SetCleared path 2026-08-03 |
| 3.4 | **B6.10** One-shot loot (bobbleheads, magazines, holotapes) | S — ✅ pickup under MINIMAL 2026-08-03 |
| 3.5 | **B6.11** Time of day + weather | M — ✅ GameHour + weather + **PassTime v23** (2026-08-03) |
| 3.6 | **B6.7** NPC dialogue state + faction | M — 🟡 PC faction + **relationship rank v23/24**; dialogue topics later |
| 3.7 | **B6.8** Companion state | M — 🟡 SetPlayerTeammate + party-teleport pull; AI ownership later |
| 3.7b | Quest item grants | S — ✅ **ITEM_GRANT v25** silent PC AddItem + KEYM |
| 3.8 | **B6.13** Power Armor frame + worn state | L |
| 3.9 | **B6.12** Workshop / settlement build state | **XL** — own epic |

Do 3.1-3.4 first: all small, all immediately visible in play.
3.9 is a project in itself and should not be started casually.

---

## Phase 4 — NPC / combat breadth

| # | Item | Size |
|---|---|---|
| 4.1 | Creature roster beyond Concord raiders | L |
| 4.2 | Pure-melee enemies (never trip the combat-controller flag → never owned) | M |
| 4.3 | Shared-HP boss encounter (mechanic done, needs a real fight) | M |
| 4.4 | Re-tune ownership hysteresis for 4 peers | S — `net/server/ownership.py`, tuned against 2 |

4.4 should be done during Phase 1, not here — four players trading fire on one
raider will thrash the current election.

---

## Phase 5 — Scale and robustness (D3)

| # | Item | Size |
|---|---|---|
| 5.1 | Interest management — filter broadcasts by `cell_id` (already on the wire) | M |
| 5.2 | Bandwidth budget for pose at N peers (fan-out is O(N²) server-side) | M |
| 5.3 | Internet play: NAT, jitter, packet loss | L |
| 5.4 | Raise 4 → 10 peers | M |
| 5.5 | **B7** Rust server port | XL — only worth it if Python becomes the bottleneck |
| 5.6 | Version-independent address binding (signatures / Address Library) | L |

**5.6 deserves promotion the moment 1.11.191 is in hand.** With a known-good
build as reference, signatures can be extracted at each of the 383 addresses
and scanned for in any other build. That converts "a Bethesda patch bricks
everything" into "regenerate the table" — and it is the only item here that
protects all the other work. `tools/offset_audit.py` is already the harness.

---

## How to run this

**One phase at a time, and don't start Phase 1 code before Phase 0.5 passes.**
A green baseline is what makes every later regression attributable.

Per work item:
1. Small commits, each leaving the build green.
2. Server-side work gets a test in `net/tests/`.
3. Engine-independent client logic gets a test in `fw_native/tests/`.
4. Scene-graph work gets a live 2-client check before the 4-client check.

**Track the honest signal.** Three tiers of coverage exist (server, engine-
independent client, and nothing-at-all for scene-graph code). When something
lands in that third tier, say so rather than implying it was tested.

## What I can do right now, unblocked

If 1.11.191 is going to take a while, this is the useful work that doesn't
need it — all of it real, none of it speculative scene-graph code:

- **4.4** ownership hysteresis re-tune for 4 peers, with tests (server-side)
- **3.1** QuestStage apply wire-up (server + protocol halves)
- **5.1** interest management by `cell_id` (server-side, testable)
- **5.6** build the signature extraction/porting pipeline so it's ready
- Expand `fw_native/tests/` over any other engine-independent logic

Say the word and I'll start on those in that order.
