"""Audit the hardcoded RVA table in fw_native/src/offsets.h against a real
Fallout4.exe.

The native client pins ~200 RVAs to Fallout4.exe 1.11.191. Those constants are
only meaningful for the exact build they were derived from: a different patch
level shifts code and the hooks land mid-instruction. This tool answers, for a
given binary, "how many of these RVAs still look like what offsets.h claims
they are?" — without needing IDA.

Method
------
Every RVA is classified by the section it lands in and then checked against the
expectation implied by its name and comment:

  * code RVAs (.text) should sit at a *function start*. On MSVC x64 every
    function that can unwind has an entry in the PE exception directory
    (.pdata / RUNTIME_FUNCTION), so "is this RVA a function start?" is an exact
    lookup, not a heuristic. That is the strongest signal available and it is
    what makes this audit trustworthy.
  * data RVAs (.data / .rdata) should land in a data section, and pointer-sized
    slots (`qword_...`, vtables, singletons) should be 8-byte aligned.

A build where nearly every code RVA hits a RUNTIME_FUNCTION start is plausibly
the right build. A build where they land mid-function is definitively wrong,
and the DLL would corrupt the game the moment it installed a detour.

Usage
-----
    python tools/offset_audit.py <path-to-Fallout4.exe> [more.exe ...]
    python tools/offset_audit.py --auto        # probe known install locations

Exit code is 0 if at least one binary scores as a plausible match.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pefile
except ImportError:  # pragma: no cover - dependency guard
    sys.exit("pefile is required:  pip install pefile")

try:
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
except ImportError:  # pragma: no cover - dependency guard
    Cs = None


REPO_ROOT = Path(__file__).resolve().parent.parent
OFFSETS_H = REPO_ROOT / "fw_native" / "src" / "offsets.h"

# `constexpr std::uintptr_t NAME_RVA = 0x00C612E0;  // sub_140C612E0`
_RVA_DECL = re.compile(
    r"constexpr\s+std::uintptr_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)$"
)

# IDA-style auto-names that tell us what the target is supposed to be.
# The name embeds the absolute VA (0x1_4XXXXXXX); ImageBase is 0x140000000, so
# the RVA is the trailing 7 nibbles — drop the "14" prefix, not just the "1".
_IDA_FUNC = re.compile(r"\bsub_14([0-9A-Fa-f]{7})\b")
_IDA_QWORD = re.compile(r"\b(?:qword|off)_14([0-9A-Fa-f]{7})\b")
_IDA_BYTE = re.compile(r"\b(?:byte|dword|word)_14([0-9A-Fa-f]{7})\b")

IMAGE_BASE = 0x140000000


def use_utf8_stdout() -> None:
    """Stop a Windows cp1252 console from killing the run on one arrow glyph.

    offsets.h comments are full of `→`, `—` and the like. On a default Windows
    console `print` raises UnicodeEncodeError mid-report, which loses the whole
    audit over a decoration. Replace unencodable characters instead.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a real TTY / already wrapped
            pass


class Kind:
    CODE = "code"
    POINTER = "pointer"
    DATA = "data"


@dataclass
class Entry:
    name: str
    rva: int
    comment: str
    kind: str
    # what the IDA name in the comment implies the absolute VA should be,
    # used to catch typos where the constant and the comment disagree
    implied_rva: int | None = None


@dataclass
class Result:
    entry: Entry
    section: str | None = None
    ok: bool = False
    detail: str = ""


@dataclass
class Report:
    path: Path
    version: str = "?"
    results: list[Result] = field(default_factory=list)

    @property
    def code(self) -> list[Result]:
        return [r for r in self.results if r.entry.kind == Kind.CODE]

    @property
    def data(self) -> list[Result]:
        return [r for r in self.results if r.entry.kind != Kind.CODE]

    def score(self) -> float:
        """Fraction of *code* RVAs landing on a real function start.

        Code is the only category with a hard oracle, so the verdict keys off
        it. Data RVAs are reported but deliberately not scored: an 8-byte
        aligned address in .data is a weak signal that would inflate the score.
        """
        code = self.code
        if not code:
            return 0.0
        return sum(1 for r in code if r.ok) / len(code)


def classify(name: str, comment: str) -> tuple[str, int | None]:
    """Decide whether an RVA names code, a pointer slot, or plain data."""
    blob = f"{name} {comment}"

    # A comment of the form `// -> sub_141695CF0` names the *callee* of a thunk,
    # not the constant itself. Treating it as the implied address would flag a
    # correct entry as a typo, so only self-naming comments imply an address.
    names_callee = bool(re.search(r"(?:->|→|=>|calls?\b)\s*(?:sub_|qword_)",
                                  comment))

    m = _IDA_FUNC.search(blob)
    if m:
        return Kind.CODE, None if names_callee else int(m.group(1), 16)

    m = _IDA_QWORD.search(blob)
    if m:
        return Kind.POINTER, int(m.group(1), 16)

    m = _IDA_BYTE.search(blob)
    if m:
        return Kind.DATA, int(m.group(1), 16)

    # No IDA name to go on — fall back to the constant's own name. These
    # suffixes are the project's own vocabulary for pointer-sized slots.
    upper = name.upper()
    if any(k in upper for k in ("VTABLE", "VTBL", "SINGLETON", "_PTR", "INSTANCE")):
        return Kind.POINTER, None
    if upper.endswith("_FN_RVA") or "_FN_" in upper:
        return Kind.CODE, None
    # Anything else named *_RVA that isn't obviously data: treat as code. Every
    # such constant in offsets.h today is a detour or call target.
    if "FLAG" in upper or "SLOT" in upper:
        return Kind.DATA, None
    # Pool descriptors / handle table bases live in .data/.rdata, not .text.
    if any(k in upper for k in ("_DESC_RVA", "TABLE_BASE", "HEAP_DESC", "_BASE_RVA")):
        return Kind.POINTER, None
    return Kind.CODE, None


def strip_non_minimal_regions(text: str) -> str:
    """Blank out the code an `FW_MINIMAL` build does not compile.

    Once a module that a minimal build KEEPS starts guarding parts of itself
    with `#if !FW_MINIMAL`, file-level exclusion stops being enough — the
    addresses inside those guards are no longer compiled and must drop off the
    work-list. This walks the conditional nesting and drops exactly the
    FW_MINIMAL-conditioned branches that a minimal build skips.

    Only conditionals whose expression is literally FW_MINIMAL / !FW_MINIMAL
    are interpreted. Every other `#if` is left alone with both branches intact,
    which is what the scanner did before this existed — the goal is an accurate
    count, not a real preprocessor.

    Lines are blanked rather than deleted so reported line numbers stay honest.
    """
    if "FW_MINIMAL" not in text:
        return text

    if_re = re.compile(r"^\s*#\s*if\s+(!\s*)?FW_MINIMAL\s*$")
    any_if_re = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
    else_re = re.compile(r"^\s*#\s*else\b")
    elif_re = re.compile(r"^\s*#\s*elif\b")
    endif_re = re.compile(r"^\s*#\s*endif\b")

    out: list[str] = []
    # Stack of "is this branch compiled in a minimal build?" — None means the
    # conditional has nothing to do with FW_MINIMAL.
    stack: list[bool | None] = []

    for line in text.splitlines():
        m = if_re.match(line)
        if m:
            negated = m.group(1) is not None
            # `#if FW_MINIMAL` -> body IS compiled; `#if !FW_MINIMAL` -> is not.
            stack.append(not negated)
            out.append("")
            continue
        if any_if_re.match(line):
            stack.append(None)
            out.append("")
            continue
        if endif_re.match(line):
            if stack:
                stack.pop()
            out.append("")
            continue
        if else_re.match(line) and stack:
            top = stack[-1]
            stack[-1] = None if top is None else (not top)
            out.append("")
            continue
        if elif_re.match(line) and stack:
            # An `#elif` on an FW_MINIMAL conditional is not something this
            # codebase does; treat the rest as indeterminate rather than guess.
            stack[-1] = None
            out.append("")
            continue

        out.append("" if any(s is False for s in stack) else line)

    return "\n".join(out)


def parse_offsets(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        m = _RVA_DECL.search(stripped)
        if not m:
            continue
        name, value, comment = m.group(1), m.group(2), m.group(3)
        if name in seen:
            continue
        seen.add(name)
        rva = int(value, 16)
        # A few constants are written as absolute VAs; normalise to RVA.
        if rva >= IMAGE_BASE:
            rva -= IMAGE_BASE
        kind, implied = classify(name, comment)
        entries.append(Entry(name, rva, comment.strip(), kind, implied))
    return entries


def parse_inline_rvas(src_root: Path, known: set[int],
                      skip: set[str] | None = None,
                      minimal: bool = False) -> list[Entry]:
    """Find RVAs hardcoded in .cpp/.h files outside the offsets.h table.

    offsets.h is only part of the story: several hooks embed a literal
    `0x00CC9650` or reference `sub_140CC9650` in code. Those are just as
    version-pinned as the table, so a port has to cover them too. Anything
    already in the table is skipped so the count reflects genuinely extra work.

    `skip` is a set of src-relative paths ("hooks/ghost_ai_aim.cpp") to leave
    out entirely — used to score an FW_MINIMAL build. It has to filter BEFORE
    the dedup below, not after: each RVA is attributed to the first file that
    mentions it, so removing entries afterwards would silently drop addresses
    that a surviving file also uses.

    `minimal` additionally drops `#if !FW_MINIMAL` regions inside the files
    that a minimal build keeps. Pass it together with `skip`; on its own it
    would count modules that are excluded wholesale.
    """
    entries: list[Entry] = []
    seen: set[int] = set(known)
    skip = skip or set()

    for path in sorted(src_root.rglob("*")):
        if path.suffix not in (".cpp", ".h", ".hpp"):
            continue
        if path.name == "offsets.h":
            continue
        if path.relative_to(src_root).as_posix() in skip:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if minimal:
            text = strip_non_minimal_regions(text)
        rel = path.relative_to(src_root).as_posix()
        for m in _IDA_FUNC.finditer(text):
            rva = int(m.group(1), 16)
            if rva in seen:
                continue
            seen.add(rva)
            entries.append(
                Entry(f"{rel}:sub_14{m.group(1)}", rva, "inline", Kind.CODE)
            )
    return entries


def load_function_starts(pe: pefile.PE) -> set[int]:
    """Every RUNTIME_FUNCTION.BeginAddress in the PE exception directory.

    On x64 MSVC this is an exhaustive index of unwindable function starts, so
    membership is a precise test rather than a prologue guess.
    """
    starts: set[int] = set()
    directory = getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", None)
    if not directory:
        return starts
    for fn in directory:
        begin = getattr(fn.struct, "BeginAddress", 0)
        if begin:
            starts.add(begin)
    return starts


def section_of(pe: pefile.PE, rva: int) -> pefile.SectionStructure | None:
    for section in pe.sections:
        start = section.VirtualAddress
        size = max(section.Misc_VirtualSize, section.SizeOfRawData)
        if start <= rva < start + size:
            return section
    return None


def file_version(pe: pefile.PE) -> str:
    for entry in getattr(pe, "FileInfo", []) or []:
        for item in entry if isinstance(entry, list) else [entry]:
            if getattr(item, "Key", b"") == b"StringFileInfo":
                for st in item.StringTable:
                    v = st.entries.get(b"FileVersion")
                    if v:
                        return v.decode(errors="replace").strip()
    vs = getattr(pe, "VS_FIXEDFILEINFO", None)
    if vs:
        f = vs[0]
        return (
            f"{f.FileVersionMS >> 16}.{f.FileVersionMS & 0xFFFF}."
            f"{f.FileVersionLS >> 16}.{f.FileVersionLS & 0xFFFF}"
        )
    return "?"


def audit(exe: Path, entries: list[Entry]) -> Report:
    pe = pefile.PE(str(exe), fast_load=False)
    report = Report(path=exe, version=file_version(pe))
    starts = load_function_starts(pe)

    for entry in entries:
        result = Result(entry=entry)
        section = section_of(pe, entry.rva)
        if section is None:
            result.detail = "RVA outside every section"
            report.results.append(result)
            continue

        name = section.Name.rstrip(b"\x00").decode(errors="replace")
        result.section = name
        executable = bool(section.Characteristics & 0x20000000)  # MEM_EXECUTE

        if entry.kind == Kind.CODE:
            if not executable:
                result.detail = f"code RVA in non-executable section {name}"
            elif not starts:
                result.detail = "no exception directory to verify against"
            elif entry.rva in starts:
                result.ok = True
                result.detail = "function start"
            else:
                # Land inside a known function? Report the distance so a human
                # can see whether it is a near-miss (patch shift) or nonsense.
                below = [s for s in starts if s <= entry.rva]
                nearest = max(below) if below else None
                if nearest is None:
                    result.detail = "mid-code, no enclosing function"
                else:
                    result.detail = (
                        f"MID-FUNCTION (+0x{entry.rva - nearest:X} "
                        f"into fn @0x{nearest:X})"
                    )
        else:
            if executable:
                result.detail = f"data RVA in executable section {name}"
            elif entry.kind == Kind.POINTER and entry.rva % 8 != 0:
                result.detail = "pointer slot not 8-byte aligned"
            else:
                result.ok = True
                result.detail = f"in {name}"

        report.results.append(result)

    pe.close()
    return report


def disasm_at(exe: Path, rva: int, count: int = 4) -> list[str]:
    """Disassemble a few instructions at an RVA — context for triage."""
    if Cs is None:
        return ["(capstone not installed)"]
    pe = pefile.PE(str(exe), fast_load=True)
    try:
        data = pe.get_data(rva, 32)
    except Exception:
        pe.close()
        return ["(unreadable)"]
    pe.close()
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    out = []
    for insn in md.disasm(data, IMAGE_BASE + rva):
        out.append(f"    {insn.address:012X}  {insn.mnemonic} {insn.op_str}".rstrip())
        if len(out) >= count:
            break
    return out or ["    (no decode)"]


def find_candidates() -> list[Path]:
    """Probe the usual Steam layouts for any Fallout4 executable."""
    roots = [
        Path("C:/Games/Steam/steamapps/common/Fallout 4"),
        Path("C:/Program Files (x86)/Steam/steamapps/common/Fallout 4"),
        Path("D:/Steam/steamapps/common/Fallout 4"),
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("Fallout4.exe", "Fallout4_*.exe"):
            for p in sorted(root.glob(pattern)):
                if "Launcher" not in p.name and p not in found:
                    found.append(p)
    return found


def print_report(report: Report, verbose: bool) -> None:
    code, data = report.code, report.data
    code_ok = sum(1 for r in code if r.ok)
    data_ok = sum(1 for r in data if r.ok)
    score = report.score()

    print(f"\n{'=' * 78}")
    print(f"{report.path.name}   (FileVersion {report.version})")
    print(f"{report.path}")
    print("=" * 78)
    print(f"  code RVAs on a function start : {code_ok}/{len(code)}")
    print(f"  data RVAs in a sane section   : {data_ok}/{len(data)}")
    print(f"  MATCH SCORE                   : {score:6.1%}")

    if score >= 0.95:
        verdict = "PLAUSIBLE MATCH — offsets line up with this build"
    elif score >= 0.50:
        verdict = "PARTIAL — some drift; do NOT run, re-derive the misses"
    else:
        verdict = "WRONG BUILD — offsets are meaningless here"
    print(f"  VERDICT                       : {verdict}")

    misses = [r for r in code if not r.ok]
    if misses:
        shown = misses if verbose else misses[:12]
        print(f"\n  first {len(shown)} of {len(misses)} code misses:")
        for r in shown:
            print(f"    {r.entry.name:<42} 0x{r.entry.rva:08X}  {r.detail}")
        if not verbose and len(misses) > len(shown):
            print(f"    ... {len(misses) - len(shown)} more (pass --verbose)")


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", nargs="*", type=Path, help="Fallout4.exe to audit")
    ap.add_argument("--auto", action="store_true", help="probe known installs")
    ap.add_argument("--verbose", action="store_true", help="list every miss")
    ap.add_argument("--include-inline", action="store_true",
                    help="also audit RVAs hardcoded outside offsets.h")
    ap.add_argument("--minimal", action="store_true",
                    help="score only what an FW_MINIMAL build compiles "
                         "(fw_native/minimal_exclude.txt + #if !FW_MINIMAL "
                         "regions). This is the acceptance test for a first "
                         "playable build — see docs/START_HERE.md step H")
    ap.add_argument("--disasm", type=lambda s: int(s, 16), metavar="RVA",
                    help="disassemble at RVA in each binary and exit")
    args = ap.parse_args()

    targets = list(args.exe)
    if args.auto or not targets:
        targets.extend(p for p in find_candidates() if p not in targets)
    if not targets:
        print("no Fallout4.exe found; pass a path explicitly", file=sys.stderr)
        return 2

    if args.disasm is not None:
        for exe in targets:
            print(f"\n{exe.name} @ RVA 0x{args.disasm:X}")
            for line in disasm_at(exe, args.disasm, count=6):
                print(line)
        return 0

    entries = parse_offsets(OFFSETS_H)
    all_named = len(entries)

    skip: set[str] = set()
    if args.minimal:
        # Same source of truth CMake uses, so "minimal" means one thing.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from port_assess import (  # noqa: E402
            load_minimal_excluded_files,
            names_referenced_outside,
        )
        skip = load_minimal_excluded_files()
        live = names_referenced_outside(skip, {e.name for e in entries})
        entries = [e for e in entries if e.name in live]
        print(f"scope: FW_MINIMAL — {len(entries)} of {all_named} named "
              f"constants still referenced by compiled code")
    else:
        print(f"parsed {all_named} RVA constants from {OFFSETS_H.name}")

    kinds = {k: sum(1 for e in entries if e.kind == k) for k in
             (Kind.CODE, Kind.POINTER, Kind.DATA)}
    print(f"  classified: {kinds[Kind.CODE]} code, "
          f"{kinds[Kind.POINTER]} pointer, {kinds[Kind.DATA]} data")

    if args.include_inline:
        # `known` from the FULL table: an inline mention of a table entry is the
        # same address, and counting it twice would distort the score.
        inline = parse_inline_rvas(OFFSETS_H.parent,
                                   {e.rva for e in parse_offsets(OFFSETS_H)},
                                   skip=skip, minimal=args.minimal)
        print(f"  + {len(inline)} additional RVAs hardcoded outside offsets.h")
        entries += inline

    # Cross-check the constant against the IDA name in its own comment.
    typos = [e for e in entries if e.implied_rva is not None
             and e.implied_rva != e.rva]
    if typos:
        print(f"\n  !! {len(typos)} constant(s) disagree with their own comment:")
        for e in typos:
            print(f"     {e.name}: value 0x{e.rva:08X} vs comment "
                  f"0x{e.implied_rva:08X}")

    reports = []
    for exe in targets:
        try:
            reports.append(audit(exe, entries))
        except Exception as exc:  # pragma: no cover - IO/format guard
            print(f"\n{exe}: FAILED to audit — {exc}", file=sys.stderr)

    for report in reports:
        print_report(report, args.verbose)

    print()
    best = max(reports, key=lambda r: r.score(), default=None)
    if best is None:
        return 2
    print(f"best candidate: {best.path.name} ({best.version}) "
          f"at {best.score():.1%}")
    return 0 if best.score() >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
