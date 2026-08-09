"""Identify BSFixedString create/assign/dtor by analyzing the Entry::release cluster."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE_BASE = 0x140000000
RELEASE = 0x01B42FD0  # AddressLib Entry::release


def resolve_call(insn, insn_rva: int) -> int | None:
    if insn.mnemonic != "call":
        return None
    # call rel32 encoded as E8
    if insn.bytes[0] == 0xE8 and len(insn.bytes) == 5:
        disp = int.from_bytes(insn.bytes[1:5], "little", signed=True)
        return insn_rva + 5 + disp
    return None


def analyze(pe, img, rva: int) -> dict:
    ends = {s: e for s, e in img.functions()}
    end = ends.get(rva)
    if not end:
        return {}
    size = end - rva
    data = pe.get_data(rva, size)
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    calls = []
    has_rep = False
    has_strlen_like = False
    movs_to_mem = 0
    cmp_null = 0
    for insn in md.disasm(data, IMAGE_BASE + rva):
        irva = insn.address - IMAGE_BASE
        t = resolve_call(insn, irva)
        if t is not None:
            calls.append(t)
        if insn.mnemonic in ("rep", "repne") or "scas" in insn.mnemonic or "movs" in insn.mnemonic:
            has_rep = True
        if insn.mnemonic == "cmp" and ("0" in insn.op_str or "rcx" in insn.op_str):
            cmp_null += 1
        if insn.mnemonic.startswith("mov") and "[" in insn.op_str.split(",")[0]:
            movs_to_mem += 1
    return {
        "rva": rva,
        "size": size,
        "n_calls": len(calls),
        "calls_release": sum(1 for c in calls if c == RELEASE),
        "other_calls": [c for c in calls if c != RELEASE],
        "has_rep": has_rep,
        "cmp_null": cmp_null,
        "movs_to_mem": movs_to_mem,
        "all_calls": calls,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    args = ap.parse_args()
    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)

    # find all callers of RELEASE
    callers_insn = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data = s.get_data()
        base = s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] == 0xE8:
                disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
                if base + i + 5 + disp == RELEASE:
                    callers_insn.append(base + i)

    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)
    import bisect

    fns = set()
    for r in callers_insn:
        i = bisect.bisect_right(starts, r) - 1
        if i >= 0 and r < ends[starts[i]]:
            fns.add(starts[i])

    print(f"functions calling Entry::release: {len(fns)}\n")
    rows = [analyze(pe, img, fn) for fn in sorted(fns)]
    rows.sort(key=lambda r: (r.get("size", 0), r.get("rva", 0)))

    for r in rows:
        if not r:
            continue
        oc = " ".join(f"0x{c:X}" for c in r["other_calls"][:4])
        print(
            f"0x{r['rva']:08X} sz=0x{r['size']:03X} "
            f"rel_calls={r['calls_release']} other={r['n_calls']-r['calls_release']} "
            f"rep={r['has_rep']} cmp0={r['cmp_null']} store={r['movs_to_mem']} "
            f"-> [{oc}]"
        )

    # Heuristics:
    # - dtor: 1x release, small, no other interesting calls
    # - create from cstr: may call GetEntry/intern (other call), stores to out ptr
    # - assign: 2x release or release+acquire pattern
    print("\n--- classification guess ---")
    for r in rows:
        if not r:
            continue
        role = "?"
        if r["calls_release"] == 1 and r["n_calls"] == 1 and r["size"] < 0x50:
            role = "dtor/simple-release"
        elif r["calls_release"] >= 1 and r["other_calls"] and r["size"] < 0x100:
            role = "create-or-assign (calls intern?)"
        elif r["calls_release"] == 2 and not r["other_calls"]:
            role = "assign/copy (double release path)"
        elif r["size"] > 0x100:
            role = "large (unlikely pure fixedstr)"
        print(f"  0x{r['rva']:08X}  {role}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
