"""Resolve a list of Address Library IDs and expand known_lookup MAP."""
from __future__ import annotations

import csv
from pathlib import Path

DB = Path("tools/addresslib/offsets-1-10-163-0.csv")

# id -> our constant name candidate (may need PE verify)
CANDIDATES: dict[int, str] = {
    303410: "PLAYER_SINGLETON_RVA",
    1171980: "PLAYER_CAMERA_SINGLETON_RVA",
    652767: "ENGINE_HEAP_ALLOC_RVA",  # MemoryManager::Allocate
    422985: "FORM_CACHE_SINGLETON_RVA",
    179707: "TESOBJECTREFR_VTABLE_RVA",
    1305073: "NI_CAMERA_VTABLE_RVA",
    901626: "HANDLE_GET_OR_ALLOC_RVA",  # BSPointerHandleManager::GetHandle
    967277: "REFHANDLE_RESOLVE_RVA",  # GetSmartPointer — VERIFY
    1085394: "SCRAPHEAP_ALLOC?",  # ScrapHeap::Allocate
    343176: "MEMMGR_SINGLETON?",
    711558: "TESDATAHANDLER_SINGLETON?",
    1390486: "BSSTRINGPOOL_BUCKET?",
    501899: "BGS_INV_INTERFACE_SINGLETON?",
    1174340: "ACTOR_EQUIP_MGR_SINGLETON?",
}


def load_db() -> dict[int, int]:
    db: dict[int, int] = {}
    with DB.open(newline="", encoding="utf-8") as f:
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


def main() -> None:
    db = load_db()
    print(f"db size {len(db)}")
    for rid, name in CANDIDATES.items():
        addr = db.get(rid)
        if addr is None:
            print(f"  MISSING {rid} {name}")
        else:
            print(f"  0x{addr:08X}  ID {rid:8}  {name}")

    # also resolve matcher exact/strong for comparison
    print("\nmatcher exact/strong:")
    with Path("docs/port_matched.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("confidence") in ("exact", "strong") and row.get("ported_rva"):
                print(
                    f"  {row['ported_rva']:>12}  {row['confidence']:10}  {row['name']}"
                )


if __name__ == "__main__":
    main()
