"""Resolve ShadowSceneNode vtable from RTTI COL chain."""
from __future__ import annotations

import struct
import sys

import pefile

IMAGE = 0x140000000


def main() -> None:
    pe = pefile.PE(
        r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe"
    )

    # Validate BSFadeNode pattern: vt-8 -> COL
    fade_vt = 0x03085EE8
    col_va = struct.unpack("<Q", pe.get_data(fade_vt - 8, 8))[0]
    col = col_va - IMAGE
    print(f"BSFadeNode COL rva=0x{col:X}")
    raw = pe.get_data(col, 0x18)
    fields = [struct.unpack_from("<I", raw, i)[0] for i in range(0, 0x18, 4)]
    print("  COL u32s:", [f"0x{x:X}" for x in fields])
    # TD is typically at +0x0C as image-relative RVA
    td = fields[3]
    print(f"  TD rva field=0x{td:X}")
    name = pe.get_data(td + 0x10, 40)
    print(f"  TD name@+10: {name}")

    # ShadowSceneNode TD name at 0x038C79F8, so TD base likely 0x038C79E8
    td_ssn = 0x038C79E8
    print(f"\nShadowSceneNode TD=0x{td_ssn:X} name={pe.get_data(td_ssn+0x10, 32)}")

    # Find COLs containing this TD RVA at +0x0C
    needle = struct.pack("<I", td_ssn)
    cols = []
    for s in pe.sections:
        if s.Characteristics & 0x20000000:
            continue
        data, base = s.get_data(), s.VirtualAddress
        idx = 0
        while True:
            j = data.find(needle, idx)
            if j < 0:
                break
            # if this is COL+0x0C, COL starts at j-0x0C
            if j >= 0x0C:
                col_rva = base + j - 0x0C
                sig = struct.unpack("<I", pe.get_data(col_rva, 4))[0]
                if sig in (0, 1):  # common COL signatures
                    cols.append(col_rva)
                    print(f"  COL candidate 0x{col_rva:08X} sig={sig}")
            idx = j + 1

    # Find vtables whose preceding qword is COL VA
    print("\nVtables referencing those COLs:")
    for col_rva in cols:
        col_va = IMAGE + col_rva
        needle8 = struct.pack("<Q", col_va)
        for s in pe.sections:
            if s.Characteristics & 0x20000000:
                continue
            data, base = s.get_data(), s.VirtualAddress
            idx = 0
            while True:
                j = data.find(needle8, idx)
                if j < 0:
                    break
                vt = base + j + 8
                slots = pe.get_data(vt, 16)
                s0 = struct.unpack_from("<Q", slots, 0)[0]
                s1 = struct.unpack_from("<Q", slots, 8)[0]
                print(f"  vt=0x{vt:08X}  [0]=0x{s0:X} [1]=0x{s1:X}")
                idx = j + 1

    # Also try TD at 0x038C79F0 / 0x038C79E0
    for td in (0x038C79E0, 0x038C79E8, 0x038C79F0, 0x038C79F8 - 0x10):
        print(f"\n--- TD 0x{td:X} ---")
        try:
            print(" name:", pe.get_data(td + 0x10, 28))
        except Exception as e:
            print("  fail", e)


if __name__ == "__main__":
    main()
