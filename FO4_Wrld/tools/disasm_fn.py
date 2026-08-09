"""Disassemble a function (or list of functions) from Fallout4.exe with capstone."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CS_OP_IMM, CS_OP_MEM

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE_BASE = 0x140000000


def disasm_range(pe: pefile.PE, rva: int, size: int, md: Cs) -> list:
    data = pe.get_data(rva, size)
    return list(md.disasm(data, IMAGE_BASE + rva))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--rva", type=lambda x: int(x, 0), nargs="+", required=True)
    ap.add_argument("--max-insns", type=int, default=80)
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    ends = {s: e for s, e in img.functions()}
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True

    for rva in args.rva:
        end = ends.get(rva)
        if end is None:
            # find enclosing
            for s, e in img.functions():
                if s <= rva < e:
                    rva, end = s, e
                    break
        if end is None:
            print(f"\n=== 0x{rva:08X} NOT A FUNCTION START ===")
            continue
        size = min(end - rva, 0x400)
        print(f"\n=== 0x{rva:08X} size=0x{size:X} ===")
        insns = disasm_range(pe, rva, size, md)
        for i, insn in enumerate(insns[: args.max_insns]):
            extra = ""
            if insn.mnemonic == "call":
                # try resolve relative
                if insn.op_str.startswith("0x"):
                    extra = f"  ; call"
            print(f"  {insn.address - IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}{extra}")
            if insn.mnemonic == "ret" or insn.mnemonic == "retn":
                break
        if len(insns) > args.max_insns:
            print(f"  ... ({len(insns)} total insns)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
