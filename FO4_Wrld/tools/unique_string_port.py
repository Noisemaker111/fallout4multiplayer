"""Port remaining code RVAs via unique-string seeds from a near build.

For each code constant still pointing at a 1.11.191 RVA:
  1. Locate the enclosing function on the source binary (1.11.221 backup)
  2. Collect strings that function references that are UNIQUE in the source
  3. Look those strings up on the target (1.10.163); if exactly one target
     function references any of them (and they agree), accept it.

This is the same seed strategy as port_match, scoped to the residual set.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from fnfingerprint import Image  # noqa: E402
from offset_audit import parse_offsets, Kind, OFFSETS_H  # noqa: E402
from strxref import StringXrefs  # noqa: E402

_CONST = re.compile(
    r"(constexpr\s+std::uintptr_t\s+)(\w+)(\s*=\s*)(0x[0-9A-Fa-f]+)(\s*;)"
)


def enclosing(img: Image, rva: int) -> int | None:
    fns = img.functions()
    lo, hi = 0, len(fns) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s, e = fns[mid]
        if s <= rva < e:
            return s
        if rva < s:
            hi = mid - 1
        else:
            lo = mid + 1
    # exact start?
    for s, e in fns:
        if s == rva:
            return s
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    print("loading ...")
    tgt, src = Image(args.target), Image(args.source)
    print("xrefs target ...")
    tx = StringXrefs(tgt)
    print(f"  {tx.stats()}")
    print("xrefs source ...")
    sx = StringXrefs(src)
    print(f"  {sx.stats()}")

    code = [e for e in parse_offsets(OFFSETS_H) if e.kind == Kind.CODE]
    # Skip already-good targets
    tgt_starts = {s for s, _ in tgt.functions()}

    accepted: dict[str, int] = {}
    ambiguous = 0
    no_seed = 0

    for e in code:
        if e.rva in tgt_starts:
            continue  # already lands on a function start
        src_fn = enclosing(src, e.rva)
        if src_fn is None:
            no_seed += 1
            continue
        src_strs = sx.strings_of(src_fn)
        unique = {s for s in src_strs if len(sx.by_string.get(s, ())) == 1}
        if not unique:
            no_seed += 1
            continue

        # target functions that reference any unique string
        votes: dict[int, int] = {}
        for s in unique:
            for fn in tx.by_string.get(s, ()):
                # only count if that string is also unique (or rare) on target
                if len(tx.by_string.get(s, ())) <= 2:
                    votes[fn] = votes.get(fn, 0) + 1
        if not votes:
            no_seed += 1
            continue
        # best by vote count
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        best_fn, best_n = ranked[0]
        if len(ranked) > 1 and ranked[1][1] == best_n:
            ambiguous += 1
            print(
                f"  AMBIG  {e.name:<36} votes={best_n} "
                f"candidates={len([x for x in ranked if x[1]==best_n])}"
            )
            continue
        if best_n < 1:
            no_seed += 1
            continue
        accepted[e.name] = best_fn
        print(
            f"  HIT    {e.name:<36} 0x{e.rva:08X} -> 0x{best_fn:08X}  "
            f"votes={best_n} seeds={len(unique)}"
        )

    print(
        f"\naccepted={len(accepted)} ambiguous={ambiguous} no_seed={no_seed} "
        f"of {len(code)} code"
    )

    if args.write and accepted:
        text = OFFSETS_H.read_text(encoding="utf-8")

        def repl(m: re.Match) -> str:
            name = m.group(2)
            if name not in accepted:
                return m.group(0)
            new = accepted[name]
            return f"{m.group(1)}{name}{m.group(3)}0x{new:08X}{m.group(5)}"

        OFFSETS_H.write_text(_CONST.sub(repl, text), encoding="utf-8")
        print(f"wrote {len(accepted)} into offsets.h")
    elif accepted:
        print("(dry-run; pass --write to apply)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
