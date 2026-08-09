"""Find SetName, NIF load, SSN singleton via targeted binary patterns."""
from __future__ import annotations

import argparse
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
RELEASE = 0x01B42FD0
CREATE = 0x01B41E70
ASSIGN = 0x01B41ED0  # operator= from other BSFixedString


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


def owning(starts, ends, rva):
    i = bisect.bisect_right(starts, rva) - 1
    if i >= 0 and rva < ends[starts[i]]:
        return starts[i]
    return None


def disasm(pe, rva, size):
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    data = pe.get_data(rva, size)
    return list(md.disasm(data, IMAGE_BASE + rva))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    args = ap.parse_args()
    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)

    # --- SetName: small functions calling ASSIGN (0x1B41ED0) which is
    # BSFixedString::operator=(const BSFixedString&). SetName is often just
    # that on the name field: assign(&node->name, src).
    print("=== callers of BSFixedString operator= (0x01B41ED0) ===")
    for site in find_callers(pe, ASSIGN):
        fn = owning(starts, ends, site)
        if not fn:
            continue
        sz = ends[fn] - fn
        if sz > 0x80:
            continue
        insns = disasm(pe, fn, sz)
        print(f"\n0x{fn:08X} size=0x{sz:X}")
        for i in insns:
            print(f"  {i.address-IMAGE_BASE:08X}: {i.mnemonic:8} {i.op_str}")

    # Also try: lea rdx/rcx with +0x10 then call ASSIGN
    print("\n=== fns that LEA +0x10 then call ASSIGN (true SetName shape) ===")
    for site in find_callers(pe, ASSIGN):
        fn = owning(starts, ends, site)
        if not fn or ends[fn] - fn > 0x40:
            continue
        insns = disasm(pe, fn, ends[fn] - fn)
        blob = " | ".join(f"{i.mnemonic} {i.op_str}" for i in insns)
        if "0x10" in blob:
            print(f"0x{fn:08X}: {blob}")

    # Direct store pattern for setname free function:
    # mov rax, [rcx+0x10]; mov [rcx+0x10], rdx; call release
    print("\n=== byte-scan SetName-like: mov [reg+0x10], reg; call release ===")
    # too hard to byte-scan reliably; instead check tiny wrappers
    for site in find_callers(pe, RELEASE):
        fn = owning(starts, ends, site)
        if not fn:
            continue
        sz = ends[fn] - fn
        if not (0x10 <= sz <= 0x40):
            continue
        insns = disasm(pe, fn, sz)
        print(f"\n0x{fn:08X} sz=0x{sz:X}")
        for i in insns:
            print(f"  {i.address-IMAGE_BASE:08X}: {i.mnemonic:8} {i.op_str}")

    # --- NIF factory registration ---
    print("\n=== NIF factory 0x01CE04B0 (first 80 insns) ===")
    for i in disasm(pe, 0x01CE04B0, min(0x400, ends.get(0x01CE04B0, 0x01CE04B0 + 0x400) - 0x01CE04B0)):
        print(f"  {i.address-IMAGE_BASE:08X}: {i.mnemonic:8} {i.op_str}")
        if i.mnemonic in ("ret", "retn"):
            break

    # --- Search RTTI / type names in data ---
    print("\n=== binary string scan for Shadow/Scene/NIF loader ===")
    needles = [
        b"ShadowSceneNode",
        b".?AVShadowSceneNode",
        b"SceneGraph",
        b".?AVBSSceneGraph",
        b"BSFadeNode",
        b".?AVBSFadeNode",
        b"LoadNIF",
        b"LoadNif",
        b"NifFile",
        b"Gamebryo File Format",
        b"NiStream",
    ]
    for s in pe.sections:
        data = s.get_data()
        base = s.VirtualAddress
        for n in needles:
            idx = 0
            while True:
                i = data.find(n, idx)
                if i < 0:
                    break
                print(f"  0x{base+i:08X}  {n!r}")
                idx = i + 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
