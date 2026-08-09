"""Automated RE for Tier A residual addresses on 1.10.163."""
from __future__ import annotations

import argparse
import bisect
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402
from strxref import StringXrefs  # noqa: E402

IMAGE_BASE = 0x140000000
CREATE = 0x01B41E70  # BSFixedString create from cstr (identified)
RELEASE = 0x01B42FD0
INTERN = 0x01B43DB0  # GetEntry-ish called by create


def find_callers(pe: pefile.PE, target: int) -> list[int]:
    out = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data = s.get_data()
        base = s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] == 0xE8:
                disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
                if base + i + 5 + disp == target:
                    out.append(base + i)
    return out


def owning_fn(starts, ends, rva):
    i = bisect.bisect_right(starts, rva) - 1
    if i >= 0 and rva < ends[starts[i]]:
        return starts[i]
    return None


def disasm_fn(pe, img, rva, max_insns=60):
    ends = {s: e for s, e in img.functions()}
    end = ends.get(rva)
    if not end:
        return []
    data = pe.get_data(rva, min(end - rva, 0x800))
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    return list(md.disasm(data, IMAGE_BASE + rva))[:max_insns]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)

    print("=== FIXEDSTR CREATE (confirmed by pattern) ===")
    print(f"  FIXEDSTR_CREATE_RVA = 0x{CREATE:08X}")
    print(f"  FIXEDSTR_RELEASE_RVA = 0x{RELEASE:08X}")
    print(f"  INTERN/GetEntry = 0x{INTERN:08X}")

    # SetName: functions that store to [reg+0x10] and call create or release
    print("\n=== hunting SETNAME (store +0x10 + fixedstr calls) ===")
    create_callers = find_callers(pe, CREATE)
    release_callers = find_callers(pe, RELEASE)
    print(f"  create call sites: {len(create_callers)}")
    print(f"  release call sites: {len(release_callers)}")

    # For each function that calls CREATE, disasm and look for +0x10 stores
    setname_candidates = []
    for site in create_callers:
        fn = owning_fn(starts, ends, site)
        if not fn:
            continue
        insns = disasm_fn(pe, img, fn, 100)
        text = "\n".join(f"{i.mnemonic} {i.op_str}" for i in insns)
        # look for + 0x10 or +0x10 in mem ops
        if "+ 0x10]" in text or "+0x10]" in text.replace(" ", ""):
            setname_candidates.append((fn, ends[fn] - fn))
        # also: mov [reg+0x10], ...
        for i in insns:
            if i.mnemonic.startswith("mov") and "0x10" in i.op_str and "[" in i.op_str:
                if i.op_str.find("[") < i.op_str.find(","):
                    setname_candidates.append((fn, ends[fn] - fn))
                    break

    # unique
    seen = set()
    print("  create-callers that touch +0x10:")
    for fn, sz in sorted(set(setname_candidates)):
        if fn in seen:
            continue
        seen.add(fn)
        print(f"    0x{fn:08X} size=0x{sz:X}")
        for insn in disasm_fn(pe, img, fn, 35):
            print(f"      {insn.address-IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}")

    # Also search ALL functions that write [*-0x10] with a small size and call release
    print("\n=== small fns calling release with +0x10 store ===")
    rel_fns = Counter()
    for site in release_callers:
        fn = owning_fn(starts, ends, site)
        if fn:
            rel_fns[fn] += 1
    for fn, n in rel_fns.most_common(40):
        sz = ends[fn] - fn
        if sz > 0x120:
            continue
        insns = disasm_fn(pe, img, fn, 50)
        hits = [
            i
            for i in insns
            if i.mnemonic.startswith("mov")
            and "0x10" in i.op_str
            and "[" in i.op_str.split(",")[0]
        ]
        if hits:
            print(f"  0x{fn:08X} sz=0x{sz:X} rel_calls={n} stores+10={len(hits)}")
            for i in disasm_fn(pe, img, fn, 40):
                mark = " <<<" if "0x10" in i.op_str else ""
                print(f"    {i.address-IMAGE_BASE:08X}: {i.mnemonic:8} {i.op_str}{mark}")

    # NIF / SSN via strings
    print("\n=== string seeds ===")
    xref = StringXrefs(img)
    for s in (
        "ShadowSceneNode",
        "BSFadeNode",
        "BSSubIndexTriShape",
        "NiNode",
        ".nif",
        "LoadNif",
        "Gamebryo",
    ):
        fns = xref.by_string.get(s, set())
        print(f"  {s!r}: {len(fns)} fns")
        for fn in sorted(fns)[:6]:
            print(f"      0x{fn:08X}  strs={sorted(xref.strings_of(fn))[:5]}")

    # RTTI-style strings
    print("\n=== RTTI-ish unique strings ===")
    for s, fn in sorted(xref.unique_strings().items()):
        if "ShadowScene" in s or "SceneGraph" in s or "FadeNode" in s:
            print(f"  0x{fn:08X}  {s!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
