"""Recover NIF_LOAD_BY_PATH + NIF_LOAD_WORKER on 1.10.163.

Strategy (M8P1 dossier):
  PC::Load3D = PlayerCharacter vt[134]
  That function (or Actor/REFR Load3D) calls the public NIF loader once.
  Public API NG was sub_1417B3E90; worker was sub_1417B3480 (callee).

Also: walk Gamebryo string xrefs carefully — 0x01BBAB10 is the *writer*
(header emit), not the loader. Prefer Load3D call-graph.

Usage:
  python tools/re_nif_load.py --exe <unpacked>
"""
from __future__ import annotations

import argparse
import bisect
import csv
import re
import struct
import sys
from collections import Counter
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE_BASE = 0x140000000
EXE_DEFAULT = Path(r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe")

# AddressLib VTABLE primary IDs (CommonLibF4 VTABLE_IDs.h)
PC_VT_ID = 1400465
ACTOR_VT_ID = 1455516
REFR_VT_ID = 179707
LOAD3D_SLOT = 134

# Known false positive: Gamebryo header *writer*
GAMEBRYO_WRITER = 0x01BBAB10


def load_addresslib(csv_path: Path) -> dict[int, int]:
    out: dict[int, int] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[int(row["id"])] = int(row["fo4_addr"], 16)
    return out


def owning(starts: list[int], ends: dict[int, int], rva: int) -> int | None:
    i = bisect.bisect_right(starts, rva) - 1
    if i >= 0 and rva < ends[starts[i]]:
        return starts[i]
    return None


def find_call_targets(pe: pefile.PE, fn_rva: int, fn_end: int) -> list[int]:
    """Return absolute RVAs of direct E8 call targets inside [fn_rva, fn_end)."""
    size = fn_end - fn_rva
    data = pe.get_data(fn_rva, size)
    out = []
    for i in range(len(data) - 5):
        if data[i] != 0xE8:
            continue
        disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
        tgt = fn_rva + i + 5 + disp
        out.append(tgt)
    return out


def find_callers(pe: pefile.PE, target: int) -> list[int]:
    out = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):
            continue
        data, base = s.get_data(), s.VirtualAddress
        for i in range(len(data) - 5):
            if data[i] != 0xE8:
                continue
            disp = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
            if base + i + 5 + disp == target:
                out.append(base + i)
    return out


def disasm(pe: pefile.PE, rva: int, size: int) -> list:
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    return list(md.disasm(pe.get_data(rva, size), IMAGE_BASE + rva))


def read_vt_slot(pe: pefile.PE, vt_rva: int, slot: int) -> int:
    raw = pe.get_data(vt_rva + slot * 8, 8)
    va = struct.unpack("<Q", raw)[0]
    return va - IMAGE_BASE


def is_code_fn_start(pe: pefile.PE, ends: dict[int, int], rva: int) -> bool:
    if rva not in ends:
        return False
    # PE section executable
    for s in pe.sections:
        if s.VirtualAddress <= rva < s.VirtualAddress + s.Misc_VirtualSize:
            return bool(s.Characteristics & 0x20000000)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=EXE_DEFAULT)
    ap.add_argument(
        "--addresslib",
        type=Path,
        default=Path(__file__).resolve().parent / "addresslib" / "offsets-1-10-163-0.csv",
    )
    args = ap.parse_args()

    print(f"loading PE {args.exe} ...")
    pe = pefile.PE(str(args.exe), fast_load=False)
    print("building function map ...")
    img = Image(args.exe)
    ends = {s: e for s, e in img.functions()}
    starts = sorted(ends)
    al = load_addresslib(args.addresslib)

    pc_vt = al[PC_VT_ID]
    actor_vt = al[ACTOR_VT_ID]
    refr_vt = al[REFR_VT_ID]
    print(f"PC vt=0x{pc_vt:08X}  Actor vt=0x{actor_vt:08X}  REFR vt=0x{refr_vt:08X}")

    pc_load = read_vt_slot(pe, pc_vt, LOAD3D_SLOT)
    actor_load = read_vt_slot(pe, actor_vt, LOAD3D_SLOT)
    refr_load = read_vt_slot(pe, refr_vt, LOAD3D_SLOT)
    print(f"PC::Load3D   vt[{LOAD3D_SLOT}] = 0x{pc_load:08X}  start={pc_load in ends} sz=0x{ends.get(pc_load,0)-pc_load:X}")
    print(f"Actor::Load3D vt[{LOAD3D_SLOT}] = 0x{actor_load:08X} start={actor_load in ends} sz=0x{ends.get(actor_load,0)-actor_load:X}")
    print(f"REFR::Load3D  vt[{LOAD3D_SLOT}] = 0x{refr_load:08X} start={refr_load in ends} sz=0x{ends.get(refr_load,0)-refr_load:X}")

    # Collect callees of all three Load3D variants
    load_fns = {
        "PC": pc_load,
        "Actor": actor_load,
        "REFR": refr_load,
    }
    all_callees: Counter[int] = Counter()
    per: dict[str, list[int]] = {}
    for name, fn in load_fns.items():
        if fn not in ends:
            print(f"  WARN {name} not a fn start")
            continue
        cals = find_call_targets(pe, fn, ends[fn])
        per[name] = cals
        for c in cals:
            all_callees[c] += 1
        print(f"  {name} direct callees: {len(cals)} unique={len(set(cals))}")

    # Public NIF loader fingerprint (from NG decomp / ni_offsets.h):
    #   u32(const char* path, NiAVObject** out, NifLoadOpts* opts)
    #   - medium-sized function
    #   - called from PC::Load3D (and often REFR/Actor paths transitively)
    #   - NOT Gamebryo writer 0x01BBAB10
    #   - typically near BSFadeNode wrap / FixedString create / pool alloc
    #
    # Score candidates that appear in PC Load3D callees first.

    FIXEDSTR_CREATE = 0x01B41E70
    MEM_ALLOC = 0x01B0EFD0
    BSFADE_CTOR = 0x01B983C0  # may be wrong — NiNode ctor; BSFade separate

    print("\n=== PC::Load3D callees (sizes) ===")
    pc_cals = sorted(set(per.get("PC", [])))
    scored: list[tuple[int, int, str]] = []
    for c in pc_cals:
        fn = owning(starts, ends, c)
        if fn is None:
            print(f"  call 0x{c:08X}  (not in map)")
            continue
        sz = ends[fn] - fn
        tag = ""
        if fn == GAMEBRYO_WRITER:
            tag = " [GAMEBRYO WRITER — skip]"
        # score: mid-size, has nested calls, called only a few times from Load3D
        print(f"  call 0x{c:08X} -> fn 0x{fn:08X} size=0x{sz:X}{tag}")
        scored.append((fn, sz, tag))

    # Walk 1-level deeper: REFR Load3D is the real worker in NG.
    print("\n=== REFR::Load3D callees (filter size 0x80..0x2000, skip tiny) ===")
    refr_candidates = []
    for c in sorted(set(per.get("REFR", []))):
        fn = owning(starts, ends, c)
        if fn is None:
            continue
        sz = ends[fn] - fn
        if sz < 0x80 or sz > 0x4000:
            continue
        if fn == GAMEBRYO_WRITER:
            continue
        ncallers = len(find_callers(pe, fn))  # expensive but OK once
        print(f"  0x{fn:08X} size=0x{sz:X} callers={ncallers}")
        refr_candidates.append((fn, sz, ncallers))

    # Prefer candidates also reachable from PC (transitively via Actor)
    print("\n=== PC callees that themselves call something large (1 hop) ===")
    # From PC Load3D decomp: after path build, direct call to nif loader.
    # Inspect medium-sized PC callees that take path-like args.
    for fn, sz, tag in scored:
        if tag or sz < 0x40 or sz > 0x800:
            continue
        nested = find_call_targets(pe, fn, ends[fn])
        print(f"\n-- disasm candidate 0x{fn:08X} size=0x{sz:X} nested_calls={len(nested)} --")
        insns = disasm(pe, fn, min(sz, 0x180))
        for i, insn in enumerate(insns[:60]):
            extra = ""
            if insn.mnemonic == "call" and insn.op_str.startswith("0x"):
                try:
                    t = int(insn.op_str, 16) - IMAGE_BASE
                    extra = f"  ; -> 0x{t:08X}"
                except ValueError:
                    pass
            print(f"  {insn.address - IMAGE_BASE:08X}: {insn.mnemonic:8} {insn.op_str}{extra}")
            if insn.mnemonic in ("ret", "retn"):
                break

    # Also: find functions that call FixedString create AND are called from Load3D chain
    print("\n=== Load3D-chain ∩ FixedString-CREATE callers ===")
    create_sites = find_callers(pe, FIXEDSTR_CREATE)
    create_fns = {owning(starts, ends, s) for s in create_sites}
    create_fns.discard(None)
    chain = set()
    for name, cals in per.items():
        for c in cals:
            fn = owning(starts, ends, c)
            if fn:
                chain.add(fn)
    # expand one hop from REFR
    for c in per.get("REFR", []):
        fn = owning(starts, ends, c)
        if fn and fn in ends:
            for c2 in find_call_targets(pe, fn, ends[fn]):
                f2 = owning(starts, ends, c2)
                if f2:
                    chain.add(f2)

    overlap = sorted(chain & create_fns)
    print(f"  overlap count={len(overlap)}")
    for fn in overlap[:40]:
        sz = ends[fn] - fn
        print(f"  0x{fn:08X} size=0x{sz:X}")

    # String path: "Gamebryo File Format" — loader may compare header, writer emits it.
    print("\n=== xrefs to Gamebryo File Format string ===")
    needle = b"Gamebryo File Format"
    str_rvas = []
    for s in pe.sections:
        data, base = s.get_data(), s.VirtualAddress
        idx = 0
        while True:
            i = data.find(needle, idx)
            if i < 0:
                break
            str_rvas.append(base + i)
            idx = i + 1
    print(f"  string @ {[hex(x) for x in str_rvas]}")

    # LEA rip-rel to string
    for sr in str_rvas:
        for s in pe.sections:
            if not (s.Characteristics & 0x20000000):
                continue
            data, base = s.get_data(), s.VirtualAddress
            for m in re.finditer(rb"[\x48\x4C]\x8D[\x05\x0D\x15\x1D\x25\x2D\x35\x3D]", data):
                off = m.start()
                if off + 7 > len(data):
                    continue
                disp = int.from_bytes(data[off + 3 : off + 7], "little", signed=True)
                if base + off + 7 + disp == sr:
                    site = base + off
                    fn = owning(starts, ends, site)
                    print(f"  lea site 0x{site:08X} fn=0x{fn:08X} sz=0x{ends[fn]-fn:X}" if fn else f"  lea site 0x{site:08X}")

    print("\n=== DONE first pass — review candidates above ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
