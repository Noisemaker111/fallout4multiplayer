"""Resolve KNOWN-tier RVAs via the Address Library dump for 1.10.163.

Step D of docs/START_HERE.md. CommonLibF4 wraps many engine APIs with
`REL::ID(N)` which the Address Library maps to a per-build RVA. We keep a
hand-curated map of our constant names to those IDs, then look them up in
the public dump:

    https://github.com/alandtse/fallout_vr_address_library
    offsets-1-10-163-0.csv  (id,fo4_addr)

Download once:

    mkdir tools/addresslib
    curl -L -o tools/addresslib/offsets-1-10-163-0.csv \\
      https://raw.githubusercontent.com/alandtse/fallout_vr_address_library/main/offsets-1-10-163-0.csv

Usage:

    python tools/known_lookup.py
    python tools/known_lookup.py --csv tools/addresslib/offsets-1-10-163-0.csv \\
        --write-worklist docs/port_worklist_minimal.csv

Only IDs listed in MAP are resolved. A missing ID or missing CSV is reported,
never guessed. Applying the values into offsets.h is a separate step — this
tool only fills the work-list `ported_addr` column (and only when --write).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Our constant name -> (REL::ID, CommonLibF4 source note).
# Only entries where we are confident the ID IS the same symbol our constant
# names. Prefer GetSingleton pointer slots and well-known free functions.
MAP: dict[str, tuple[int, str]] = {
    # Singletons (data RVAs — pointer slots, not function starts)
    "PLAYER_SINGLETON_RVA": (
        303410,
        "PlayerCharacter.h GetSingleton → NiPointer<PlayerCharacter>*",
    ),
    "PLAYER_CAMERA_SINGLETON_RVA": (
        1171980,
        "TESCamera.h PlayerCamera::GetSingleton → PlayerCamera**",
    ),
    # Memory
    "ENGINE_HEAP_ALLOC_RVA": (
        652767,
        "MemoryManager.h MemoryManager::Allocate — VERIFY: may be a different "
        "heap helper than our sub_1416579C0; cross-check against port_matched "
        "exact row before applying",
    ),
    # Form table (not the same as LOOKUP_BY_FORMID free function — CommonLib
    # does a hash-map walk instead of calling our sub_140311850. Kept as a
    # related singleton for the form-cache work.)
    "FORM_CACHE_SINGLETON_RVA": (
        422985,
        "TESForms.h GetAllForms → BSTHashMap<u32,TESForm*>**  "
        "(related, not identical to LOOKUP_BY_FORMID_RVA)",
    ),
    # Vtables — first entry of CommonLib's VTABLE_IDs array is the primary
    # RTTI/vtbl used by type checks. Spot-check in IDA before applying.
    "TESOBJECTREFR_VTABLE_RVA": (
        179707,
        "VTABLE_IDs.h TESObjectREFR[0]",
    ),
    "NI_CAMERA_VTABLE_RVA": (
        1305073,
        "VTABLE_IDs.h NiCamera[0]",
    ),
    # Handle manager (BSPointerHandle.h)
    "HANDLE_GET_OR_ALLOC_RVA": (
        901626,
        "BSPointerHandleManagerInterface::GetHandle",
    ),
    "REFHANDLE_RESOLVE_RVA": (
        967277,
        "BSPointerHandleManagerInterface::GetSmartPointer — API is "
        "(handle, NiPointer&) not our older single-out form; verify call sites",
    ),
}


def load_db(path: Path) -> dict[int, int]:
    db: dict[int, int] = {}
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r, None)
        if not header or header[0].lower() not in ("id", "index"):
            # still try: first col id, second hex addr
            pass
        for row in r:
            if len(row) < 2:
                continue
            try:
                i = int(row[0])
                addr = int(row[1], 16)
            except ValueError:
                continue
            db[i] = addr
    return db


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--csv",
        type=Path,
        default=REPO / "tools" / "addresslib" / "offsets-1-10-163-0.csv",
        help="Address Library dump (id,fo4_addr)",
    )
    ap.add_argument(
        "--write-worklist",
        type=Path,
        default=None,
        help="optional work-list CSV to fill ported_addr for matched names",
    )
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"missing Address Library dump: {args.csv}", file=sys.stderr)
        print("see the module docstring for the download URL", file=sys.stderr)
        return 2

    print(f"loading {args.csv} ...")
    db = load_db(args.csv)
    print(f"  {len(db)} IDs\n")

    resolved: dict[str, int] = {}
    for name, (rid, note) in MAP.items():
        addr = db.get(rid)
        if addr is None:
            print(f"  MISSING   {name:<32} ID {rid}")
            continue
        resolved[name] = addr
        print(f"  OK        {name:<32} ID {rid:<8} -> 0x{addr:08X}")
        print(f"            {note}")

    print(f"\n  {len(resolved)}/{len(MAP)} resolved")

    if args.write_worklist:
        path = args.write_worklist
        rows = []
        with path.open(newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            fieldnames = r.fieldnames or []
            for row in r:
                name = row.get("name", "")
                if name in resolved and not row.get("ported_addr"):
                    row["ported_addr"] = f"0x{resolved[name]:08X}"
                    row["verified"] = "addresslib"
                rows.append(row)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"  wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
