"""Find global SSN** by tracing stores of SSN ctor result and Interface3D paths."""
from __future__ import annotations

import bisect
from pathlib import Path
import sys

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CS_OP_MEM
from capstone.x86 import X86_REG_RIP, X86_REG_R14, X86_REG_RAX

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

    # All call sites of SSN ctor
    sites = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] != 0xE8:
                continue
            disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
            if base + i + 5 + disp == SSN_CTOR:
                sites.append(base + i)

    print(f"SSN ctor sites: {len(sites)}")
    for site in sites:
        i = bisect.bisect_right(starts, site) - 1
        fn = starts[i] if i >= 0 and site < ends[starts[i]] else None
        if not fn:
            continue
        print(f"\n=== 0x{fn:08X} site=0x{site:08X} ===")
        data = pe.get_data(fn, ends[fn] - fn)
        # after each call to ctor, track where rax/r14 goes if stored to rip-relative
        saw_ctor = False
        for insn in md.disasm(data, IMAGE + fn):
            rva = insn.address - IMAGE
            if rva == site:
                saw_ctor = True
                print(f"  {rva:08X}: CALL CTOR")
                continue
            if not saw_ctor:
                continue
            # print next ~40 insns after ctor with mem annotations
            extra = ""
            for op in insn.operands:
                if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                    tgt = rva + insn.size + op.mem.disp
                    extra = f"  ; -> 0x{tgt:08X}"
            print(f"  {rva:08X}: {insn.mnemonic:8} {insn.op_str}{extra}")
            if rva > site + 0x80:
                break

    # Also: search for mov reg, [rip+X]; mov reg2, [reg+0x238/0x240]
    # as SSN access pattern through Renderer*
    print("\n=== loads of [reg+0x238] or [reg+0x240] near known globals ===")
    # find mov rax/rcx/rdx, [reg+0x238] patterns
    # 48 8B 80 38 02 00 00 = mov rax, [rax+0x238]
    # 48 8B 81 38 02 00 00 = mov rax, [rcx+0x238]
    patterns = {
        bytes([0x48, 0x8B, 0x80, 0x38, 0x02, 0x00, 0x00]): "mov rax,[rax+0x238]",
        bytes([0x48, 0x8B, 0x81, 0x38, 0x02, 0x00, 0x00]): "mov rax,[rcx+0x238]",
        bytes([0x48, 0x8B, 0x82, 0x38, 0x02, 0x00, 0x00]): "mov rax,[rdx+0x238]",
        bytes([0x48, 0x8B, 0x80, 0x40, 0x02, 0x00, 0x00]): "mov rax,[rax+0x240]",
        bytes([0x48, 0x8B, 0x81, 0x40, 0x02, 0x00, 0x00]): "mov rax,[rcx+0x240]",
        bytes([0x4C, 0x8B, 0x81, 0x38, 0x02, 0x00, 0x00]): "mov r8,[rcx+0x238]",
        bytes([0x4C, 0x8B, 0x81, 0x40, 0x02, 0x00, 0x00]): "mov r8,[rcx+0x240]",
    }
    hits = 0
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for pat, name in patterns.items():
            idx = 0
            while hits < 30:
                j = data.find(pat, idx)
                if j < 0:
                    break
                rva = base + j
                # look back 20 bytes for mov reg, [rip+global]
                start = max(0, j - 24)
                chunk = data[start:j]
                for insn in md.disasm(chunk, IMAGE + base + start):
                    if "rip" in insn.op_str and insn.mnemonic.startswith("mov"):
                        for op in insn.operands:
                            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                                tgt = (insn.address - IMAGE) + insn.size + op.mem.disp
                                print(
                                    f"  {name} @ 0x{rva:08X}  "
                                    f"prev load global 0x{tgt:08X}  "
                                    f"({insn.mnemonic} {insn.op_str})"
                                )
                                hits += 1
                idx = j + 1
                if hits >= 30:
                    break


if __name__ == "__main__":
    main()
