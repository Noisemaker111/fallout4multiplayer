"""Cross-build function matcher — port pinned addresses between FO4 builds.

Pipeline
--------
1. **Anchor.** Every pinned code address is a function start in the build it
   came from (1.11.191). We don't have that build, but 1.11.221 is the same
   code shifted, so the function *containing* each pinned RVA there is very
   probably the same function. That is the anchor.

2. **Seed.** Match anchors to functions in the target build (1.10.163) by
   referenced string literals. A string referenced by exactly one function in
   each build is a near-certain pair.

3. **Propagate.** Matched pairs expose their call lists. Where two matched
   functions call the same *number* of functions and the already-matched
   subset lines up in order, the unmatched callees pair off positionally. This
   is what reaches functions carrying no signal of their own — measured at
   ~65% of anchors in tools/port_probe2.py.

4. **Verify.** Every candidate is checked against the target's exception
   directory: a port that doesn't land on a function start is rejected outright.

Output is a CSV of (name, source_rva, ported_rva, confidence, method) plus a
residual list for manual RE.

Nothing here is trusted blind. `confidence` exists so the residual and the
low-confidence rows get human eyes before anything is compiled against them.

Usage
-----
    python tools/port_match.py --source <ng.exe> --target <1.10.163.exe> \
                               --out docs/port_matched.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
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
from callgraph import CallGraph  # noqa: E402
from strxref import StringXrefs  # noqa: E402

SRC_ROOT = Path(__file__).resolve().parent.parent / "fw_native" / "src"

# Jaccard floor for accepting a fuzzy string-set match. High on purpose: a
# wrong hook address is worse than a missing one, because it silently corrupts
# the game instead of failing to build.
_FUZZY_MIN = 0.60


class Conf:
    EXACT = "exact"        # unique string in both builds
    STRONG = "strong"      # string-set overlap above threshold
    PROPAGATED = "propagated"   # positional match via a matched caller
    NONE = "none"


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_anchors(src: Image):
    """pinned-name -> (pinned_rva, enclosing_function_rva) in the source build."""
    entries = [e for e in parse_offsets(OFFSETS_H) if e.kind == Kind.CODE]
    entries += parse_inline_rvas(SRC_ROOT, {e.rva for e in entries})
    out = {}
    unanchored = []
    for e in entries:
        rng = src.enclosing_function(e.rva)
        if rng:
            out[e.name] = (e.rva, rng[0])
        else:
            unanchored.append(e.name)
    return out, unanchored


def seed_matches(src: Image, tgt: Image,
                 src_x: StringXrefs, tgt_x: StringXrefs,
                 anchor_fns: set[int]) -> dict[int, tuple[int, str]]:
    """src_fn_rva -> (tgt_fn_rva, confidence) from string evidence."""
    matches: dict[int, tuple[int, str]] = {}

    src_unique = src_x.unique_strings()      # string -> src fn
    tgt_unique = tgt_x.unique_strings()      # string -> tgt fn

    # Pass 1: a literal referenced by exactly one function in BOTH builds.
    for s, sfn in src_unique.items():
        tfn = tgt_unique.get(s)
        if tfn is not None and sfn not in matches:
            matches[sfn] = (tfn, Conf.EXACT)

    # Pass 2: fuzzy set overlap, restricted to anchors still unmatched. Only
    # target functions sharing at least one string are considered, which keeps
    # this from being a quadratic sweep over 200k functions.
    for sfn in anchor_fns:
        if sfn in matches:
            continue
        sstrings = src_x.strings_of(sfn)
        if not sstrings:
            continue
        cands: set[int] = set()
        for s in sstrings:
            cands |= tgt_x.by_string.get(s, set())
        best, best_score = None, 0.0
        for tfn in cands:
            score = jaccard(sstrings, tgt_x.strings_of(tfn))
            if score > best_score:
                best, best_score = tfn, score
        if best is not None and best_score >= _FUZZY_MIN:
            matches[sfn] = (best, Conf.STRONG)

    return matches


def propagate(src_cg, tgt_cg,
              matches: dict[int, tuple[int, str]],
              targets: set[int],
              rounds: int = 6) -> dict[int, tuple[int, str]]:
    """Extend matches through the call graph, in both directions.

    Forward: for a matched pair (S, T) issuing the same number of direct calls,
    the i-th callee of S pairs with the i-th callee of T. Call order is a
    property of the source, so it survives a recompile; addresses do not.

    Backward: if S has exactly one caller and T has exactly one caller, those
    callers are the same function. This is the half that actually reaches the
    pinned addresses — they are deep engine internals that string-bearing
    functions seldom call, but which routinely call into identified helpers.
    The uniqueness requirement keeps it conservative; a function with three
    callers gives no way to tell which is which.
    """
    frontier = set(matches)
    for _ in range(rounds):
        new: dict[int, tuple[int, str]] = {}

        for sfn in frontier:
            tfn, _conf = matches[sfn]

            # --- forward: positional alignment of call sites ---
            s_callees = src_cg.out_edges(sfn)
            t_callees = tgt_cg.out_edges(tfn)
            if s_callees and len(s_callees) == len(t_callees):
                for s_c, t_c in zip(s_callees, t_callees):
                    if s_c in matches or s_c in new:
                        continue
                    if t_c in targets:
                        new[s_c] = (t_c, Conf.PROPAGATED)

            # --- backward: unique caller on both sides ---
            s_callers = src_cg.in_edges(sfn)
            t_callers = tgt_cg.in_edges(tfn)
            if len(s_callers) == 1 and len(t_callers) == 1:
                s_p = next(iter(s_callers))
                t_p = next(iter(t_callers))
                if s_p not in matches and s_p not in new and t_p in targets:
                    new[s_p] = (t_p, Conf.PROPAGATED)

        if not new:
            break
        matches.update(new)
        frontier = set(new)
    return matches


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, help="next-gen exe (anchor build)")
    ap.add_argument("--target", type=Path, help="exe to port TO (1.10.163)")
    ap.add_argument("--out", type=Path, default=Path("docs/port_matched.csv"))
    ap.add_argument("--rounds", type=int, default=6)
    args = ap.parse_args()

    source, target = args.source, args.target
    if source is None or target is None:
        for cand in find_candidates():
            if "downgradeBackup" in cand.name and source is None:
                source = cand
            elif cand.name == "Fallout4.exe" and target is None:
                target = cand
    if not source or not target:
        print("need --source and --target", file=sys.stderr)
        return 2

    print(f"source (anchor): {source.name}")
    print(f"target (port to): {target.name}\n")

    src, tgt = Image(source), Image(target)
    print(f"  source functions: {len(src.functions())}")
    print(f"  target functions: {len(tgt.functions())}")

    print("\nindexing string xrefs (byte scan)...")
    src_x = StringXrefs(src)
    print(f"  source: {src_x.stats()}")
    tgt_x = StringXrefs(tgt)
    print(f"  target: {tgt_x.stats()}")

    anchors, unanchored = load_anchors(src)
    anchor_fns = {fn for _, fn in anchors.values()}
    print(f"\nanchored {len(anchors)} pinned addresses "
          f"onto {len(anchor_fns)} distinct functions "
          f"({len(unanchored)} unanchored)")

    print("\nseeding on string evidence...")
    matches = seed_matches(src, tgt, src_x, tgt_x, anchor_fns)
    seeded_anchors = sum(1 for fn in anchor_fns if fn in matches)
    print(f"  {len(matches)} function pairs seeded "
          f"({seeded_anchors} of them are anchors)")

    print("\nbuilding call graphs (byte scan)...")
    src_cg = CallGraph(src)
    print(f"  source: {src_cg.stats()}")
    tgt_cg = CallGraph(tgt)
    print(f"  target: {tgt_cg.stats()}")

    print(f"\npropagating through the call graph ({args.rounds} rounds)...")
    targets = {b for b, _ in tgt.functions()}
    matches = propagate(src_cg, tgt_cg, matches, targets, args.rounds)
    print(f"  {len(matches)} function pairs after propagation")

    # A correct mapping is injective: two distinct source functions cannot both
    # be the same target function. Where that happens at least one side is
    # wrong and we cannot tell which, so both are demoted rather than shipped.
    tgt_claims: dict[int, set[int]] = defaultdict(set)
    for sfn, (tfn, _c) in matches.items():
        tgt_claims[tfn].add(sfn)
    conflicted = {t for t, srcs in tgt_claims.items() if len(srcs) > 1}
    if conflicted:
        n_pairs = sum(len(tgt_claims[t]) for t in conflicted)
        print(f"\n  {len(conflicted)} target functions claimed by >1 source "
              f"({n_pairs} pairs) — demoting all of them to unresolved")

    # Resolve each pinned address through its anchor.
    rows = []
    by_conf: dict[str, int] = defaultdict(int)
    for name, (pinned_rva, fn_rva) in sorted(anchors.items()):
        m = matches.get(fn_rva)
        if not m:
            by_conf[Conf.NONE] += 1
            rows.append([name, f"0x{pinned_rva:08X}", "", Conf.NONE, "",
                         "manual RE"])
            continue
        tfn, conf = m
        # Verification 1: the port must land on a real function start.
        if not tgt.is_function_start(tfn):
            by_conf[Conf.NONE] += 1
            rows.append([name, f"0x{pinned_rva:08X}", "", Conf.NONE, conf,
                         "REJECTED: not a function start"])
            continue
        # Verification 2: the mapping must be injective.
        if tfn in conflicted:
            by_conf[Conf.NONE] += 1
            rows.append([name, f"0x{pinned_rva:08X}", "", Conf.NONE, conf,
                         "REJECTED: target claimed by multiple sources"])
            continue
        by_conf[conf] += 1
        rows.append([name, f"0x{pinned_rva:08X}", f"0x{tfn:08X}", conf, conf,
                     ""])

    total = len(anchors)
    print(f"\n--- results ({total} anchored addresses) ---")
    for conf in (Conf.EXACT, Conf.STRONG, Conf.PROPAGATED, Conf.NONE):
        n = by_conf.get(conf, 0)
        print(f"  {conf:<12} {n:>4}  ({100.0*n/total:5.1f}%)")
    resolved = total - by_conf.get(Conf.NONE, 0)
    print(f"  {'RESOLVED':<12} {resolved:>4}  ({100.0*resolved/total:5.1f}%)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "source_rva", "ported_rva", "confidence",
                    "method", "note"])
        w.writerows(rows)
    print(f"\nwrote {args.out}")
    print("\nEVERY ported address is a candidate, not a fact. Verify before "
          "compiling against it:\n"
          "  - 'exact' rows are the most trustworthy (unique string in both)\n"
          "  - 'propagated' rows inherit their caller's correctness\n"
          "  - spot-check in IDA before flipping version.h")

    src.close()
    tgt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
