"""Port code RVAs by unique string cross-references in the target binary."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from fnfingerprint import Image  # noqa: E402
from strxref import StringXrefs  # noqa: E402

# Distinctive strings that should pin a single function (or a tiny set).
STRING_HINTS: dict[str, list[str]] = {
    "MAIN_MENU_REGISTRAR_RVA": [
        "onContinuePress",
        "ContinueGame",
        "requestLoadGame",
    ],
    "PLACE_AT_ME_RVA": ["PlaceAtMe"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument(
        "--worklist",
        type=Path,
        default=REPO / "docs" / "port_worklist_minimal.csv",
    )
    ap.add_argument("--write-worklist", action="store_true")
    ap.add_argument("--probe", nargs="*", default=[],
                    help="extra strings to probe")
    args = ap.parse_args()

    print(f"loading {args.exe} ...")
    img = Image(args.exe)
    print("building string xrefs (this can take a minute) ...")
    xref = StringXrefs(img)
    print(f"  {xref.stats()}")

    # Extra probes
    for s in args.probe:
        fns = xref.by_string.get(s, set())
        print(f"  probe {s!r}: {len(fns)} functions")
        for r in sorted(fns)[:12]:
            print(f"      0x{r:08X}")

    results: dict[str, int] = {}
    for name, strings in STRING_HINTS.items():
        candidates: set[int] | None = None
        for s in strings:
            fns = xref.by_string.get(s, set())
            print(f"  string {s!r} -> {len(fns)} functions")
            candidates = fns if candidates is None else (candidates & fns)
        if not candidates:
            print(f"  {name}: no intersection")
            continue
        if len(candidates) == 1:
            rva = next(iter(candidates))
            results[name] = rva
            print(f"  {name}: UNIQUE 0x{rva:08X}")
        else:
            print(f"  {name}: {len(candidates)} candidates")
            for r in sorted(candidates)[:12]:
                print(f"      0x{r:08X}")

    if args.write_worklist and results:
        rows = []
        with args.worklist.open(encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            fields = r.fieldnames
            for row in r:
                n = row.get("name") or ""
                if n in results and not (row.get("ported_addr") or "").strip():
                    row["ported_addr"] = f"0x{results[n]:08X}"
                    row["verified"] = "string-xref"
                rows.append(row)
        with args.worklist.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(results)} into {args.worklist}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
