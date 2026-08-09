# Co-op 1:1 campaign gap map

**Date:** 2026-08-03  
**Target:** one logical co-op campaign that feels like a single released playthrough  
**Architecture (locked):** server-authoritative campaign overlay on two local `.fos` files — not a true single-save multiplayer process  

This document is the honest inventory of what is integrated on the wire + native path vs what still cannot be “release 1:1” without larger RE / design work.

Protocol version at time of writing: **v25**.

---

## Legend

| Tag | Meaning |
|-----|---------|
| **SHIPPED** | Observe + server + apply (or intentional best-effort) on classic **1.10.163** + `FW_MINIMAL` where noted |
| **PARTIAL** | Useful in play; missing late-join, ownership, or fidelity |
| **STUB/WIRE** | Protocol/server only, or observe without full apply |
| **XL** | Own epic — do not start casually |
| **OUT** | Explicitly out of product scope for this track |

---

## Narrative / campaign core

| Slice | Status | What peers get | Gap to 1:1 |
|-------|--------|----------------|------------|
| Quest stages | **SHIPPED** | `SetCurrentStageID` → both + bootstrap | Stage-complete scripts that only run on one client may leave local side-effects desynced |
| GlobalVar (story) | **SHIPPED** | non-const SetValue + boot | Const globals; script-only vars never written via SetValue |
| PC faction rank | **SHIPPED** | SetFactionRank on PC + boot | NPC-only faction ranks; full dialogue tree state |
| Relationship rank | **SHIPPED** (v23/v24) | SetRelationshipRank observe/apply + late-join boot | Affinity-only (not full disposition graph / dialogue trees) |
| Unique world loot | **SHIPPED** | pickup → `ACTOR_EVENT DISABLE` under MINIMAL | Leveled-list / inventory seed divergence (Phase 2.11) |
| Cell cleared | **SHIPPED** | SetCleared + poll + boot | Cells never flagged via Papyrus |
| Quest XP both | **SHIPPED** | RewardPlayerXP + proximity gate | — |
| Quest item grants | **SHIPPED** (v25) | silent PC `AddItem` + KEYM form always → ITEM_GRANT | Vendor/craft/non-silent loot not mirrored (by design) |
| Personal level/SPECIAL | **OUT (by design)** | each player builds own | — |

---

## World interaction / economy

| Slice | Status | Notes |
|-------|--------|-------|
| Containers TAKE/PUT | **SHIPPED** | server-authoritative anti-dup; under MINIMAL |
| World put | **SHIPPED** | put_hook under MINIMAL |
| Doors open/close | **SHIPPED** | Activate worker; MINIMAL |
| Lights / ACTI toggles | **SHIPPED** | form-type filter expansion (DOOR_OP reuse) |
| Locks / terminals | **SHIPPED** | ForceUnlock/ForceLock path |
| Equip visuals (ghost) | **PARTIAL** | equip_hook kept; equip_cycle/announce optional; naked-until-reequip remains |
| Workshop build | **XL** | B6.12 — settlement scrap/build/network |
| Vendor inventory stock | **PARTIAL** | container path only if treated as container; vendor leveled lists diverge |

---

## Time / weather / travel

| Slice | Status | Notes |
|-------|--------|-------|
| GameHour GlobalVar | **SHIPPED** | classic form `0x38` |
| Wait / Sleep PassTime | **SHIPPED** (v23) | observe PassTime → fan-out; apply bumps GameHour + GameDaysPassed |
| Weather | **SHIPPED** | SetActive + Sky poll + boot |
| Party teleport F8/F9 | **SHIPPED** | A2 |
| Cell-travel auto follow | **SHIPPED** | load door / fast travel → PARTY_WARP (debounce 2.5s) |
| True shared calendar edge cases | **PARTIAL** | no Timescale / holiday script fan-out beyond globals that fire SetValue |

---

## Companions / NPCs / combat

| Slice | Status | Notes |
|-------|--------|-------|
| Recruit/dismiss flags | **SHIPPED** | SetPlayerTeammate SET/BCAST/boot |
| Companion AI ownership + shared body | **XL / N-branch** | each client still runs local AI; true 1:1 needs owner stream |
| Companion follow position | **PARTIAL** | party-teleport pull of known teammates; no continuous AI/pos stream under MINIMAL |
| Hostile raider owner sync | **PARTIAL** | N1–N4 strong on Concord path; **compiled out of FW_MINIMAL** combat tier |
| Shared HP / death | **PARTIAL** | full build; not MINIMAL combat tier |
| Creature roster beyond raiders | **L** | Phase 4 |
| Full dialogue / TopicInfo | **XL** | F4MP-style topic mirror is a separate project |
| ForceGreet / scene packages | **XL** | package ownership |

---

## Player presence / feel

| Slice | Status | Notes |
|-------|--------|-------|
| POS + rotation | **SHIPPED** | proven in session |
| Ghost body inject | **PARTIAL** | primary-ghost heavy under MINIMAL; N-peer polish Phase 1 |
| Full-body pose | **PARTIAL** | multi-peer bone tables incomplete |
| Power Armor frame/worn | **XL** | B6.13 |
| SAVE_DEV / auto-load | **PARTIAL** | named load flaky |

---

## What “1:1 like they released it” still means here

Bethesda’s single-player campaign assumes **one process, one script VM, one world**. Co-op 1:1 in this architecture means:

1. **Shared narrative outcomes** (stages, factions, keys, clears, weather, time jumps, one-shot loot).
2. **Contested economy** (containers/world loot once).
3. **Forgiving travel** (never stuck in different cells).
4. **Not** shared SPECIAL/inventory cosmetics, **not** one `.fos`, **not** full dual-process companion AI without ownership.

The **SHIPPED** rows above are the maximum S/M surface that can honestly claim campaign co-op on classic + MINIMAL today.

---

## Next integration order (when continuing)

Do in this order unless play testing re-prioritizes:

1. **Two-peer live smoke** of PassTime + relationship + silent AddItem  
2. **Companion ownership design** (before more companion code)  
3. **Full combat tier under a non-MINIMAL co-op preset** (not default MINIMAL)  
4. **Dialogue topics** only after a written design (F4MP lessons)  
5. **Workshop / PA** only as dedicated epics  
6. Optional: KEY form-type always-grant even if non-silent  

---

## Verification checklist (this sprint)

| Check | Result |
|-------|--------|
| `pytest net/tests/test_item_grant.py` + relationship | see latest run |
| `fw_native` `build.bat --minimal` | OK (`dxgi.dll`) |
| Live FO4 two-peer smoke | **pending** |
