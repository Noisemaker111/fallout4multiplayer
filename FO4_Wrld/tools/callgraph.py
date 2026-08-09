"""Whole-image call graph, built by byte scan.

The matcher needs to propagate matches in BOTH directions: from a known
function to what it calls, and from a known function to what calls it. The
reverse edges are the important half — the pinned addresses are deep engine
internals that string-bearing functions rarely call, but which frequently
*call into* well-identified helpers.

Disassembling ~218k functions to get this would take hours. Instead scan .text
for the direct near-call form:

    E8 disp32                    = call rel32     (5 bytes)

then keep only targets that are real function starts per the exception
directory. That filter removes essentially all false hits from E8 bytes that
were actually operands or immediates, because a bogus target almost never
lands exactly on a RUNTIME_FUNCTION boundary.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict

_CALL_REL32 = re.compile(rb"\xE8", re.DOTALL)
_INSN_LEN = 5


class CallGraph:
    def __init__(self, image):
        self.image = image
        self.callees: dict[int, list[int]] = defaultdict(list)
        self.callers: dict[int, set[int]] = defaultdict(set)
        self._build()

    def _build(self) -> None:
        fns = self.image.functions()
        starts = [b for b, _ in fns]
        ends = {b: e for b, e in fns}
        valid = set(starts)

        for section in self.image.pe.sections:
            if not (section.Characteristics & 0x20000000):
                continue
            sec_rva = section.VirtualAddress
            data = section.get_data()

            for m in _CALL_REL32.finditer(data):
                off = m.start()
                if off + _INSN_LEN > len(data):
                    continue
                disp = int.from_bytes(data[off + 1:off + 5], "little",
                                      signed=True)
                insn_rva = sec_rva + off
                target = insn_rva + _INSN_LEN + disp
                if target not in valid:
                    continue        # not a function start -> not a real call

                i = bisect_right(starts, insn_rva) - 1
                if i < 0:
                    continue
                caller = starts[i]
                if insn_rva >= ends[caller]:
                    continue        # inter-function padding

                self.callees[caller].append(target)
                self.callers[target].add(caller)

    def out_edges(self, fn: int) -> list[int]:
        """Call targets in address order of their call sites.

        finditer walks the section forward, so append order already reflects
        call-site order within a function. That ordering is the signal
        positional propagation depends on — never sort it.
        """
        return self.callees.get(fn, [])

    def in_edges(self, fn: int) -> set[int]:
        return self.callers.get(fn, set())

    def stats(self) -> str:
        edges = sum(len(v) for v in self.callees.values())
        return (f"{len(self.callees)} calling functions, "
                f"{len(self.callers)} called functions, {edges} edges")
