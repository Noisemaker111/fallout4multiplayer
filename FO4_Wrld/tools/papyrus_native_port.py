"""Find Papyrus native function pointers by name string in Fallout4.exe.

Papyrus natives are registered with a name string nearby the function pointer
in .rdata. We scan for the ASCII name, then look for nearby absolute pointers
into .text that land on function starts.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnfingerprint import Image  # noqa: E402

IMAGE_BASE = 0x140000000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument(
        "--names",
        nargs="+",
        default=[
            "PlaceAtMe",
            "AddItem",
            "RemoveItem",
            "Lock",
            "Unlock",
            "SetValue",
            "SetCurrentStageID",
        ],
    )
    ap.add_argument("--window", type=lambda x: int(x, 0), default=0x80)
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    starts = {s for s, _ in img.functions()}

    # Collect all non-exec section bytes with their RVAs
    sections = []
    for s in pe.sections:
        execu = bool(s.Characteristics & 0x20000000)
        if execu:
            continue
        data = s.get_data()
        sections.append((s.VirtualAddress, data))

    text_lo = text_hi = None
    for s in pe.sections:
        if s.Characteristics & 0x20000000:
            lo = s.VirtualAddress
            hi = lo + max(s.Misc_VirtualSize, s.SizeOfRawData)
            if text_lo is None:
                text_lo, text_hi = lo, hi
            else:
                text_lo = min(text_lo, lo)
                text_hi = max(text_hi, hi)

    for name in args.names:
        needle = name.encode("ascii") + b"\x00"
        hits = []
        for sec_rva, data in sections:
            start = 0
            while True:
                i = data.find(needle, start)
                if i < 0:
                    break
                hits.append(sec_rva + i)
                start = i + 1
        print(f"\n=== {name!r}: {len(hits)} string hits ===")
        for hrva in hits[:6]:
            print(f"  string @ 0x{hrva:08X}")
            # Scan ±window for absolute VAs pointing into .text function starts
            candidates = []
            for sec_rva, data in sections:
                # only scan around the string's section
                if not (sec_rva <= hrva < sec_rva + len(data)):
                    continue
                off = hrva - sec_rva
                lo = max(0, off - args.window)
                hi = min(len(data) - 8, off + args.window)
                for p in range(lo & ~7, hi, 8):
                    raw = data[p : p + 8]
                    if len(raw) < 8:
                        continue
                    va = struct.unpack("<Q", raw)[0]
                    if va < IMAGE_BASE:
                        continue
                    rva = va - IMAGE_BASE
                    if text_lo <= rva < text_hi and rva in starts:
                        candidates.append((sec_rva + p, rva))
            # dedupe by target
            seen = set()
            for slot, rva in candidates:
                if rva in seen:
                    continue
                seen.add(rva)
                print(f"    ptr @ 0x{slot:08X} -> fn 0x{rva:08X}")
            if not candidates:
                print("    (no nearby .text function pointers)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
