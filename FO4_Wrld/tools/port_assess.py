"""Classify every version-pinned constant by how hard it is to port.

Retargeting the native client to a different Fallout 4 build means re-deriving
every hardcoded address. "383 addresses" is a scary number that hides a very
uneven distribution: some are free, some are a lookup, and some are original
reverse-engineering that exists nowhere else. This tool splits them so the work
can be planned instead of guessed at.

Tiers
-----
  FIELD    struct field offsets (`TESForm::formID` at +0x14, etc.)
           Recoverable from public sources for 1.10.163 — F4SE and
           CommonLibF4 publish these layouts. Cheap; often unchanged.

  KNOWN    engine APIs with public equivalents: Papyrus natives, form lookup,
           PlaceAtMe, the memory allocator, vtables. For 1.10.163 these are in
           the F4SE Address Library. A lookup, not an investigation.

  NOVEL    the project's own reverse engineering: skin pipeline, combat
           orchestration, the HP funnel, ghost hostility guards, walker
           guards. No public source exists at any version. Each one has to be
           re-derived by hand in IDA against the target build.

The NOVEL count is the real cost of a port. Everything else is bookkeeping.

Scope
-----
By default every constant in the tree is counted. `--minimal` counts only what
an `FW_MINIMAL` build actually compiles: the modules listed in
`fw_native/minimal_exclude.txt` are dropped, and so are the offsets.h constants
that nothing outside them still references. That is the number that matters for
a first playable build, because an address in a module you don't compile is an
address you don't have to re-derive.

Usage
-----
    python tools/port_assess.py                 # summary
    python tools/port_assess.py --list NOVEL    # the actual work-list
    python tools/port_assess.py --csv out.csv   # for tracking progress
    python tools/port_assess.py --minimal       # FW_MINIMAL scope only
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from offset_audit import (  # noqa: E402
    OFFSETS_H,
    Kind,
    parse_inline_rvas,
    parse_offsets,
    strip_non_minimal_regions,
    use_utf8_stdout,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "fw_native" / "src"
MINIMAL_EXCLUDE = REPO_ROOT / "fw_native" / "minimal_exclude.txt"

# `constexpr std::size_t NAME = 0x14;  // comment`
_FIELD_DECL = re.compile(
    r"constexpr\s+std::size_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;(.*)$"
)


class Tier:
    FIELD = "FIELD"
    KNOWN = "KNOWN"
    NOVEL = "NOVEL"


# Substrings that mark an address as a publicly-documented engine API. Matched
# against the constant name and its comment, case-insensitively.
KNOWN_MARKERS = (
    "papyrus", "lookup_by_formid", "lookupbyformid", "place_at_me", "placeatme",
    "memmgr", "heap_alloc", "engine_heap", "malloc", "free",
    "vtable", "singleton", "bsfixedstring", "formid", "form_cache",
    "getcomponent", "get_component", "inv_list", "invlist", "inventory",
    "add_item", "remove_item", "equip_object", "unequip_object",
    "set_open_state", "force_unlock", "force_lock", "lock_data",
    "load_game", "save", "main_menu", "console",
    "set_graph_var", "update_downward", "niav", "nif_loader",
    "actor_equip_mgr", "refhandle", "handle_get_or_alloc",
)

# Substrings that mark the project's own RE. These win over KNOWN_MARKERS
# because several novel functions mention a known type in passing.
NOVEL_MARKERS = (
    "combat", "aggro", "hostil", "aim", "fire", "attack", "hit_applier",
    "threat", "ai_", "aiproc", "package", "perception", "los_", "sight",
    "skin", "bones", "bone_", "bsskin", "bssub", "bstri", "bsgeo",
    "puppet", "ghost", "proxy", "walker", "guard", "orchestr",
    "hp_funnel", "hp_bar", "health", "kill", "death", "ragdoll",
    "havok", "bhk", "char_rigid", "movement_controller", "tickmovement",
    "cross_cell", "cell_loaded", "alive_count", "post_pick",
    "event_dispatch", "extradata", "graphmgr", "anim", "foot_ik",
    "clone_factory", "omod", "objinstance", "bipedanim", "arma",
    "combatctrl", "selector", "promoter",
)


@dataclass
class Item:
    name: str
    value: str
    comment: str
    tier: str
    source: str      # "offsets.h" or a src-relative path


def classify_tier(name: str, comment: str, is_field: bool) -> str:
    if is_field:
        return Tier.FIELD
    blob = f"{name} {comment}".lower()
    # Novel wins ties: a combat-controller helper that happens to mention a
    # vtable is still original RE, not a public lookup.
    if any(m in blob for m in NOVEL_MARKERS):
        return Tier.NOVEL
    if any(m in blob for m in KNOWN_MARKERS):
        return Tier.KNOWN
    return Tier.NOVEL


def load_minimal_excluded_files() -> set[str]:
    """src-relative .cpp/.h paths that an FW_MINIMAL build leaves out.

    Reads the same list CMake reads, so "minimal" means one thing in the build
    and in the work-list. A stem covers both translation unit and header: the
    header matters here because a good half of the pinned addresses in this
    tree live in `sub_14XXXXXXX` references inside header comments.
    """
    excluded: set[str] = set()
    for raw in MINIMAL_EXCLUDE.read_text(encoding="utf-8").splitlines():
        stem = raw.strip()
        if not stem or stem.startswith("#"):
            continue
        excluded.add(f"{stem}.cpp")
        excluded.add(f"{stem}.h")
    return excluded


def names_referenced_outside(excluded: set[str], names: set[str]) -> set[str]:
    """Which offsets.h constants are still referenced by a file that survives.

    offsets.h is one flat table shared by everything, so a constant only leaves
    the port work-list when every file that mentions it is compiled out.
    """
    if not names:
        return set()
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in
                                           sorted(names, key=len, reverse=True)) + r")\b")
    still_used: set[str] = set()
    for path in sorted(SRC_ROOT.rglob("*")):
        if path.suffix not in (".cpp", ".h", ".hpp"):
            continue
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel == "offsets.h" or rel in excluded:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # A constant referenced only from inside `#if !FW_MINIMAL` is not
        # compiled either, so it must not keep itself alive in the work-list.
        still_used.update(pattern.findall(strip_non_minimal_regions(text)))
    return still_used


def parse_field_offsets(path: Path) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        m = _FIELD_DECL.search(stripped)
        if not m:
            continue
        name, value, comment = m.group(1), m.group(2), m.group(3)
        if name in seen:
            continue
        seen.add(name)
        items.append(Item(name, value, comment.strip(),
                          Tier.FIELD, "offsets.h"))
    return items


def collect(minimal: bool = False) -> list[Item]:
    items: list[Item] = []
    excluded = load_minimal_excluded_files() if minimal else set()

    # Struct field offsets. Filtered exactly like the RVA constants below: a
    # field offset is only work if something still compiled reads it. Most of
    # this tier turns out to be AIProcess / CombatController / aim-controller
    # internals, which go away with the combat layer.
    fields = parse_field_offsets(OFFSETS_H)
    if minimal:
        live_fields = names_referenced_outside(excluded, {i.name for i in fields})
        fields = [i for i in fields if i.name in live_fields]
    items.extend(fields)

    # Named RVA constants.
    rvas = parse_offsets(OFFSETS_H)
    if minimal:
        live = names_referenced_outside(excluded, {e.name for e in rvas})
        rvas = [e for e in rvas if e.name in live]
    for e in rvas:
        items.append(Item(
            name=e.name,
            value=f"0x{e.rva:08X}",
            comment=e.comment,
            tier=classify_tier(e.name, e.comment, is_field=False),
            source="offsets.h",
        ))

    # Addresses hardcoded outside the table.
    #
    # `known` is deliberately built from the FULL table, not the filtered one:
    # an inline `sub_14XXXXXXX` that duplicates a table entry is the same
    # address either way, and counting it twice would inflate the minimal
    # work-list with addresses already tracked as constants.
    inline = parse_inline_rvas(SRC_ROOT, {e.rva for e in parse_offsets(OFFSETS_H)},
                               skip=excluded, minimal=minimal)
    for e in inline:
        # e.name is "path:sub_14XXXXXXX" — the file it lives in is the best
        # available hint about what subsystem it belongs to.
        path_hint = e.name.split(":", 1)[0]
        items.append(Item(
            name=e.name,
            value=f"0x{e.rva:08X}",
            comment=path_hint,
            tier=classify_tier(path_hint, "", is_field=False),
            source=path_hint,
        ))

    return items


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", choices=[Tier.FIELD, Tier.KNOWN, Tier.NOVEL],
                    help="print every item in one tier")
    ap.add_argument("--csv", type=Path, help="write the full work-list to CSV")
    ap.add_argument("--minimal", action="store_true",
                    help="count only what an FW_MINIMAL build compiles "
                         "(see fw_native/minimal_exclude.txt)")
    args = ap.parse_args()

    items = collect(minimal=args.minimal)
    counts = Counter(i.tier for i in items)
    total = len(items)

    if args.minimal:
        full_total = len(collect())
        print(f"scope: FW_MINIMAL "
              f"({total} of {full_total} constants; "
              f"{full_total - total} dropped with the excluded modules)\n")
    print(f"{total} version-pinned constants\n")
    for tier, blurb in (
        (Tier.FIELD, "struct layout      — public for 1.10.163 (F4SE/CommonLibF4)"),
        (Tier.KNOWN, "known engine API   — Address Library lookup"),
        (Tier.NOVEL, "original RE        — must be re-derived in IDA"),
    ):
        n = counts.get(tier, 0)
        pct = (100.0 * n / total) if total else 0.0
        print(f"  {tier:<6} {n:>4}  ({pct:4.1f}%)  {blurb}")

    novel = counts.get(Tier.NOVEL, 0)
    print(f"\nThe port cost is essentially the {novel} NOVEL entries.")
    print("FIELD and KNOWN are lookups; NOVEL is the actual reverse engineering.")

    # Which subsystems carry the novel work — this is what decides whether a
    # port is scoped as "a few weeks" or "redo the hard half of the project".
    by_area = Counter()
    for i in items:
        if i.tier != Tier.NOVEL:
            continue
        area = i.source
        if area.startswith("hooks/"):
            area = "hooks/"
        elif area.startswith("native/"):
            area = "native/ (scene graph + skin)"
        elif area.startswith("engine/"):
            area = "engine/"
        by_area[area] += 1
    if by_area:
        print("\nNOVEL entries by area:")
        for area, n in by_area.most_common():
            print(f"  {n:>4}  {area}")

    if args.list:
        print(f"\n--- {args.list} ---")
        for i in items:
            if i.tier == args.list:
                note = i.comment[:70]
                print(f"  {i.value:<12} {i.name:<44} {note}")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["tier", "value", "name", "source", "comment",
                        "ported_addr", "verified"])
            for i in items:
                w.writerow([i.tier, i.value, i.name, i.source, i.comment,
                            "", ""])
        print(f"\nwrote {args.csv} "
              f"({total} rows; fill ported_addr/verified as you go)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
