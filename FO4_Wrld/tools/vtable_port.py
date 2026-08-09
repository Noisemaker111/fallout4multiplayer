"""Port ni_offsets / offsets.h vtable RVAs via CommonLibF4 VTABLE_IDs + AddressLib.

Resolves REL::ID arrays from VTABLE_IDs.h and writes PE-verified .rdata
addresses into a candidate map.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VTABLE_H = (
    REPO
    / "CommonLibF4"
    / "CommonLibF4"
    / "include"
    / "RE"
    / "VTABLE_IDs.h"
)
ADDRDB = REPO / "tools" / "addresslib" / "offsets-1-10-163-0.csv"

# Our constant name -> CommonLib VTABLE_IDs symbol (first array entry = primary)
WANT: dict[str, str] = {
    "NINODE_VTABLE_RVA": "NiNode",
    "NIAVOBJECT_VTABLE_RVA": "NiAVObject",
    "NIREFOBJECT_VTABLE_RVA": "NiRefObject",
    "BSFADENODE_VTABLE_RVA": "BSFadeNode",
    "BSLEAFANIMNODE_VTABLE_RVA": "BSLeafAnimNode",
    "BSSKIN_INSTANCE_VTABLE_RVA": "BSSkin__Instance",
    "BSGEOMETRY_VTABLE_RVA": "BSGeometry",
    "BSTRISHAPE_VTABLE_RVA": "BSTriShape",
    "BSDYNAMICTRISHAPE_VTABLE_RVA": "BSDynamicTriShape",
    "BSSUBINDEXTRISHAPE_VTABLE_RVA": "BSSubIndexTriShape",
    "NI_CAMERA_VTABLE_RVA": "NiCamera",
    "TESOBJECTREFR_VTABLE_RVA": "TESObjectREFR",
    "MAIN_CULLING_CAMERA_VTABLE_RVA": "MainCullingCamera",
}


def parse_vtable_ids(path: Path) -> dict[str, list[int]]:
    """name -> list of REL::IDs."""
    text = path.read_text(encoding="utf-8", errors="replace")
    # inline constexpr std::array<REL::ID, N> Name{ REL::ID(a), REL::ID(b), ... };
    pat = re.compile(
        r"inline constexpr std::array<REL::ID,\s*\d+>\s+(\w+)\s*\{([^}]+)\}",
        re.MULTILINE,
    )
    id_pat = re.compile(r"REL::ID\((\d+)\)")
    out: dict[str, list[int]] = {}
    for m in pat.finditer(text):
        name = m.group(1)
        ids = [int(x) for x in id_pat.findall(m.group(2))]
        if ids:
            out[name] = ids
    return out


def load_db(path: Path) -> dict[int, int]:
    db: dict[int, int] = {}
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 2:
                continue
            try:
                db[int(row[0])] = int(row[1], 16)
            except ValueError:
                continue
    return db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commonlib-vtables", type=Path, default=VTABLE_H)
    ap.add_argument("--db", type=Path, default=ADDRDB)
    args = ap.parse_args()

    if not args.commonlib_vtables.is_file():
        print(f"missing {args.commonlib_vtables}", file=sys.stderr)
        return 2
    if not args.db.is_file():
        print(f"missing {args.db}", file=sys.stderr)
        return 2

    tables = parse_vtable_ids(args.commonlib_vtables)
    print(f"parsed {len(tables)} VTABLE_IDs symbols")
    db = load_db(args.db)
    print(f"addresslib {len(db)} ids\n")

    for const, sym in WANT.items():
        ids = tables.get(sym)
        if not ids:
            # try alternate spellings
            alts = [k for k in tables if k.replace("_", "") == sym.replace("_", "")]
            print(f"  MISS  {const:<36} symbol {sym!r} not in VTABLE_IDs"
                  + (f"  alts={alts[:5]}" if alts else ""))
            continue
        rid = ids[0]
        addr = db.get(rid)
        if addr is None:
            print(f"  MISS  {const:<36} ID {rid} not in addresslib")
            continue
        extra = ""
        if len(ids) > 1:
            extras = []
            for i in ids[1:4]:
                a = db.get(i)
                if a is not None:
                    extras.append(f"0x{a:08X}")
            if extras:
                extra = f"  (+{len(ids)-1} more: {', '.join(extras)})"
        print(f"  OK    {const:<36} ID {rid:<8} -> 0x{addr:08X}{extra}")

    # Also list fuzzy matches for skin/fade/geometry if exact miss
    print("\n--- fuzzy related symbols ---")
    keys = ("Skin", "Fade", "TriShape", "Geometry", "NiNode", "NiAV", "NiCamera", "Culling")
    for name, ids in sorted(tables.items()):
        if any(k.lower() in name.lower() for k in keys):
            rid = ids[0]
            addr = db.get(rid)
            if addr is not None:
                print(f"  {name:<50} 0x{addr:08X}  (ID {rid})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
