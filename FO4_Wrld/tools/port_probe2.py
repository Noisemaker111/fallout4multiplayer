"""Second-stage probe: does call-graph propagation rescue the featureless?

port_probe.py measured only *seed* signal — strings and constants inside the
function itself — and found ~22%. That understates what a real matcher can do.
Binary diffing (BinDiff and friends) works by seeding on high-confidence
matches and then propagating through the call graph: a function with no
internal signal is still pinned if it is "the second function called by an
already-matched function".

This measures the propagation potential:

  depth 0  the anchor itself is distinctive (strings / constants)
  depth 1  it directly calls, or is directly called by, a distinctive function
  depth 2  same, one hop further out

An anchor reachable at depth 1-2 from a distinctive neighbour is very likely
recoverable automatically. One reachable at no depth is genuinely manual RE.

Usage
-----
    python tools/port_probe2.py [exe]
"""

from __future__ import annotations

import argparse
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

SRC_ROOT = Path(__file__).resolve().parent.parent / "fw_native" / "src"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", nargs="?", type=Path)
    args = ap.parse_args()

    exe = args.exe
    if exe is None:
        for cand in find_candidates():
            if "downgradeBackup" in cand.name:
                exe = cand
                break
    if exe is None or not exe.exists():
        print("pass a next-gen Fallout4.exe path", file=sys.stderr)
        return 2

    entries = [e for e in parse_offsets(OFFSETS_H) if e.kind == Kind.CODE]
    entries += parse_inline_rvas(SRC_ROOT, {e.rva for e in entries})

    img = Image(exe)
    print(f"probing {len(entries)} addresses against {exe.name}")
    print(f"  {len(img.functions())} functions indexed\n")

    # Anchor each pinned address to its enclosing function.
    anchors: dict[str, int] = {}
    for e in entries:
        rng = img.enclosing_function(e.rva)
        if rng:
            anchors[e.name] = rng[0]

    # Fingerprint the anchors plus one hop of callees, caching as we go.
    fps: dict[int, object] = {}

    def fp_of(rva: int):
        if rva not in fps:
            rng = img.enclosing_function(rva)
            fps[rva] = img.fingerprint(*rng) if rng else None
        return fps[rva]

    print("fingerprinting anchors and their neighbourhoods "
          "(this takes a minute)...")

    distinctive: set[int] = set()
    callees: dict[int, set[int]] = {}
    for rva in set(anchors.values()):
        fp = fp_of(rva)
        if fp is None:
            continue
        callees[rva] = set(fp.callees)
        if fp.is_distinctive():
            distinctive.add(rva)

    # One hop out: fingerprint the callees so we know which of them are
    # distinctive, and build the reverse edges for "called by" propagation.
    callers: dict[int, set[int]] = defaultdict(set)
    for rva, outs in list(callees.items()):
        for t in outs:
            callers[t].add(rva)
            fp = fp_of(t)
            if fp is not None and fp.is_distinctive():
                distinctive.add(t)

    # Classify each anchor by how far it sits from any distinctive function.
    depth_counts = {0: 0, 1: 0, 2: 0}
    unreachable: list[str] = []

    for name, rva in anchors.items():
        if rva in distinctive:
            depth_counts[0] += 1
            continue
        one_hop = callees.get(rva, set()) | callers.get(rva, set())
        if one_hop & distinctive:
            depth_counts[1] += 1
            continue
        two_hop: set[int] = set()
        for n in one_hop:
            two_hop |= callees.get(n, set()) or set(
                (fp_of(n).callees if fp_of(n) else set()))
            two_hop |= callers.get(n, set())
        if two_hop & distinctive:
            depth_counts[2] += 1
            continue
        unreachable.append(name)

    img.close()

    total = len(anchors)
    reachable = depth_counts[0] + depth_counts[1] + depth_counts[2]

    print(f"\n--- propagation reach ({total} anchored addresses) ---")
    print(f"  depth 0  distinctive itself        : {depth_counts[0]:>4}"
          f"  ({100.0*depth_counts[0]/total:4.1f}%)")
    print(f"  depth 1  neighbour is distinctive  : {depth_counts[1]:>4}"
          f"  ({100.0*depth_counts[1]/total:4.1f}%)")
    print(f"  depth 2  two hops out              : {depth_counts[2]:>4}"
          f"  ({100.0*depth_counts[2]/total:4.1f}%)")
    print(f"  ---")
    print(f"  REACHABLE (automatable candidate)  : {reachable:>4}"
          f"  ({100.0*reachable/total:4.1f}%)")
    print(f"  unreachable (manual RE)            : {len(unreachable):>4}"
          f"  ({100.0*len(unreachable)/total:4.1f}%)")

    if unreachable:
        print("\n--- unreachable sample ---")
        for n in unreachable[:20]:
            print(f"  {n}")

    ratio = reachable / total if total else 0.0
    print("\n--- verdict ---")
    if ratio >= 0.75:
        print("  Call-graph propagation makes this MOSTLY AUTOMATABLE.")
        print("  Build the matcher: seed on distinctive functions + Address")
        print("  Library entries, then propagate. Verify every result.")
    elif ratio >= 0.50:
        print("  Propagation helps substantially. A matcher plus a manual tail")
        print("  is the right shape.")
    else:
        print("  Propagation does not rescue enough. Manual RE dominates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
