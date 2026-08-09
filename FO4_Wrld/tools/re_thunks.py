"""Find SetName thunks and SSN string pointer chains."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402
import bisect

IMAGE_BASE = 0x140000000
ASSIGN = 0x01B41ED0


def main() -> None:
    pe = pefile.PE(
        r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe"
    )
    img = Image(
        Path(r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe")
    )
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)

    def owning(rva):
        i = bisect.bisect_right(starts, rva) - 1
        if i >= 0 and rva < ends[starts[i]]:
            return starts[i]
        return None

    patterns = [
        bytes([0x48, 0x8D, 0x49, 0x10]),  # lea rcx,[rcx+0x10]
        bytes([0x48, 0x83, 0xC1, 0x10]),  # add rcx,0x10
    ]
    print("=== SetName thunks (lea/add +0x10 then call/jmp ASSIGN) ===")
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for pat in patterns:
            i = 0
            while True:
                j = data.find(pat, i)
                if j < 0:
                    break
                k = j + len(pat)
                if k + 5 <= len(data) and data[k] in (0xE8, 0xE9):
                    disp = int.from_bytes(data[k + 1 : k + 5], "little", signed=True)
                    tgt = base + k + 5 + disp
                    if tgt == ASSIGN:
                        rva = base + j
                        # walk back to find function start if mid
                        fn = owning(rva)
                        kind = "call" if data[k] == 0xE8 else "jmp"
                        print(
                            f"  0x{rva:08X}  {kind}  fn_start={fn and hex(fn)}  "
                            f"size={fn and hex(ends[fn]-fn)}"
                        )
                        if fn and ends[fn] - fn <= 0x30:
                            md = Cs(CS_ARCH_X86, CS_MODE_64)
                            code = pe.get_data(fn, ends[fn] - fn)
                            for insn in md.disasm(code, IMAGE_BASE + fn):
                                print(
                                    f"    {insn.address-IMAGE_BASE:08X}: "
                                    f"{insn.mnemonic:8} {insn.op_str}"
                                )
                i = j + 1

    # Also: pure jmp thunks TO assign from a named export style
    # any function whose ONLY nontrivial op is call/jmp ASSIGN and size < 0x20
    print("\n=== tiny functions that only call ASSIGN ===")
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    for site_sec in pe.sections:
        if not (site_sec.Characteristics & 0x20000000):
            continue
        data, base = site_sec.get_data(), site_sec.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] != 0xE8:
                continue
            disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
            if base + i + 5 + disp != ASSIGN:
                continue
            fn = owning(base + i)
            if not fn:
                continue
            sz = ends[fn] - fn
            if sz > 0x20:
                continue
            print(f"  fn 0x{fn:08X} size=0x{sz:X}")
            code = pe.get_data(fn, sz)
            for insn in md.disasm(code, IMAGE_BASE + fn):
                print(f"    {insn.address-IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}")

    # SSN string absolute pointers
    print("\n=== abs ptrs to ShadowSceneNode cstr 0x03095360 ===")
    needle = struct.pack("<Q", IMAGE_BASE + 0x03095360)
    for s in pe.sections:
        data, base = s.get_data(), s.VirtualAddress
        idx = 0
        while True:
            j = data.find(needle, idx)
            if j < 0:
                break
            print(f"  0x{base+j:08X}")
            idx = j + 1

    # RTTI COL chain: complete object locator has TD pointer at +0x0C? 
    # Actually MSVC COL: signature, offset, cdOffset, pTypeDescriptor, pClassDescriptor
    # pTypeDescriptor is at +0x0C as RVA (32-bit) on x64 with /RTTI or as pointer
    # FO4 uses 64-bit absolute often for RTTI in .rdata

    # Find COL by scanning for 32-bit RVA of TD
    print("\n=== 32-bit RVA refs to TD 0x038C79F8 (possible COL) ===")
    td_rva = 0x038C79F8
    needle4 = struct.pack("<I", td_rva)
    for s in pe.sections:
        if s.Characteristics & 0x20000000:
            continue
        data, base = s.get_data(), s.VirtualAddress
        idx = 0
        n = 0
        while n < 20:
            j = data.find(needle4, idx)
            if j < 0:
                break
            print(f"  0x{base+j:08X}")
            idx = j + 1
            n += 1


if __name__ == "__main__":
    main()
