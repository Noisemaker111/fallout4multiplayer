"""Resolve SSN singleton global from ctor caller stores."""
from __future__ import annotations

import bisect
from pathlib import Path
import sys

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CS_OP_MEM, CS_OP_IMM

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE = 0x140000000
SSN_CTOR = 0x0280E5B0


def main() -> None:
    pe = pefile.PE(
        r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe"
    )
    img = Image(
        Path(r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe")
    )
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True

    callers = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] == 0xE8:
                disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
                if base + i + 5 + disp == SSN_CTOR:
                    callers.append(base + i)

    print(f"SSN ctor callers: {len(callers)}")
    for site in callers:
        i = bisect.bisect_right(starts, site) - 1
        fn = starts[i] if i >= 0 and site < ends[starts[i]] else None
        if not fn:
            continue
        print(f"\n=== fn 0x{fn:08X} (call site 0x{site:08X}) size=0x{ends[fn]-fn:X} ===")
        data = pe.get_data(fn, ends[fn] - fn)
        for insn in md.disasm(data, IMAGE + fn):
            rva = insn.address - IMAGE
            extra = ""
            if insn.op_count(CS_OP_MEM):
                for op in insn.operands:
                    if op.type == CS_OP_MEM and op.mem.base == 0 and False:
                        pass
                    # rip-relative: base is RIP which capstone encodes as
                    if op.type == CS_OP_MEM:
                        # on x86_64 rip-relative has base == X86_REG_RIP
                        from capstone.x86 import X86_REG_RIP

                        if op.mem.base == X86_REG_RIP:
                            tgt = rva + insn.size + op.mem.disp
                            extra = f"  ; mem=0x{tgt:08X}"
            print(f"  {rva:08X}: {insn.mnemonic:8} {insn.op_str}{extra}")


if __name__ == "__main__":
    main()
