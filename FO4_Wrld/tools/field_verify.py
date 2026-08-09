"""Verify offsets.h FIELD constants against CommonLibF4's 1.10.163 layouts.

Step C of docs/START_HERE.md. The FIELD tier is 158 struct offsets — the single
largest tier, and the only one recoverable without IDA: CommonLibF4 publishes
the class layouts for exactly our target build.

Why a tool and not a one-off read: "verify each one" in step C means 158 manual
comparisons, and next-gen moved several TESObjectREFR fields, so the ones that
did NOT move are not self-evident. This turns the check into something you can
re-run after every edit to offsets.h.

Source of truth
---------------
CommonLibF4 `master` targets Fallout 4 1.10.163 (the `community` branch is the
next-gen one — do not point this at it). It is not vendored here; clone it and
pass the path:

    git clone --depth 1 --branch master \
        https://github.com/Ryan-rsm-McKenzie/CommonLibF4.git
    python tools/field_verify.py --commonlib ./CommonLibF4

CommonLibF4 annotates every member with its hex offset, e.g.

    std::uint32_t formID;   // 14

which is what this parses. Nested members compose: a field inside a struct that
is itself a member gets `outer.member + inner.member`, written in MAP below as
`TESObjectREFR.data + OBJ_REFR.location`.

Reading the output
------------------
  CONFIRMED  our value matches CommonLibF4 for 1.10.163 — leave it alone
  MISMATCH   real port work; CommonLibF4's value is the one to take
  UNMAPPED   no entry in MAP yet. NOT a pass — it means nobody has checked it.

Exit code is 1 if any MISMATCH, so this can gate a build.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from offset_audit import OFFSETS_H, use_utf8_stdout  # noqa: E402
from port_assess import parse_field_offsets  # noqa: E402


# Our constant name -> a CommonLibF4 offset expression.
#
# Every entry here is a claim that has been checked by hand once; the tool
# re-checks it on demand. Add entries as you work through the tier — an
# UNMAPPED constant is unverified, not verified-good.
#
# Composition: "A.b + C.d" means field `b` of class `A` plus field `d` of
# class `C`, used when `b` is itself a struct. `+ 0xNN` adds a literal.
MAP: dict[str, str] = {
    # --- TESForm ---------------------------------------------------------
    "FORMID_OFF": "TESForm.formID",
    "FLAGS_OFF": "TESForm.formFlags",

    # --- TESObjectREFR ---------------------------------------------------
    # data is an OBJ_REFR; NiPoint3A is 16-byte aligned, which is why
    # location lands on 0x10 inside it rather than 0x0C.
    "ROT_OFF": "TESObjectREFR.data + OBJ_REFR.angle",
    "POS_OFF": "TESObjectREFR.data + OBJ_REFR.location",
    "BASE_FORM_OFF": "TESObjectREFR.data + OBJ_REFR.objectReference",
    "PARENT_CELL_OFF": "TESObjectREFR.parentCell",
    "REFR_INV_LIST_OFF": "TESObjectREFR.inventoryList",
    # Base-class subobject of TESObjectREFR (inheritance comment // 048).
    # sizeof(IAnimationGraphManagerHolder) == 0x8 confirms the slot size.
    "IANIMGRAPHHOLDER_OFF": "0x48",

    # --- BGSInventoryList ------------------------------------------------
    # `data` is a BSTArray<BGSInventoryItem>, whose allocator puts the data
    # pointer at +0x00 and capacity at +0x08, so _size lands at +0x10.
    "INVLIST_ENTRIES_OFF": "BGSInventoryList.data",
    "INVLIST_COUNT_OFF": "BGSInventoryList.data + 0x10",
    "INVLIST_MUTEX_OFF": "BGSInventoryList.rwLock",
    "INVENTORY_ITEM_OBJ_OFF": "BGSInventoryItem.object",

    # --- Scene graph (the skin pipeline Milestone 2 needs) ---------------
    # NiTransform is rotate(0x00, NiMatrix3 = 0x30) + translate(0x30) +
    # scale(0x3C), sizeof 0x40 — which is why world - local == 0x40.
    "LOADED_REF_DATA_OFF": "TESObjectREFR.loadedData",
    "NIREFOBJECT_REFCOUNT_OFF": "NiRefObject.refCount",
    "NI_AV_LOCAL_TRANSLATE_OFF": "NiAVObject.local + NiTransform.translate",
    "NI_AV_WORLD_TRANSLATE_OFF": "NiAVObject.world + NiTransform.translate",
    "NI_AV_WORLD_ROTATE_OFF": "NiAVObject.world + NiTransform.rotate",
    "NI_AV_WORLD_SCALE_OFF": "NiAVObject.world + NiTransform.scale",
    # NiMatrix3 is three NiPoint4 rows; sizeof(NiPoint4) == 0x10.
    "NI_MATRIX3_ROW_STRIDE": "0x10",

    # --- PlayerCamera (CommonLibF4 PlayerCamera members) -----------------
    # cameraStates is a C array; the member parser now records array names.
    "PLAYER_CAMERA_STATES_OFF": "PlayerCamera.cameraStates",
    "PLAYER_CAMERA_BUF_POS_OFF": "PlayerCamera.bufferedCameraPos",
    "PLAYER_CAMERA_BUF_VAL_OFF": "PlayerCamera.cameraPosBuffered",
    # Our name says "active state index"; CommonLib labels this field
    # furnitureCollisionGroup. Offset matches; naming is project debt.
    "PLAYER_CAMERA_ACTIVE_OFF": "PlayerCamera.furnitureCollisionGroup",

    # --- Armor / ARMO addon table ----------------------------------------
    "TESGLOBAL_VALUE_OFF": "TESGlobal.value",
    "TESMODEL_PATH_BSFIXEDSTR_OFF": "TESModel.model",
    "TESOBJECTARMO_ADDON_ARR_OFF": "TESObjectARMO.modelArray",
    # modelArray is a BSTArray; its _size sits 0x10 past the allocator.
    "TESOBJECTARMO_ADDON_COUNT_OFF": "TESObjectARMO.modelArray + 0x10",
    "TESOBJECTARMO_ADDON_ARMA_PTR_OFF": "ArmorAddon.armorAddon",
    "TESOBJECTARMO_ADDON_ENTRY_PRIORITY_OFF": "ArmorAddon.index",
    # default priority = InstanceData.index living inside TESObjectARMO.data
    "TESOBJECTARMO_DEFAULT_PRIORITY_OFF": "TESObjectARMO.data + InstanceData.index",
    "TESOBJECTARMO_INSTANCEDATA_PRIORITY_OFF": "InstanceData.index",
    # BGSBipedObjectForm is a base of TESObjectARMO at 0x1E0; its
    # bipedModelData sits at +0x08 and bipedObjectSlots is the first u32.
    "TESOBJECTARMO_BIPED_SLOTS_OFF": (
        "0x1E0 + BGSBipedObjectForm.bipedModelData + BIPED_MODEL.bipedObjectSlots"
    ),

    # --- ExtraData list walkers ------------------------------------------
    "BSEXTRADATA_NEXT_OFF": "BSExtraData.next",

    # --- Actor -----------------------------------------------------------
    # See NOTES: ACTOR_AIPROCESS_OFF is misnamed; it is combatController.
    "ACTOR_AIPROCESS_OFF": "Actor.combatController",
    # The real AIProcess* pointer (CommonLib Actor::currentProcess).
    "ACTOR_AIPROCESS_PTR_OFF": "Actor.currentProcess",
}

# Constants where our name and CommonLibF4's disagree in a way that needs a
# human, printed alongside CONFIRMED/MISMATCH so nobody "fixes" it blindly.
NOTES: dict[str, str] = {
    "ACTOR_AIPROCESS_OFF": (
        "NAMING DEBT (confirmed 2026-08-02). Our constant is 0x328 and is\n"
        "       used for combat paths. CommonLibF4 1.10.163 puts\n"
        "       Actor::combatController at 0x328 and Actor::currentProcess\n"
        "       (AIProcess*) at 0x300. offsets.h already has the real\n"
        "       AIProcess slot as ACTOR_AIPROCESS_PTR_OFF = 0x300. Do NOT\n"
        "       'fix' ACTOR_AIPROCESS_OFF to 0x300 — combat readers want\n"
        "       the CombatController*. Rename when convenient."
    ),
    "PLAYER_CAMERA_ACTIVE_OFF": (
        "Our comment calls this 'active state index'; CommonLibF4 names the\n"
        "       field at 0x1A0 furnitureCollisionGroup (u32). Offset is right;\n"
        "       the semantic label is project debt from the IDA ctor pass."
    ),
}

# Constants that are sizes/indices rather than offsets, checked against
# `static_assert(sizeof(X) == N)` instead of a member offset.
SIZE_MAP: dict[str, str] = {
    "INVENTORY_ITEM_STRIDE": "BGSInventoryItem",
    "TESOBJECTARMO_ADDON_ENTRY_STRIDE": "ArmorAddon",
    "PLAYER_INSTANCE_SIZE": "PlayerCharacter",
    # BSFIXEDSTRING_CSTR_OFF = 0x18 is sizeof(BSStringPool::Entry) — the
    # c_str lives at entry+1 (BSStringPool.h). Do NOT map via SIZE_MAP
    # "Entry": many nested Entry classes exist and first-wins is wrong.
    # NI_CAMERA_* need a NiCamera header this CommonLib checkout lacks.
}


@dataclass
class Layout:
    members: dict[str, int]      # "Class.member" -> offset
    sizes: dict[str, int]        # "Class" -> sizeof


# Member with optional C-array bounds, e.g. `cameraStates[CameraStates::kTotal]; // 0E0`
_MEMBER = re.compile(
    r"^\s+(?:[\w:<>,\*&\s\{\}]+?)\b(\w+)\s*(?:\[[^\]]*\])?\s*(?:\{[^}]*\})?\s*;"
    r"\s*//\s*([0-9A-Fa-f]+)\s*$"
)
# CommonLibF4 writes `class __declspec(novtable) TESForm : ...`, so the
# attribute has to be skipped or every such class is recorded as "__declspec".
_CLASS = re.compile(
    r"^\s*(?:class|struct)\s+"
    r"(?:(?:__declspec|alignas)\s*\([^)]*\)\s*)*"
    r"(\w+)"
)
_SIZEOF = re.compile(r"static_assert\(sizeof\((\w+)\)\s*==\s*(0x[0-9A-Fa-f]+|\d+)\)")


def parse_commonlib(root: Path) -> Layout:
    """Collect `Class.member -> offset` and `Class -> sizeof` from the headers.

    Members must be attributed to the class whose body actually encloses them,
    which means tracking brace depth. Simply remembering "the last class seen"
    is wrong and quietly so: CommonLibF4 declares nested functors and enums
    before the trailing `// members` block, so TESForm's members would get
    filed under whatever nested type was declared last.
    """
    inc = root / "CommonLibF4" / "include"
    if not inc.is_dir():
        inc = root / "include"
    if not inc.is_dir():
        raise SystemExit(f"no CommonLibF4 include dir under {root}")

    members: dict[str, int] = {}
    sizes: dict[str, int] = {}

    for path in sorted(inc.rglob("*.h")):
        depth = 0
        pending: str | None = None
        stack: list[tuple[str, int]] = []   # (class name, depth of its body)

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _SIZEOF.search(line)
            if m:
                sizes.setdefault(m.group(1), int(m.group(2), 0))

            m = _CLASS.match(line)
            if m and not line.rstrip().endswith(";"):   # skip forward decls
                pending = m.group(1)

            opens = line.count("{")
            closes = line.count("}")

            if opens:
                depth += opens
                if pending:
                    stack.append((pending, depth))
                    pending = None
            if closes:
                depth -= closes
                while stack and stack[-1][1] > depth:
                    stack.pop()

            if stack and not m:
                mm = _MEMBER.match(line)
                if mm:
                    key = f"{stack[-1][0]}.{mm.group(1)}"
                    # First definition wins; later headers may redeclare.
                    members.setdefault(key, int(mm.group(2), 16))
    return Layout(members, sizes)


def resolve(expr: str, layout: Layout) -> int | None:
    """Evaluate a MAP expression like `A.b + C.d + 0x10`."""
    total = 0
    for term in (t.strip() for t in expr.split("+")):
        if term.startswith("0x") or term.isdigit():
            total += int(term, 0)
            continue
        if term not in layout.members:
            return None
        total += layout.members[term]
    return total


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commonlib", type=Path, required=True,
                    help="path to a CommonLibF4 checkout (master branch)")
    ap.add_argument("--verbose", action="store_true",
                    help="also list every UNMAPPED constant")
    args = ap.parse_args()

    layout = parse_commonlib(args.commonlib)
    print(f"CommonLibF4: {len(layout.members)} annotated members, "
          f"{len(layout.sizes)} sizeof asserts")

    ours = {i.name: i for i in parse_field_offsets(OFFSETS_H)}
    print(f"offsets.h:   {len(ours)} FIELD constants\n")

    confirmed, mismatched, unresolved, unmapped = [], [], [], []

    for name, item in ours.items():
        expr = MAP.get(name)
        size_of = SIZE_MAP.get(name)
        if expr is None and size_of is None:
            unmapped.append(name)
            continue
        want = (layout.sizes.get(size_of) if size_of is not None
                else resolve(expr, layout))
        if want is None:
            unresolved.append((name, expr or size_of))
            continue
        got = int(item.value, 0)
        (confirmed if got == want else mismatched).append((name, got, want))

    for name, got, want in confirmed:
        print(f"  CONFIRMED  {name:<26} 0x{got:X}")
        if name in NOTES:
            print(f"       NOTE: {NOTES[name]}")
    for name, got, want in mismatched:
        print(f"  MISMATCH   {name:<26} ours 0x{got:X}  CommonLibF4 0x{want:X}")
        if name in NOTES:
            print(f"       NOTE: {NOTES[name]}")
    for name, expr in unresolved:
        print(f"  ?? no such symbol in CommonLibF4: {name} -> {expr}")

    print(f"\n  {len(confirmed)} confirmed, {len(mismatched)} mismatched, "
          f"{len(unresolved)} unresolvable, {len(unmapped)} unmapped "
          f"(of {len(ours)})")
    if unmapped:
        print("\n  UNMAPPED means UNVERIFIED — add them to MAP as you work "
              "through the tier.")
        if args.verbose:
            for n in sorted(unmapped):
                print(f"    {n}")

    return 1 if mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
