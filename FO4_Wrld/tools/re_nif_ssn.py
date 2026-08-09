"""Locate NIF load public API and ShadowSceneNode singleton."""
from __future__ import annotations

import bisect
import re
import struct
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE_BASE = 0x140000000
REGISTER = 0x01BBA390  # called from factory with (name*, ctor*)


def owning(starts, ends, rva):
    i = bisect.bisect_right(starts, rva) - 1
    if i >= 0 and rva < ends[starts[i]]:
        return starts[i]
    return None


def find_callers(pe, target):
    out = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] == 0xE8:
                disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
                if base + i + 5 + disp == target:
                    out.append(base + i)
    return out


def disasm(pe, rva, size):
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    return list(md.disasm(pe.get_data(rva, size), IMAGE_BASE + rva))


def main() -> None:
    pe = pefile.PE(
        r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe"
    )
    img = Image(
        Path(r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe")
    )
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)

    # BSFadeNode vt - COL is usually right before vtable
    fade_vt = 0x03085EE8
    print("=== around BSFadeNode vtable (find COL pattern) ===")
    raw = pe.get_data(fade_vt - 0x20, 0x30)
    for i in range(0, 0x30, 8):
        q = struct.unpack_from("<Q", raw, i)[0]
        print(f"  0x{fade_vt - 0x20 + i:08X}: 0x{q:016X}")

    # For each known vtable, qword at vt-8 often is COL pointer
    for name, vt in [
        ("BSFadeNode", 0x03085EE8),
        ("NiNode", 0x02E14818),
        ("NiAVObject", 0x02E150A8),
    ]:
        prev = struct.unpack("<Q", pe.get_data(vt - 8, 8))[0]
        print(f"{name} vt-8 = 0x{prev:X} rva=0x{prev-IMAGE_BASE:X}" if prev > IMAGE_BASE else f"{name} vt-8 = 0x{prev:X}")

    # Find ShadowSceneNode vtable via RTTI: scan for type name in RTTI TypeDescriptor
    # Structure: void* vtable_of_type_info; void* spare; char name[]
    # Search for ".?AVShadowSceneNode@@" 
    print("\n=== find .?AVShadowSceneNode@@ ===")
    needle = b".?AVShadowSceneNode"
    for s in pe.sections:
        data, base = s.get_data(), s.VirtualAddress
        j = data.find(needle)
        if j >= 0:
            print(f"  found at 0x{base+j:08X}")
            # TD starts a few bytes before the name (typically -0x10)
            for back in (0x10, 0x18, 0x8, 0x20):
                td = base + j - back
                print(f"  candidate TD 0x{td:08X}")
                # find 64-bit pointers to this TD
                n64 = struct.pack("<Q", IMAGE_BASE + td)
                n32 = struct.pack("<I", td)
                for s2 in pe.sections:
                    d2, b2 = s2.get_data(), s2.VirtualAddress
                    k = 0
                    while True:
                        p = d2.find(n64, k)
                        if p < 0:
                            break
                        print(f"    64ptr @ 0x{b2+p:08X}")
                        k = p + 1
                    k = 0
                    hits32 = 0
                    while hits32 < 15:
                        p = d2.find(n32, k)
                        if p < 0:
                            break
                        # likely COL field
                        print(f"    32rva @ 0x{b2+p:08X}")
                        k = p + 1
                        hits32 += 1

    # NIF: find functions referencing "Gamebryo File Format" that look like load
    # also search for path-like load - callers of CREATE with ".nif" in same fn strings
    print("\n=== NiStream / load candidates: large fns calling CREATE many times ===")
    create_callers = find_callers(pe, 0x01B41E70)
    from collections import Counter
    c = Counter()
    for site in create_callers:
        fn = owning(starts, ends, site)
        if fn:
            c[fn] += 1
    for fn, n in c.most_common(15):
        if n < 3:
            continue
        sz = ends[fn] - fn
        print(f"  0x{fn:08X} create_calls={n} size=0x{sz:X}")

    # Public NIF API from NG was large and took path. Look for functions
    # that call both intern/create and something stream-related.
    print("\n=== disasm factory register helper 0x01BBA390 ===")
    for insn in disasm(pe, 0x01BBA390, min(0x100, ends.get(0x01BBA390, 0x01BBA390 + 0x100) - 0x01BBA390)):
        print(f"  {insn.address-IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}")
        if insn.mnemonic in ("ret", "retn"):
            break


if __name__ == "__main__":
    main()
