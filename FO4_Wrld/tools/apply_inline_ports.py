"""Apply port_matched.csv exact/strong rows that name inline sub_14XXXXXXX.

Rewrites `sub_140XXXXXXX` / `0x00XXXXXX` occurrences in the source tree for
rows that PE-verify as function starts on the target binary.

Usage:
    python tools/apply_inline_ports.py --exe <unpacked> --write
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pefile

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "fw_native" / "src"
MATCHED = REPO / "docs" / "port_matched.csv"
IMAGE_BASE = 0x140000000


def function_starts(pe: pefile.PE) -> set[int]:
    starts: set[int] = set()
    directory = getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", None)
    if not directory:
        return starts
    for fn in directory:
        begin = getattr(fn.struct, "BeginAddress", 0)
        if begin and (begin & 0xF) == 0:
            starts.add(begin)
    return starts


def load_exact_strong() -> list[tuple[str, int, int, str]]:
    """(name, source_rva, ported_rva, confidence)."""
    rows = []
    with MATCHED.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            conf = row.get("confidence") or ""
            if conf not in ("exact", "strong"):
                continue
            src = (row.get("source_rva") or "").strip()
            dst = (row.get("ported_rva") or "").strip()
            if not src or not dst:
                continue
            s = int(src, 16)
            d = int(dst, 16)
            if s >= IMAGE_BASE:
                s -= IMAGE_BASE
            if d >= IMAGE_BASE:
                d -= IMAGE_BASE
            rows.append((row.get("name") or "", s, d, conf))
    return rows


def rewrite_file(path: Path, pairs: list[tuple[int, int]], dry: bool) -> int:
    """Replace source RVA forms with ported. Returns number of substitutions."""
    text = path.read_text(encoding="utf-8", errors="replace")
    original = text
    n = 0
    for src, dst in pairs:
        # sub_14XXXXXXX (7 hex digits after 14)
        src_hex7 = f"{src:07X}"
        dst_hex7 = f"{dst:07X}"
        # also absolute IDA form sub_140XXXXXXX
        for old, new in (
            (f"sub_14{src_hex7}", f"sub_14{dst_hex7}"),
            (f"sub_140{src_hex7}", f"sub_140{dst_hex7}"),
            (f"0x{src:08X}", f"0x{dst:08X}"),
            (f"0x{src:07X}", f"0x{dst:07X}"),
            (f"0x{src:X}", f"0x{dst:X}"),
        ):
            if old in text and old != new:
                count = text.count(old)
                text = text.replace(old, new)
                n += count
    if n and not dry:
        path.write_text(text, encoding="utf-8")
    elif n and dry and text != original:
        pass
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    dry = not args.write

    pe = pefile.PE(str(args.exe), fast_load=False)
    starts = function_starts(pe)

    accepted: list[tuple[int, int]] = []
    print("candidates:")
    for name, src, dst, conf in load_exact_strong():
        ok = dst in starts
        mark = "OK  " if ok else "FAIL"
        print(f"  {mark}  {name:50} 0x{src:08X} -> 0x{dst:08X} [{conf}]")
        if ok:
            accepted.append((src, dst))

    # dedupe by source
    by_src: dict[int, int] = {}
    for s, d in accepted:
        by_src[s] = d
    pairs = list(by_src.items())
    print(f"\n{len(pairs)} unique source->dest pairs to rewrite\n")

    total = 0
    for path in sorted(SRC.rglob("*")):
        if path.suffix not in (".cpp", ".h", ".hpp"):
            continue
        n = rewrite_file(path, pairs, dry=dry)
        if n:
            action = "would touch" if dry else "touched"
            print(f"  {action} {path.relative_to(SRC)}  ({n} subs)")
            total += n

    print(f"\ntotal substitutions: {total}" + (" (dry-run)" if dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
