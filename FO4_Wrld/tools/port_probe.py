"""Feasibility probe for automated cross-build address porting.

Question this answers: if we try to port the pinned addresses by matching
functions between two builds, how much signal is actually there?

Method
------
Every pinned code address is a function start in the build it was derived from
(1.11.191). We don't have that build, but we have 1.11.221, which the audit
shows is the same code shifted by small deltas — so the function *containing*
each pinned RVA in 1.11.221 is very probably the same function. That gives an
anchor without ever holding 1.11.191.

For each anchor we fingerprint the function and ask whether it carries anything
a matcher could key on across a major-version jump: referenced string literals
first, distinctive immediate constants second.

Verdict
-------
Combat/AI internals frequently reference nothing unique. If most anchors come
back featureless, structural matching will be weak and the port is genuinely
manual IDA work — worth knowing before building a matcher rather than after.

Usage
-----
    python tools/port_probe.py <next-gen exe> [--sample N]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnfingerprint import Image  # noqa: E402
from offset_audit import (  # noqa: E402
    OFFSETS_H,
    Kind,
    find_candidates,
    parse_inline_rvas,
    parse_offsets,
)

SRC_ROOT = Path(__file__).resolve().parent.parent / "fw_native" / "src"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", nargs="?", type=Path,
                    help="next-gen Fallout4.exe to anchor against")
    ap.add_argument("--sample", type=int, default=0,
                    help="only probe the first N anchors (faster)")
    ap.add_argument("--show", type=int, default=12,
                    help="how many example fingerprints to print")
    args = ap.parse_args()

    exe = args.exe
    if exe is None:
        # Prefer a next-gen binary: it is the near-neighbour of the build the
        # offsets came from, so enclosing-function anchoring is meaningful.
        for cand in find_candidates():
            if "downgradeBackup" in cand.name:
                exe = cand
                break
    if exe is None or not exe.exists():
        print("pass a next-gen Fallout4.exe path", file=sys.stderr)
        return 2

    entries = [e for e in parse_offsets(OFFSETS_H) if e.kind == Kind.CODE]
    entries += parse_inline_rvas(SRC_ROOT, {e.rva for e in entries})
    if args.sample:
        entries = entries[:args.sample]

    print(f"anchoring {len(entries)} code addresses against {exe.name}\n")
    img = Image(exe)
    print(f"  {len(img.functions())} functions in the exception directory\n")

    anchored = 0
    unanchored = 0
    with_strings = 0
    with_consts = 0
    distinctive = 0
    featureless: list[str] = []
    examples: list[tuple[str, object]] = []
    string_counts = Counter()

    for e in entries:
        rng = img.enclosing_function(e.rva)
        if rng is None:
            unanchored += 1
            continue
        anchored += 1
        fp = img.fingerprint(*rng)
        if fp.strings:
            with_strings += 1
            string_counts[len(fp.strings)] += 1
        if len(fp.consts) >= 2:
            with_consts += 1
        if fp.is_distinctive():
            distinctive += 1
            if len(examples) < args.show:
                examples.append((e.name, fp))
        else:
            if len(featureless) < 15:
                featureless.append(f"{e.name} @0x{e.rva:08X} "
                                   f"({fp.n_insns} insns, {fp.n_calls} calls)")

    img.close()

    total = anchored + unanchored
    print("--- anchoring ---")
    print(f"  resolved to an enclosing function : {anchored}/{total}")
    print(f"  no enclosing function             : {unanchored}")

    print("\n--- signal available for matching ---")
    if anchored:
        print(f"  reference >=1 string literal      : {with_strings:>4}"
              f"  ({100.0*with_strings/anchored:4.1f}%)")
        print(f"  have >=2 distinctive constants    : {with_consts:>4}"
              f"  ({100.0*with_consts/anchored:4.1f}%)")
        print(f"  DISTINCTIVE (either of the above) : {distinctive:>4}"
              f"  ({100.0*distinctive/anchored:4.1f}%)")
        print(f"  featureless                       : "
              f"{anchored - distinctive:>4}"
              f"  ({100.0*(anchored-distinctive)/anchored:4.1f}%)")

    if examples:
        print("\n--- example fingerprints ---")
        for name, fp in examples:
            strs = sorted(fp.strings)[:3]
            shown = ", ".join(repr(s[:44]) for s in strs)
            print(f"  {name[:52]:<52} {fp.n_insns:>5} insns")
            if shown:
                print(f"      strings: {shown}")
            elif fp.consts:
                cs = ", ".join(f"0x{c:X}" for c in sorted(fp.consts)[:5])
                print(f"      consts : {cs}")

    if featureless:
        print("\n--- featureless (manual RE needed) ---")
        for f in featureless:
            print(f"  {f}")

    ratio = (distinctive / anchored) if anchored else 0.0
    print("\n--- verdict ---")
    if ratio >= 0.60:
        print("  Structural matching looks WORTH BUILDING: a clear majority of")
        print("  anchors carry a signal that survives a recompile.")
    elif ratio >= 0.30:
        print("  Structural matching is PARTIALLY viable. Expect it to recover")
        print("  a useful fraction automatically and leave a manual tail.")
    else:
        print("  Structural matching is WEAK here. Most anchors carry nothing")
        print("  distinctive, so a matcher would guess. Prefer manual RE, or a")
        print("  reference build that permits a direct diff.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
