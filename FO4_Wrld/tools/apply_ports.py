"""Apply ported RVAs from the work-list into fw_native sources, PE-verified.

Reads docs/port_worklist*.csv rows that have `ported_addr` set, checks each
candidate against the real 1.10.163 binary:

  * code RVAs must be a RUNTIME_FUNCTION start (or mid-range is rejected)
  * pointer/data RVAs must land in a non-executable section and be 8-aligned
    when the name says VTABLE/SINGLETON

Then rewrites matching `constexpr std::uintptr_t NAME = 0x....;` lines in
offsets.h (and optional other headers that pin the same names).

Usage:
    python tools/apply_ports.py \\
        --exe "C:/Games/Steam/steamapps/common/Fallout 4/Fallout4.exe.unpacked.exe" \\
        --worklist docs/port_worklist_minimal.csv \\
        --dry-run
    python tools/apply_ports.py --exe ... --worklist ... --write
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pefile

REPO = Path(__file__).resolve().parent.parent
OFFSETS_H = REPO / "fw_native" / "src" / "offsets.h"
IMAGE_BASE = 0x140000000

_CONST = re.compile(
    r"(constexpr\s+std::uintptr_t\s+)(\w+)(\s*=\s*)(0x[0-9A-Fa-f]+)(\s*;)"
)


def load_function_starts(pe: pefile.PE) -> set[int]:
    starts: set[int] = set()
    directory = getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", None)
    if not directory:
        return starts
    for fn in directory:
        begin = getattr(fn.struct, "BeginAddress", 0)
        # Skip UNW_FLAG_CHAININFO continuations — BeginAddress is mid-function.
        unwind = getattr(fn.struct, "UnwindData", 0)
        # Without parsing the unwind info flag we still filter unaligned starts.
        if begin and (begin & 0xF) == 0:
            starts.add(begin)
    return starts


def section_for(pe: pefile.PE, rva: int) -> tuple[str, bool] | None:
    """Return (section_name, is_executable) or None."""
    for s in pe.sections:
        va = s.VirtualAddress
        size = max(s.Misc_VirtualSize, s.SizeOfRawData)
        if va <= rva < va + size:
            name = s.Name.rstrip(b"\x00").decode(errors="replace")
            exec_ = bool(s.Characteristics & 0x20000000)
            return name, exec_
    return None


def classify_name(name: str) -> str:
    u = name.upper()
    if any(k in u for k in ("VTABLE", "SINGLETON", "_PTR", "INSTANCE", "DESC")):
        return "pointer"
    if "FLAG" in u or "SLOT" in u:
        return "data"
    return "code"


def load_ports(worklist: Path) -> dict[str, tuple[int, str]]:
    """name -> (ported_rva, verified_tag). Only simple *_RVA constants.

    FIELD offsets are struct member displacements, not image RVAs — they
    must never be PE-checked or rewritten as if they were addresses.
    """
    out: dict[str, tuple[int, str]] = {}
    with worklist.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = row.get("name") or ""
            tier = (row.get("tier") or "").upper()
            addr = (row.get("ported_addr") or "").strip()
            if not name or not addr or ":" in name:
                continue  # skip path:sub_14... inline rows
            if not re.fullmatch(r"\w+", name):
                continue
            if tier == "FIELD" or not name.endswith("_RVA"):
                continue
            try:
                rva = int(addr, 16)
            except ValueError:
                continue
            if rva >= IMAGE_BASE:
                rva -= IMAGE_BASE
            out[name] = (rva, (row.get("verified") or "").strip())
    return out


def verify(
    pe: pefile.PE, starts: set[int], name: str, rva: int
) -> tuple[bool, str]:
    kind = classify_name(name)
    sec = section_for(pe, rva)
    if sec is None:
        return False, "RVA outside any section"
    sname, is_exec = sec
    if kind == "code":
        if not is_exec:
            return False, f"code candidate in non-exec {sname}"
        if rva in starts:
            return True, f"function start in {sname}"
        return False, f"not a function start (in {sname})"
    # pointer / data
    if is_exec:
        return False, f"data candidate in executable {sname}"
    if kind == "pointer" and (rva & 7) != 0:
        return False, f"pointer not 8-aligned in {sname}"
    return True, f"data in {sname}"


def apply_to_file(
    path: Path, ports: dict[str, int], dry_run: bool
) -> list[tuple[str, int, int]]:
    """Rewrite matching constexpr RVAs. Returns list of (name, old, new)."""
    text = path.read_text(encoding="utf-8")
    changes: list[tuple[str, int, int]] = []

    def repl(m: re.Match) -> str:
        name = m.group(2)
        if name not in ports:
            return m.group(0)
        old = int(m.group(4), 16)
        new = ports[name]
        if old == new:
            return m.group(0)
        changes.append((name, old, new))
        return f"{m.group(1)}{name}{m.group(3)}0x{new:08X}{m.group(5)}"

    new_text = _CONST.sub(repl, text)
    if changes and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--worklist", type=Path, required=True)
    ap.add_argument("--write", action="store_true", help="actually rewrite files")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument(
        "--files",
        nargs="*",
        default=[str(OFFSETS_H)],
        help="source files to rewrite",
    )
    args = ap.parse_args()
    dry = not args.write

    pe = pefile.PE(str(args.exe), fast_load=False)
    starts = load_function_starts(pe)
    print(f"exe: {args.exe}")
    print(f"  function starts (aligned): {len(starts)}")

    ports = load_ports(args.worklist)
    print(f"worklist candidates: {len(ports)}\n")

    accepted: dict[str, int] = {}
    rejected: list[tuple[str, int, str]] = []
    for name, (rva, tag) in sorted(ports.items()):
        ok, detail = verify(pe, starts, name, rva)
        mark = "OK  " if ok else "FAIL"
        print(f"  {mark}  {name:<36} 0x{rva:08X}  [{tag or '-'}]  {detail}")
        if ok:
            accepted[name] = rva
        else:
            rejected.append((name, rva, detail))

    print(f"\n  accepted {len(accepted)} / rejected {len(rejected)}")

    all_changes: list[tuple[str, Path, int, int]] = []
    for f in args.files:
        path = Path(f)
        if not path.is_file():
            path = REPO / f
        ch = apply_to_file(path, accepted, dry_run=dry)
        for name, old, new in ch:
            all_changes.append((name, path, old, new))
            action = "would write" if dry else "wrote"
            print(f"  {action} {path.name}: {name}  0x{old:08X} -> 0x{new:08X}")

    if dry:
        print("\n(dry-run; pass --write to apply)")
    else:
        print(f"\napplied {len(all_changes)} rewrites")
    return 0 if not rejected or accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
