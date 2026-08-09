"""Function fingerprinting for cross-build address porting.

Shared library for the port pipeline. Given a Fallout4.exe, it can:

  * enumerate function ranges from the PE exception directory (.pdata), which
    on x64 MSVC is an exhaustive index of unwindable functions
  * disassemble one function and reduce it to a fingerprint that should
    survive a recompile: referenced string literals, distinctive immediate
    constants, call count, basic-block count, instruction-mnemonic histogram

The point is to match "the function at RVA X in build A" to "the function at
RVA Y in build B" without a symbol table. Strings are the strongest signal by
far — a function that references "BSSkin::Instance" almost certainly maps to
the one function in the other build referencing the same literal. Everything
else is a tiebreak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CS_OP_IMM, CS_OP_MEM

IMAGE_BASE = 0x140000000

# A run of printable bytes long enough to be a real literal rather than
# incidental data. Four is the usual floor for strings tooling.
_MIN_STR = 4
_PRINTABLE = re.compile(rb"[\x20-\x7E]{%d,}" % _MIN_STR)


@dataclass
class Fingerprint:
    rva: int
    size: int = 0
    n_calls: int = 0
    n_insns: int = 0
    n_blocks: int = 0
    strings: set[str] = field(default_factory=set)
    # Immediates big enough to be discriminating. Small ones (0, 1, 8, 0x10)
    # appear in every function and only add noise.
    consts: set[int] = field(default_factory=set)
    mnemonics: tuple[tuple[str, int], ...] = ()
    # RVAs of direct `call rel32` targets, in the order they are issued. Order
    # matters and must not be sorted: positional propagation relies on "the
    # i-th call site in A corresponds to the i-th in B", which is a property of
    # the source code and survives a recompile. Sorting by address would
    # destroy exactly the signal we depend on, since addresses are what
    # changed between builds.
    callees: list[int] = field(default_factory=list)

    @property
    def callee_set(self) -> set[int]:
        return set(self.callees)

    def is_distinctive(self) -> bool:
        """Does this have any signal a matcher could actually key on?"""
        return bool(self.strings) or len(self.consts) >= 2


class Image:
    """A parsed Fallout4.exe with lazily-built lookup tables."""

    def __init__(self, path):
        self.path = path
        self.pe = pefile.PE(str(path), fast_load=False)
        self._sections = [
            (s.VirtualAddress,
             max(s.Misc_VirtualSize, s.SizeOfRawData),
             s.Name.rstrip(b"\x00").decode(errors="replace"),
             bool(s.Characteristics & 0x20000000))
            for s in self.pe.sections
        ]
        self._md = Cs(CS_ARCH_X86, CS_MODE_64)
        self._md.detail = True
        self._functions: list[tuple[int, int]] | None = None
        self._all_ranges: list[tuple[int, int]] = []

    # --- layout ------------------------------------------------------------

    def section_of(self, rva: int):
        for start, size, name, execu in self._sections:
            if start <= rva < start + size:
                return name, execu
        return None, False

    def _is_chained(self, unwind_rva: int) -> bool:
        """True if this UNWIND_INFO is a continuation of another function.

        MSVC splits functions across non-contiguous ranges and emits one
        RUNTIME_FUNCTION per chunk. Continuation chunks set UNW_FLAG_CHAININFO
        and their BeginAddress is the middle of a function, not a start.
        Treating them as function starts produces exactly the symptom seen in
        the first matcher run: "matches" at unaligned addresses like
        0x01449355 that are really mid-function.

        UNWIND_INFO byte 0 = Version (bits 0-2) | Flags (bits 3-7).
        """
        if not unwind_rva:
            return False
        raw = self.read(unwind_rva, 1)
        if not raw:
            return False
        return bool((raw[0] >> 3) & 0x4)      # UNW_FLAG_CHAININFO

    def functions(self) -> list[tuple[int, int]]:
        """Sorted (begin_rva, end_rva) for every real function start.

        Continuation chunks are excluded — see _is_chained.
        """
        if self._functions is None:
            out = []
            all_ranges = []
            for fn in getattr(self.pe, "DIRECTORY_ENTRY_EXCEPTION", []) or []:
                begin = getattr(fn.struct, "BeginAddress", 0)
                end = getattr(fn.struct, "EndAddress", 0)
                unwind = getattr(fn.struct, "UnwindData", 0)
                if not begin or end <= begin:
                    continue
                # Every range participates in containment lookups, so an
                # address inside a split function still resolves to something.
                # Only non-chained records count as function *starts*.
                all_ranges.append((begin, end))
                if self._is_chained(unwind):
                    continue
                out.append((begin, end))
            out.sort()
            all_ranges.sort()
            merged_all: dict[int, int] = {}
            for b, e in all_ranges:
                if e > merged_all.get(b, 0):
                    merged_all[b] = e
            self._all_ranges = sorted(merged_all.items())
            # Chunked functions appear as several RUNTIME_FUNCTIONs sharing a
            # start; keep the widest range per start so a fingerprint covers
            # the whole body.
            merged: dict[int, int] = {}
            for b, e in out:
                if e > merged.get(b, 0):
                    merged[b] = e
            self._functions = sorted(merged.items())
        return self._functions

    @lru_cache(maxsize=1)
    def _starts(self) -> frozenset[int]:
        return frozenset(b for b, _ in self.functions())

    def is_function_start(self, rva: int) -> bool:
        return rva in self._starts()

    def enclosing_function(self, rva: int) -> tuple[int, int] | None:
        """The (begin, end) whose range contains rva, or None.

        Searches ALL ranges including continuation chunks — an address in the
        middle of a split function must still resolve, otherwise anchoring
        loses every pinned address that happens to sit in a later chunk.
        """
        self.functions()          # ensure _all_ranges is built
        fns = self._all_ranges
        lo, hi = 0, len(fns) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            b, e = fns[mid]
            if b <= rva:
                best = (b, e)
                lo = mid + 1
            else:
                hi = mid - 1
        if best and best[0] <= rva < best[1]:
            return best
        return None

    # --- data --------------------------------------------------------------

    def read(self, rva: int, n: int) -> bytes:
        try:
            return self.pe.get_data(rva, n)
        except Exception:
            return b""

    def string_at(self, rva: int, max_len: int = 128) -> str | None:
        """A printable NUL-terminated literal at rva, if one is there."""
        name, execu = self.section_of(rva)
        if name is None or execu:
            return None          # code, not data
        raw = self.read(rva, max_len)
        if not raw:
            return None
        end = raw.find(b"\x00")
        cand = raw if end < 0 else raw[:end]
        if len(cand) < _MIN_STR:
            return None
        if not _PRINTABLE.fullmatch(cand):
            return None
        try:
            return cand.decode("ascii")
        except UnicodeDecodeError:
            return None

    # --- fingerprinting ----------------------------------------------------

    def fingerprint(self, begin: int, end: int,
                    max_bytes: int = 4096) -> Fingerprint:
        size = min(end - begin, max_bytes)
        code = self.read(begin, size)
        fp = Fingerprint(rva=begin, size=end - begin)
        if not code:
            return fp

        mnem: dict[str, int] = {}
        # Branch targets inside the body approximate basic-block count without
        # building a real CFG — enough for a similarity tiebreak.
        block_starts: set[int] = set()

        for insn in self._md.disasm(code, IMAGE_BASE + begin):
            fp.n_insns += 1
            mnem[insn.mnemonic] = mnem.get(insn.mnemonic, 0) + 1

            if insn.mnemonic == "call":
                fp.n_calls += 1
                # Direct near call: the single immediate operand is already
                # the absolute VA. Indirect calls (vtable dispatch) carry no
                # static target and are skipped.
                ops = insn.operands
                if len(ops) == 1 and ops[0].type == CS_OP_IMM:
                    target = int(ops[0].imm) - IMAGE_BASE
                    if 0 < target < 0x10000000:
                        fp.callees.append(target)
            if insn.mnemonic.startswith("j"):
                block_starts.add(insn.address + insn.size)

            for op in insn.operands:
                if op.type == CS_OP_IMM:
                    v = op.imm
                    # Keep values unlikely to be structural noise: large
                    # magic numbers, form ids, offsets past the small-int band.
                    if 0x1000 <= abs(v) <= 0xFFFFFFFF:
                        fp.consts.add(int(v))
                elif op.type == CS_OP_MEM and op.mem.base == 0x29:  # RIP
                    target = insn.address + insn.size + op.mem.disp
                    s = self.string_at(int(target) - IMAGE_BASE)
                    if s:
                        fp.strings.add(s)

        fp.n_blocks = len(block_starts) + 1
        fp.mnemonics = tuple(sorted(mnem.items(), key=lambda kv: -kv[1])[:12])
        return fp

    def close(self) -> None:
        self.pe.close()
