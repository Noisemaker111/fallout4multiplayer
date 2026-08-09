"""Find MEM_POOL global used with ALLOCATE_FN 0x01B0EE10."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CS_OP_MEM
from capstone.x86 import X86_REG_RIP

IMAGE = 0x140000000
ALLOC = 0x01B0EE10


def main() -> None:
    pe = pefile.PE(
        r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe"
    )
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    pools: Counter[int] = Counter()

    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] != 0xE8:
                continue
            disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
            if base + i + 5 + disp != ALLOC:
                continue
            # look back 48 bytes
            start = max(0, i - 48)
            for insn in md.disasm(data[start:i], IMAGE + base + start):
                if insn.mnemonic != "lea":
                    continue
                if not insn.op_str.startswith("rcx"):
                    continue
                for op in insn.operands:
                    if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                        rva = insn.address - IMAGE
                        tgt = rva + insn.size + op.mem.disp
                        pools[tgt] += 1

    print("pool candidates (lea rcx before call ALLOCATE_FN):")
    for p, n in pools.most_common(20):
        print(f"  0x{p:08X}  hits={n}")


if __name__ == "__main__":
    main()
