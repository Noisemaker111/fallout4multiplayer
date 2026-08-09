"""One-shot status dump for the port session."""
from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    wl = REPO / "docs" / "port_worklist_minimal.csv"
    print("=== minimal KNOWN unfilled ===")
    for row in csv.DictReader(wl.open(encoding="utf-8")):
        if row["tier"] == "KNOWN" and not (row.get("ported_addr") or "").strip():
            c = (row.get("comment") or "")[:70]
            print(f"  {row['name']:40} {row['value']:12} {c}")

    filled = {
        row["name"]
        for row in csv.DictReader(wl.open(encoding="utf-8"))
        if (row.get("ported_addr") or "").strip()
    }
    print("\n=== exact/strong matcher ===")
    for row in csv.DictReader((REPO / "docs" / "port_matched.csv").open(encoding="utf-8")):
        if row.get("confidence") in ("exact", "strong") and row.get("ported_rva"):
            mark = "ALREADY" if row["name"] in filled else "TODO"
            print(
                f"  [{mark:7}] {row['name']:48} "
                f"{row['source_rva']} -> {row['ported_rva']} [{row['confidence']}]"
            )

    print("\n=== filled summary ===")
    from collections import Counter

    by, filled_c = Counter(), Counter()
    for row in csv.DictReader(wl.open(encoding="utf-8")):
        by[row["tier"]] += 1
        if (row.get("ported_addr") or "").strip():
            filled_c[row["tier"]] += 1
    print(f"  totals {dict(by)}")
    print(f"  filled {dict(filled_c)}")


if __name__ == "__main__":
    main()
