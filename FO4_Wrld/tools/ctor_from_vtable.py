"""Find constructors by scanning .text for stores of a known vtable address.

MSVC x64 ctors typically begin with:
    mov rax, imm64          ; or lea rax, [rip+disp]
    mov [rcx], rax          ; store vptr

We scan for the absolute 8-byte vtable VA embedded in code, or for
RIP-relative LEA that targets the vtable, then attribute the containing
function as the ctor (or a factory that installs the vtable).
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE_BASE = 0x140000000

# name -> 1.10.163 vtable RVA (from vtable_port)
VTABLES = {
    "NiNode": 0x02E14818,
    "NiAVObject": 0x02E150A8,
    "BSFadeNode": 0x03085EE8,
    "BSSkin::Instance": 0x02E16588,
    "BSGeometry": 0x02E161D8,
    "BSTriShape": 0x02E16AE8,
    "BSDynamicTriShape": 0x02E17BA8,
    "BSSubIndexTriShape": 0x02E314E8,
    "NiCamera": 0x02E15E38,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    fns = img.functions()
    starts = [s for s, _ in fns]
    ends = {s: e for s, e in fns}

    def owning_fn(rva: int) -> int | None:
        import bisect

        i = bisect.bisect_right(starts, rva) - 1
        if i < 0:
            return None
        s = starts[i]
        if rva < ends[s]:
            return s
        return None

    text_secs = []
    for s in pe.sections:
        if s.Characteristics & 0x20000000:
            text_secs.append((s.VirtualAddress, s.get_data()))

    for name, vt_rva in VTABLES.items():
        vt_va = IMAGE_BASE + vt_rva
        needle = struct.pack("<Q", vt_va)
        hits: dict[int, int] = {}  # fn_start -> count
        for sec_rva, data in text_secs:
            start = 0
            while True:
                i = data.find(needle, start)
                if i < 0:
                    break
                insn_rva = sec_rva + i
                fn = owning_fn(insn_rva)
                if fn is not None:
                    hits[fn] = hits.get(fn, 0) + 1
                start = i + 1

        # Also try RIP-relative LEA to vtable: 48 8D xx disp32 where target=vt
        # lea r64, [rip+disp] = 48/4C 8D modrm(disp32)
        import re

        lea_re = re.compile(rb"[\x48\x4C]\x8D[\x05\x0D\x15\x1D\x25\x2D\x35\x3D]")
        for sec_rva, data in text_secs:
            for m in lea_re.finditer(data):
                off = m.start()
                if off + 7 > len(data):
                    continue
                disp = int.from_bytes(data[off + 3 : off + 7], "little", signed=True)
                target = sec_rva + off + 7 + disp
                if target == vt_rva:
                    fn = owning_fn(sec_rva + off)
                    if fn is not None:
                        hits[fn] = hits.get(fn, 0) + 1

        ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"\n=== {name} vt=0x{vt_rva:08X}: {len(ranked)} functions reference it ===")
        for fn, n in ranked[:12]:
            size = ends[fn] - fn
            print(f"  0x{fn:08X}  refs={n}  size=0x{size:X}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
