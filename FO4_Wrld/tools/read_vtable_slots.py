"""Read function pointers from a vtable in the target binary."""
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
    ap.add_argument("--vtable", type=lambda x: int(x, 0), required=True)
    ap.add_argument("--slots", type=int, default=80)
    ap.add_argument("--from-slot", type=int, default=0)
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=False)
    img = Image(args.exe)
    starts = {s for s, _ in img.functions()}

    raw = pe.get_data(args.vtable, args.slots * 8)
    print(f"vtable @ 0x{args.vtable:08X}")
    for i in range(args.from_slot, args.slots):
        va = struct.unpack_from("<Q", raw, i * 8)[0]
        if va < IMAGE_BASE:
            print(f"  [{i:3}] 0x{va:016X}  (not image VA)")
            continue
        rva = va - IMAGE_BASE
        mark = "FN" if rva in starts else "  "
        print(f"  [{i:3}] {mark} 0x{rva:08X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
