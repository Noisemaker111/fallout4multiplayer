"""Finish Tier A: SetName wrapper, NIF via Gamebryo string, SSN via RTTI xrefs."""
from __future__ import annotations

import argparse
import bisect
import struct
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402
from strxref import StringXrefs  # noqa: E402

IMAGE_BASE = 0x140000000
ASSIGN = 0x01B41ED0
CREATE = 0x01B41E70


def owning(starts, ends, rva):
    i = bisect.bisect_right(starts, rva) - 1
    if i >= 0 and rva < ends[starts[i]]:
        return starts[i]
    return None


def lea_rip_targets(pe, string_rva):
    """Find .text sites with lea reg, [rip+disp] targeting string_rva."""
    hits = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        # 48/4C 8D xx disp32
        import re

        for m in re.finditer(rb"[\x48\x4C]\x8D[\x05\x0D\x15\x1D\x25\x2D\x35\x3D]", data):
            off = m.start()
            if off + 7 > len(data):
                continue
            disp = int.from_bytes(data[off + 3 : off + 7], "little", signed=True)
            target = base + off + 7 + disp
            if target == string_rva:
                hits.append(base + off)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    args = ap.parse_args()
    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True

    # --- SetName: scan for add/lea rcx,+0x10 then call/jmp ASSIGN ---
    print("=== SetName wrapper byte patterns ===")
    patterns = [
        # lea rcx, [rcx+0x10] = 48 8D 49 10
        (bytes([0x48, 0x8D, 0x49, 0x10]), "lea rcx,[rcx+0x10]"),
        # add rcx, 0x10 = 48 83 C1 10
        (bytes([0x48, 0x83, 0xC1, 0x10]), "add rcx,0x10"),
        # lea rcx, [rax+0x10]
        (bytes([0x48, 0x8D, 0x48, 0x10]), "lea rcx,[rax+0x10]"),
    ]
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for pat, name in patterns:
            idx = 0
            while True:
                i = data.find(pat, idx)
                if i < 0:
                    break
                # look ahead 20 bytes for call to ASSIGN
                window = data[i : i + 24]
                for j in range(len(window) - 5):
                    if window[j] == 0xE8:
                        disp = int.from_bytes(window[j + 1 : j + 5], "little", signed=True)
                        absr = base + i + j + 5 + disp
                        if absr == ASSIGN:
                            rva = base + i
                            fn = owning(starts, ends, rva)
                            print(f"  HIT {name} @ 0x{rva:08X} fn=0x{fn:08X}" if fn else f"  HIT {name} @ 0x{rva:08X}")
                            if fn:
                                size = min(ends[fn] - fn, 0x60)
                                code = pe.get_data(fn, size)
                                for insn in md.disasm(code, IMAGE_BASE + fn):
                                    print(f"    {insn.address-IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}")
                                    if insn.mnemonic in ("ret", "retn"):
                                        break
                idx = i + 1

    # --- Gamebryo File Format -> NIF ---
    print("\n=== xrefs to 'Gamebryo File Format' @ 0x02E17538 ===")
    for site in lea_rip_targets(pe, 0x02E17538):
        fn = owning(starts, ends, site)
        print(f"  site 0x{site:08X} fn=0x{fn:08X} size=0x{ends[fn]-fn:X}" if fn else f"  site 0x{site:08X}")
        if fn:
            code = pe.get_data(fn, min(ends[fn] - fn, 0x200))
            n = 0
            for insn in md.disasm(code, IMAGE_BASE + fn):
                print(f"    {insn.address-IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}")
                n += 1
                if n >= 50 or insn.mnemonic in ("ret", "retn"):
                    break

    # --- ShadowSceneNode string xrefs ---
    print("\n=== xrefs to ShadowSceneNode string 0x03095360 ===")
    for site in lea_rip_targets(pe, 0x03095360)[:20]:
        fn = owning(starts, ends, site)
        print(f"  site 0x{site:08X} fn=0x{fn:08X}" if fn else f"  site 0x{site:08X}")

    # --- RTTI type descriptor xrefs (pointer to .?AVShadowSceneNode) ---
    # The TD is often referenced as absolute VA in .rdata vtables/RTTI
    print("\n=== data pointers to RTTI TD 0x038C79F8 (.?AVShadowSceneNode) ===")
    td_va = IMAGE_BASE + 0x038C79F8
    needle = struct.pack("<Q", td_va)
    for s in pe.sections:
        data, base = s.get_data(), s.VirtualAddress
        idx = 0
        while True:
            i = data.find(needle, idx)
            if i < 0:
                break
            print(f"  ptr @ 0x{base+i:08X}")
            idx = i + 1

    # Also find complete object locator / vtable for ShadowSceneNode via
    # string-based type info: scan for col that references TD
    print("\n=== StringXrefs ShadowSceneNode / Gamebryo ===")
    xref = StringXrefs(img)
    for s in ("ShadowSceneNode", "Gamebryo File Format", "NiStream", "BSFadeNode"):
        fns = xref.by_string.get(s, set())
        print(f"  {s!r}: {len(fns)}")
        for fn in sorted(fns)[:8]:
            print(f"      0x{fn:08X}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
