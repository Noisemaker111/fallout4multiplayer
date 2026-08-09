"""Find pool arg for ALLOCATE_FN more carefully."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

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
    samples = 0

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
            samples += 1
            if samples <= 5:
                # print context
                start = max(0, i - 32)
                print(f"\nsite 0x{base+i:08X}:")
                for insn in md.disasm(data[start : i + 5], IMAGE + base + start):
                    print(f"  {insn.address-IMAGE:08X}: {insn.mnemonic:8} {insn.op_str}")
            start = max(0, i - 48)
            for insn in md.disasm(data[start:i], IMAGE + base + start):
                if "rip" not in insn.op_str:
                    continue
                for op in insn.operands:
                    if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                        rva = insn.address - IMAGE
                        tgt = rva + insn.size + op.mem.disp
                        pools[tgt] += 1

    print(f"\ntotal call sites to ALLOCATE_FN: {samples}")
    print("rip-relative targets near calls:")
    for p, n in pools.most_common(25):
        print(f"  0x{p:08X}  hits={n}")


if __name__ == "__main__":
    main()
