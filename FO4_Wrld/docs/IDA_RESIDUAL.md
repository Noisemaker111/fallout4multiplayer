# IDA residual checklist — 1.10.163 port

Ordered for **first playable 2-peer ghost** (Milestone 1–2). Work against:

```
C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe
```

After each hit: write `ported_addr` + `verified=ida` into
`docs/port_worklist_minimal.csv`, update the constant in source, re-run:

```bash
./.venv/Scripts/python.exe tools/offset_audit.py \
  "C:/Games/Steam/steamapps/common/Fallout 4/Fallout4.exe.unpacked.exe" --minimal
```

Exit for hooks: **≥95%** on `--minimal`, then flip `version.h` `PORT_READY=true`.

---

## Already done (do not re-derive)

| Area | Method |
|---|---|
| FIELD struct layouts (REFR pos/cell/inv, camera, armor, …) | CommonLibF4 `field_verify` — 38 confirmed |
| Player / camera / form-cache singletons | AddressLib |
| TESObjectREFR / NiCamera / MCC vtables | AddressLib |
| Heap alloc (`ENGINE_HEAP_ALLOC`) | matcher-exact + PE |
| Main menu registrar + LoadGame | matcher + string confirm |
| Handle get / resolve | AddressLib BSPointerHandle |
| **All primary Ni* / BS* vtables** in `ni_offsets.h` | AddressLib VTABLE_IDs |
| NiNode / NiAVObject / BSGeometry / BSTriShape / BSDynamicTriShape **ctors** | `ctor_from_vtable.py` |
| NiNode AttachChild / AttachChildAt / DetachChild | live vt[58..60] |
| BSSkin::Instance copy-ctor candidate | 2nd vt-store site |
| FixedString create/release + SetName + Mem pool | disasm 2026-08-02 |
| World SceneGraph** + SSN via +0x140 | disasm; NG free globals dead |

---

## Tier A — required for ghost body inject (do first)

Spec: `re/M8P*.txt`, `ni_offsets.h` comments.

| # | Constant | NG (1.11.191) | How to find on 1.10.163 | Done |
|---|---|---|---|---|
| A1 | `NIF_LOAD_BY_PATH_RVA` | `0x17B3E90` | **DONE:** `0x01C8E0C0` — PC::Load3D vt[134]=`0x00E9F5B0` calls after c_str with opts flags `0x3D`. Thin Demand→entry+0x20 wrapper. **Not** Gamebryo writer `0x01BBAB10`. 31 call sites. | ✅ |
| A2 | `NIF_LOAD_WORKER_RVA` | `0x17B3480` | **DONE:** `0x01C8D700` — 5-arg (path rdx, opts r8, out r9); tests opts+8 bits `0x02/0x10/0x20`; BSFadeNode ctor `0x027D04C0`. Funnel via EntryDB `0x00196CCA`. | ✅ |
| A3 | `FIXEDSTR_CREATE_RVA` | `0x167BDC0` | **DONE:** `0x01B41E70` — disasm: zeros `*out`, interns cstr via `0x01B43DB0`, stores handle. Release = `0x01B42FD0`. | ✅ |
| A4 | `NINODE_SETNAME_RVA` | `0x16BCD40` | **DONE:** `0x01B966C0` — pure thunk `add rcx,0x10; jmp operator=` (`0x01B41ED0`). Call sites must pass `&handle` (fixed in scene_inject). | ✅ |
| A5 | `SSN_SINGLETON_RVA` | `0x3E47A10` | **DONE (indirect):** NG free global has **0** xrefs on 1.10.163. World SSN = `*(WORLD_SG + 0x140)`. SSN vt `0x03095148`, ctor `0x0280E5B0`. UI-only SSNs on `Interface3D::Renderer+0x238/0x240` (CommonLib) — not for ghosts. `SSN_SINGLETON_RVA=0`; resolve via A6. | ✅ |
| A6 | `WORLD_SG_SINGLETON_RVA` | `0x32D2228` | **DONE:** NG `0x032D2228` has **0** xrefs. Live SceneGraph** = **`0x05AA4358`** (109+ rip-rel refs; SSN ctor site loads `[rax+0x140]`). SceneGraph vt still `0x02D3DB88`. | ✅ |
| A7 | `MEM_POOL_RVA` + `POOL_INIT_*` | `0x3E5E0F0` / `0x1657F90` | **DONE:** pool `0x038CC980`, flag `0x038CCE00`, init `0x01B0F450`, alloc `0x01B0EFD0` (from SSN create sites). | ✅ |
| A8 | `APPLY_MATERIALS_WALKER_RVA` | `0x255BA0` | **DONE:** `0x00053080` — REFR::Load3D post-ProcessEvent(28) @ `0x00404006` with `(root, matSwap, matIdx, a4, 0)`. Family xrefs `data\materials\%s`. Inner `0x000531B0`. | ✅ |
| A9 | `UPDATE_DOWNWARD_PASS_RVA` | `0x16C8050` | **DONE 1.10.163:** NiAVObject vt[0x30] = `0x01BA3EA0` (CommonLib index // 30). | ✅ |

---

## Tier B — required for network + local player

| # | Constant | NG | Hint | Done |
|---|---|---|---|---|
| B1 | `LOOKUP_BY_FORMID_RVA` | `0x311850` | **DONE:** `0x00152C90` — free fn ecx=formID; locks allFormsMapLock; probes allForms hash; returns TESForm*. | ✅ |
| B2 | `PLACE_AT_ME_RVA` | `0x1159C10` | **DONE:** `0x0140B0E0` — Papyrus ObjectReference.PlaceAtMe; reg site `0x01414C94` r9=handler; name still `0x02CB0B70`. | ✅ |
| B3 | Player position poll target | hooks | **DONE:** no code RVA — `player_pos_hook` only needs `PLAYER_SINGLETON_RVA` + field offs (CONFIRMED). | ✅ |
| B4 | Single-instance bypass site | was `0xC2FB62` on NG | 1-byte patch; re-locate mutex/singleton branch on 1.10.163. Open for 1-PC two-client. | ☐ |
| B5 | `BSFIXEDSTRING_MAKE` / graph vars | `0x167BDC0` / `0x818D60` | **DONE:** MAKE=`0x01B41E70` (=A3); BOOL=`0x0081D3D0` INT=`0x0081D3F0` FLOAT=`0x0081D410` (0x20 apart; r8b/r8d/xmm2). | ✅ |

---

## Tier C — defer until two players walk

Containers, doors, locks, equip, NPC combat — all behind `FW_MINIMAL` today.
Do not open until A+B clear and a 2-peer soak works.

---

## Method notes

1. **Prefer live vtables over matcher** for virtual methods (we already saw
   matcher attach RVAs disagree with 1.10.163 NiNode vt[58]).
2. **Ctor recovery**: `python tools/ctor_from_vtable.py --exe <unpacked>` after
   any new vtable lands.
3. **Do not** accept "mid-function + small delta" without string/fingerprint
   proof — measured Jaccard 0 against 1.11.221.
4. Record every hit in the work-list CSV so restarts don't re-find.

---

## Acceptance

```text
offset_audit --minimal  ≥ 95%     ✅ 100% (2026-08-02)
PORT_READY              = true    ✅ version.h
FW_MINIMAL build        clean     ✅
2 clients               HELLO/WELCOME + ghost body visible   ☐ smoke next
```
