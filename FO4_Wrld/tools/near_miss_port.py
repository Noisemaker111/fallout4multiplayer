"""Recover ports when an NG RVA lands a few bytes into a 1.10.163 function.

Cross-checks the candidate against the 1.11.221 backup (when available) by
shared string xrefs. Accepts only when:

  * target delta into enclosing function is small (< max_delta)
  * candidate is a real function start
  * string-set Jaccard vs the source function (on backup) is high, OR
    the candidate has at least one unique string also on the source fn

Usage:
    python tools/near_miss_port.py \\
      --target Fallout4.exe.unpacked.exe \\
      --source Fallout4_downgradeBackup.exe \\
      --write
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from fnfingerprint import Image  # noqa: E402
from offset_audit import parse_offsets, Kind, OFFSETS_H, strip_non_minimal_regions  # noqa: E402
from strxref import StringXrefs  # noqa: E402

_CONST = re.compile(
    r"(constexpr\s+std::uintptr_t\s+)(\w+)(\s*=\s*)(0x[0-9A-Fa-f]+)(\s*;)"
)


def enclosing(img: Image, rva: int) -> tuple[int, int] | None:
    """Return (start, end) of the function containing rva, if any."""
    fns = img.functions()
    # binary search
    lo, hi = 0, len(fns) - 1
    ans = None
    while lo <= hi:
        mid = (lo + hi) // 2
        s, e = fns[mid]
        if s <= rva < e:
            return s, e
        if rva < s:
            hi = mid - 1
        else:
            lo = mid + 1
    return ans


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    u = a | b
    if not u:
        return 0.0
    return len(a & b) / len(u)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, required=True, help="1.10.163 unpacked")
    ap.add_argument("--source", type=Path, required=True, help="1.11.221 backup")
    ap.add_argument("--max-delta", type=lambda x: int(x, 0), default=0x80)
    ap.add_argument("--min-jaccard", type=float, default=0.25)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--minimal", action="store_true", default=True)
    args = ap.parse_args()

    print("loading target (1.10.163) ...")
    tgt = Image(args.target)
    print("loading source (1.11.x backup) ...")
    src = Image(args.source)
    print("string xrefs target ...")
    tx = StringXrefs(tgt)
    print(f"  {tx.stats()}")
    print("string xrefs source ...")
    sx = StringXrefs(src)
    print(f"  {sx.stats()}")

    text = OFFSETS_H.read_text(encoding="utf-8", errors="replace")
    entries = parse_offsets(OFFSETS_H)
    # only code
    code = [e for e in entries if e.kind == Kind.CODE]

    accepted: dict[str, int] = {}
    print(f"\nscanning {len(code)} code constants (max_delta=0x{args.max_delta:X})...\n")

    for e in code:
        enc = enclosing(tgt, e.rva)
        if not enc:
            continue
        start, end = enc
        delta = e.rva - start
        if delta == 0:
            continue  # already a hit
        if delta > args.max_delta:
            continue

        # source side: prefer function at same rva, else enclosing
        src_enc = enclosing(src, e.rva)
        if src_enc:
            src_start = src_enc[0]
        else:
            # try exact
            src_start = e.rva if any(s == e.rva for s, _ in src.functions()) else None
        if src_start is None:
            continue

        t_strs = tx.strings_of(start)
        s_strs = sx.strings_of(src_start)
        jac = jaccard(t_strs, s_strs)
        shared = t_strs & s_strs
        # unique-string boost: any unique string of source also on target fn
        src_unique = {s for s in s_strs if len(sx.by_string.get(s, ())) == 1}
        unique_hit = bool(src_unique & t_strs)

        ok = (jac >= args.min_jaccard and len(shared) >= 1) or (
            unique_hit and len(shared) >= 1
        )
        mark = "ACCEPT" if ok else "skip  "
        print(
            f"  {mark}  {e.name:<36} NG 0x{e.rva:08X} -> "
            f"0x{start:08X} (delta +0x{delta:X})  "
            f"jac={jac:.2f} shared={len(shared)} uniq={unique_hit}"
        )
        if ok:
            accepted[e.name] = start

    print(f"\naccepted {len(accepted)}")
    if not accepted:
        return 0

    if args.write:
        def repl(m: re.Match) -> str:
            name = m.group(2)
            if name not in accepted:
                return m.group(0)
            new = accepted[name]
            old = int(m.group(4), 16)
            if old == new:
                return m.group(0)
            return f"{m.group(1)}{name}{m.group(3)}0x{new:08X}{m.group(5)}"

        new_text = _CONST.sub(repl, text)
        # annotate comments lightly
        OFFSETS_H.write_text(new_text, encoding="utf-8")
        print(f"wrote {OFFSETS_H}")
        for n, r in sorted(accepted.items()):
            print(f"  {n} -> 0x{r:08X}")
    else:
        print("(dry-run; pass --write to apply)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
