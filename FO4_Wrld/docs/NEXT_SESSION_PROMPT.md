# Recursive session prompt — shared-campaign co-op (Tier A → B)

Paste **everything below the line** into a new Grok / agent session.

---

You are the sole engineer on **FO4_Wrld** (Fallout 4 multiplayer: `dxgi.dll` + Python UDP server).

## Mission (product)

Ship **one logical campaign** for co-op — not two friends soloing with ghosts.

- Either peer talks to a quest giver → **both** get the quest / stage.
- Quest **rewards + proximity XP** for both when in range.
- **Teleport / party travel** so cell desync never bricks a session.
- Then **Tier B** so story does not break: dialogue/factions, companions, travel rules, time/weather.

**Honest architecture (locked — do not reopen):**

| Concept | Reality |
|--------|---------|
| “One save” | **One server-authoritative campaign state** |
| Client saves | Each FO4 process still has its **own** `.fos` (appearance, inventory, local load) |
| Sync | Overlay UDP makes quests/world/NPCs feel like one world |
| Not in scope | Literally one open save file in two processes |

**Product rules (locked):**

1. **Narrative = shared** — quests, factions, unique keys, cleared cells, story rewards.
2. **Build = personal** — SPECIAL, perks, most inventory (unless quest-unique).
3. **Loot = contested once** — chest empty for everyone after take (B1 already).
4. **XP = personal level; shared gain events** when nearby (or always for quest XP).
5. **Stuck = free teleport to party** — forgives cell desync forever.

## Locked engineering decisions

| Item | Value |
|------|--------|
| Target build | **1.10.163 only** (`EXPECTED` in `fw_native/src/version.h`) |
| Game dir | `C:\Games\Steam\steamapps\common\Fallout 4` |
| Repo | `C:\Users\Jk101\Projects\fallout4multiplayer\FO4_Wrld` |
| Analysis binary | `Fallout4.exe.unpacked.exe` (Steamless) |
| Mode | Prefer `FW_MINIMAL` until a feature needs full hook set |
| Server | `net/server/main.py` UDP **:31337** — start **before** FO4 (HELLO only at boot) |
| Install UX | `tools/player_setup/` — drop into FO4 folder; no Nexus required |
| Family Share | Poor for simultaneous co-op — assume two installs / two machines when possible |

Do **not** re-litigate next-gen, AddressLib wholesale rewrites, or “true single .fos multiplayer.”

## Current state (as of 2026-08-03 handoff)

### What works in play

- Classic 1.10.163 stack stable enough for **main menu → load into world**.
- Base-only / DLC+CC off used for stability during classic bring-up (revisit DLC later).
- Native client deploys as `dxgi.dll`; `fw_native.log` is the ground truth.
- UDP server accepts peers; **POS_STATE / BROADCAST** proven (thousands of frames in session).
- Shared combat pieces already strong: **N2/N3/N4** (aggro, shared HP, player death), containers **B1**, doors/locks/terminals **B6.0/3/4**, cell-aware pos **B6.1**, equip ghost **M9**.

### What is incomplete for “one campaign”

| Slice | Status | Notes |
|-------|--------|--------|
| **B4 GlobalVar** | ✅ shipped | Hook + direct apply; classic RVA **`0x13F0A40`** (was wrong mid-fn) |
| **B4 QuestStage** | ✅ shipped 2026-08-03 | Observe SetCurrentStageID + main-thread apply + QUEST_STATE_BOOT; classic RVA **`0x1449550`** |
| **B6.7 Faction rank** | 🟡 partial | PC SetFactionRank sync; dialogue trees later |
| **Relationship rank** | ✅ v23/v24 | SetRelationshipRank observe/apply + late-join boot |
| **A2 Teleport** | ✅ | **F8** to peer; **F9** summon |
| **A4 Proximity XP** | ✅ | `RewardPlayerXP` + radius 4096 u |
| **B6.11 Time/weather** | ✅ v23 | GameHour + weather + **PassTime (Wait/Sleep)** fan-out |
| **B6.8 Companion** | 🟡 | SetPlayerTeammate SET/BCAST/boot; companion AI mirror later |
| **B6.2 Lights** | ✅ | Activate toggle filter expanded (proto reuses DOOR_OP) |
| **B6.9 Cell cleared** | ✅ | SetCleared + poll (proto **v22**) |
| **Economy under MINIMAL** | ✅ | container/put/lock/pickup/kill/equip/door kept in |
| Quest rewards both | ✅ v25 | Silent PC AddItem grants + XP proximity; non-silent loot stays personal |
| Fast travel / load-door party | ✅ | Cell change auto PARTY_WARP follow; F8/F9 still available |
| Ghost N-body polish | 🟡 | Registry multi-peer; body inject still primary-ghost heavy under MINIMAL |
| Two-peer ghost proof | 🟡 | Pending solid same-session demo |
| SAVE_DEV / auto-load | 🟡 | Wrapper LoadGame `0xCED1D0`, SAVE_MGR `0x05A735C8`; named load flaky |
| **Honest inventory** | 📄 | `docs/COOP_1TO1_GAP.md` — read before claiming 1:1 |

### Key code map (do not reinvent)

| Area | Where |
|------|--------|
| Protocol quest/global | `net/protocol.py` — `QUEST_STAGE_*`, `GLOBAL_VAR_*`, bootstraps |
| Server quest/global state | `net/server/state.py` (`QuestStageState`, `GlobalVarState`) |
| Server handlers | `net/server/main.py` — `_handle_quest_stage_set`, `_handle_global_var_set` |
| Tests | `net/tests/test_quest_sync.py` |
| C++ protocol mirrors | `fw_native/src/net/protocol.h` |
| GlobalVar hook | `fw_native/src/hooks/worldstate_hook.*` |
| GlobalVar apply | `fw_native/src/engine/engine_calls.cpp` → `apply_global_var` |
| Net RX apply GlobalVar | `fw_native/src/net/client.cpp` (~GLOBAL_VAR_BCAST) |
| Offsets | `fw_native/src/offsets.h` — `PAPYRUS_GLOBALVAR_SETVALUE_RVA`, teleport helpers |
| Roadmap | `docs/ROADMAP.md` Phase 3 (world-state), Phase 2 (feel) |
| Status table | `README.md` milestones B4 / B6.* |

Design intent already on wire (protocol v4 comment): *“10 player = 1 entità narrativa”* → quest progress is global, not per-peer; stages monotonic.

## Co-op feature tiers (session ordering)

### Tier A — “we’re playing together” (do these before deep Tier B)

1. **Ghost + move + shoot same enemies** — mostly there; harden two-peer when blocked on story work.
2. **Teleport to friend / bring party** — high UX value; unblocks cell desync forever.
3. **Quest stage both** — finish B4 QuestStage apply + SetStage observe hook + bootstrap apply.
4. **Quest rewards + proximity XP both** — fan-out grants when in range (or always for pure quest XP).
5. **One-shot unique loot + cell cleared** — B6.10 / B6.9 (small; story-adjacent).

### Tier B — “story doesn’t break” (this epic’s target after A3 at least)

| # | Feature | Roadmap | Size | Depends on |
|---|---------|---------|------|------------|
| B1 | **NPC dialogue state + faction joined** | B6.7 | M | Quest stages + GlobalVar patterns |
| B2 | **Companion state** (recruit / dismiss / who owns follow AI) | B6.8 | M | NPC ownership (N branch) |
| B3 | **Fast travel + load-door party rules** | travel UX | M | Teleport-to-peer (A2) + cell_id |
| B4 | **Time of day + weather** | B6.11 | M | GlobalVar `GameHour` + Sky weather state |

Do **not** start workshop (**B6.12**) or full PA epic (**B6.13**) in this track unless user redirects — those are Tier C / XL.

## Recommended next-session plan

### Default focus (if user says “keep going”)

Narrative S/M stack is largely **shipped on wire** (protocol **v24**). Next value:

| Next | Notes |
|------|--------|
| **Live smoke** | Two-peer PassTime, relationship, silent AddItem, cell-travel |
| **Companion ownership design** | Before more companion AI code (MINIMAL combat tier out) |
| **Non-MINIMAL combat preset** | Shared HP / N-tier when you want Concord co-op fights |
| **Dialogue topics** | XL — design first (F4MP lessons); do not free-hand |
| **Workshop / PA** | XL epics — only if user redirects |

Read **`docs/COOP_1TO1_GAP.md`** before claiming release 1:1.

## How to run (smoke)

**Friend one-click:** `tools/player_setup/dist/FoM_PlayerPack.zip` → **FoM.exe** → Host/Join.  
**Dev preflight:** `powershell -File tools\mp_preflight.ps1`  
**Full runbook:** `docs/MP_TEST_RUNBOOK.md`

```powershell
# Friends / two PCs (preferred)
#   Unzip FoM_PlayerPack → FoM.exe → [1] Host or [2] Join

# Dev rebuild pack after native changes:
cd FO4_Wrld\fw_native; .\build.bat --minimal
cd ..\fw_launcher; .\build.bat
cd ..\tools\player_setup; powershell -File .\Build-PlayerPack.ps1
```

Server tests (no game):

```powershell
.\.venv\Scripts\python.exe -m pytest net/tests/test_quest_sync.py -q
.\.venv\Scripts\python.exe -m pytest net/tests -q
```

## Explicit non-goals this track

- Workshop / settlement full sync (B6.12 XL).
- 10-player MMO / Rust server (B7) unless Python burns.
- Re-port entire offset table “for fun” — only fix call sites smoke proves wrong.
- Nexus / MO2 as required install path — keep FoM_Setup folder drop.
- Family Share as the co-op distribution plan.

## Definition of done (milestones)

| Milestone | Done when |
|-----------|-----------|
| **A3 QuestStage** | A sets stage → server records → B applies; join bootstrap restores stages; tests green |
| **A2 Teleport** | One key/command moves local player to peer pos/cell (best-effort cell match) |
| **A4 Rewards/XP** | Configurable radius; kill or quest XP on A grants on B when in range; no double-grant loop |
| **B1 Dialogue/faction** | At least one faction join / dialogue global visible on both Pip-Boys / factions |
| **B2 Companion** | One companion follows owner; other peer sees companion pos; dismiss is shared |
| **B3 Travel** | 🟡 F9 summon + cell-travel auto PARTY_WARP; policy toggle in code |
| **B4 Time/weather** | Same hour + weather form on both within a few seconds |

## Session hygiene

- One shippable issue per session unless user asks for a program.
- Small commits; server changes get `net/tests/` coverage.
- Prefer existing CONTAINER / GLOBAL_VAR patterns over new architectures.
- Kill FO4 before deploy when `dxgi.dll` is locked.
- If HELLO times out: server not running or started after FO4 — restart FO4 after server.
- Update this file’s “Current state” section at end of session.

## Start now

1. Read `README.md` B4/B6 rows + `docs/ROADMAP.md` § Phase 3 item **3.1**.
2. Grep QuestStage / SetStage in `fw_native` and `net/` — implement missing **apply + hook**, do not redesign protocol.
3. Prove with logs or two-peer smoke.
4. If A3 lands mid-session and time remains: stub **party teleport** debug command.

**Go.**
