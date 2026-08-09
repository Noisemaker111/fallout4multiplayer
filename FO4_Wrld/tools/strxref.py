"""Fast string cross-reference index for a Fallout4.exe.

Answers "which functions reference this string literal?" across the whole
image, which is the highest-precision seed a cross-build function matcher can
have: if exactly one function in each build references "BSSkin::Instance", the
pair is almost certainly the same function.

Speed
-----
Disassembling ~218k functions with capstone in Python takes hours. This instead
byte-scans .text for the one instruction form that loads a data address:

    REX.W  8D  modrm(mod=00, rm=101)  disp32        =  lea r64, [rip+disp32]

That is a fixed 7-byte shape, so a regex over the section bytes finds every
candidate in seconds. Targets are then checked for printable NUL-terminated
data. Some hits are not really string loads (the scan can land mid-instruction
and misread a byte sequence), which is fine: a spurious xref adds a little
noise to a set, and matching keys on set overlap rather than exact identity.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict

IMAGE_BASE = 0x140000000

# REX.W (0x48) or REX.WR (0x4C) + 0x8D (lea) + modrm with mod=00, rm=101.
# Those modrm bytes are 0x05,0x0D,0x15,0x1D,0x25,0x2D,0x35,0x3D — i.e.
# (modrm & 0xC7) == 0x05. Spelled out so the regex stays a literal char class.
_LEA_RIP = re.compile(
    rb"[\x48\x4C]\x8D[\x05\x0D\x15\x1D\x25\x2D\x35\x3D]",
    re.DOTALL,
)

_INSN_LEN = 7           # REX + opcode + modrm + disp32
_MIN_STR = 4
_MAX_STR = 96
_PRINTABLE = re.compile(rb"[\x20-\x7E]+")


class StringXrefs:
    """string -> set of function RVAs, and function RVA -> set of strings."""

    def __init__(self, image):
        self.image = image
        self.by_string: dict[str, set[int]] = defaultdict(set)
        self.by_function: dict[int, set[str]] = defaultdict(set)
        self._build()

    # -- internals ---------------------------------------------------------

    def _text_sections(self):
        out = []
        for s in self.image.pe.sections:
            if s.Characteristics & 0x20000000:      # MEM_EXECUTE
                out.append((s.VirtualAddress, s.get_data()))
        return out

    def _string_reader(self):
        """Cache decoded strings by RVA — the same literal is hit many times."""
        cache: dict[int, str | None] = {}
        pe = self.image.pe
        sections = [
            (s.VirtualAddress,
             max(s.Misc_VirtualSize, s.SizeOfRawData),
             bool(s.Characteristics & 0x20000000))
            for s in pe.sections
        ]

        def read(rva: int):
            if rva in cache:
                return cache[rva]
            hit = None
            for start, size, execu in sections:
                if start <= rva < start + size:
                    hit = execu
                    break
            if hit is None or hit:          # outside image, or in code
                cache[rva] = None
                return None
            try:
                raw = pe.get_data(rva, _MAX_STR)
            except Exception:
                cache[rva] = None
                return None
            end = raw.find(b"\x00")
            cand = raw if end < 0 else raw[:end]
            if len(cand) < _MIN_STR or not _PRINTABLE.fullmatch(cand):
                cache[rva] = None
                return None
            try:
                val = cand.decode("ascii")
            except UnicodeDecodeError:
                val = None
            cache[rva] = val
            return val

        return read

    def _build(self) -> None:
        fns = self.image.functions()
        starts = [b for b, _ in fns]
        ends = {b: e for b, e in fns}
        read_string = self._string_reader()

        for sec_rva, data in self._text_sections():
            for m in _LEA_RIP.finditer(data):
                off = m.start()
                if off + _INSN_LEN > len(data):
                    continue
                disp = int.from_bytes(data[off + 3:off + 7], "little",
                                      signed=True)
                insn_rva = sec_rva + off
                target = insn_rva + _INSN_LEN + disp
                if target < 0:
                    continue

                s = read_string(target)
                if not s:
                    continue

                # Which function contains this instruction?
                i = bisect_right(starts, insn_rva) - 1
                if i < 0:
                    continue
                fn = starts[i]
                if insn_rva >= ends[fn]:
                    continue        # in a gap between functions

                self.by_string[s].add(fn)
                self.by_function[fn].add(s)

    # -- queries -----------------------------------------------------------

    def strings_of(self, fn_rva: int) -> set[str]:
        return self.by_function.get(fn_rva, set())

    def unique_strings(self) -> dict[str, int]:
        """Strings referenced by exactly one function — the best seeds."""
        return {s: next(iter(fns))
                for s, fns in self.by_string.items() if len(fns) == 1}

    def stats(self) -> str:
        return (f"{len(self.by_string)} distinct strings, "
                f"{len(self.by_function)} functions with xrefs, "
                f"{len(self.unique_strings())} uniquely-referencing strings")
