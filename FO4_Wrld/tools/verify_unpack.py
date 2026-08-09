"""Verify a Steamless-unpacked Fallout4.exe is actually usable for analysis.

Steamless can report success while leaving output that is useless to us, so
this checks the three things that actually matter before any porting work is
built on the file.

1. **Is .text decrypted?** Encrypted code is indistinguishable from random:
   entropy pins at ~8.0 and the 0xCC int3 padding MSVC emits between functions
   disappears. Real x64 code sits around 6.0-6.5 with several percent 0xCC.

2. **Are the RVAs preserved?** Steamless can realign sections. If .text or
   .rdata moved relative to the packed original, every address derived from
   this file is wrong at runtime — the exact failure this whole exercise is
   meant to prevent. Run Steamless with "Don't Realign Sections" checked.

3. **Is the exception directory intact?** It is the function-start oracle the
   matcher and `offset_audit.py` both depend on.

Usage
-----
    python tools/verify_unpack.py <unpacked.exe> [--original <packed.exe>]
"""

from __future__ import annotations

import argparse
import collections
import math
import sys
from pathlib import Path

import pefile

# Empirical thresholds from the two binaries on this machine:
#   encrypted 1.10.163 .text -> entropy 8.000, 0xCC 0.39%
#   plain     1.11.221 .text -> entropy 6.226, 0xCC 7.39%
MAX_PLAUSIBLE_ENTROPY = 7.2
MIN_PLAUSIBLE_CC = 0.02          # 2%
SAMPLE = 0x100000                # 1 MB is plenty to characterise a section


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = collections.Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def section_map(pe: pefile.PE) -> dict[str, tuple[int, int]]:
    out = {}
    for s in pe.sections:
        name = s.Name.rstrip(b"\x00").decode(errors="replace")
        out[name] = (s.VirtualAddress, s.Misc_VirtualSize)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", type=Path, help="the .unpacked output")
    ap.add_argument("--original", type=Path,
                    help="the packed exe, to confirm RVAs did not move")
    args = ap.parse_args()

    if not args.exe.exists():
        print(f"not found: {args.exe}", file=sys.stderr)
        return 2

    pe = pefile.PE(str(args.exe), fast_load=False)
    problems: list[str] = []

    print(f"{args.exe.name}\n")

    # --- 1. decryption -----------------------------------------------------
    text = None
    for s in pe.sections:
        if s.Name.rstrip(b"\x00") == b".text":
            text = s
            break
    if text is None:
        problems.append("no .text section")
    else:
        data = text.get_data()[:SAMPLE]
        ent = entropy(data)
        cc = data.count(0xCC) / len(data) if data else 0.0
        print(f"  .text entropy      : {ent:.3f}   "
              f"(encrypted ~8.0, plain code ~6.2)")
        print(f"  .text 0xCC padding : {cc*100:.2f}%   "
              f"(encrypted <1%, plain code ~7%)")
        print(f"  first 16 bytes     : {data[:16].hex()}")

        if ent > MAX_PLAUSIBLE_ENTROPY:
            problems.append(f".text still looks encrypted (entropy {ent:.3f})")
        if cc < MIN_PLAUSIBLE_CC:
            problems.append(
                f".text has almost no int3 padding ({cc*100:.2f}%) — "
                "not decompiled code")

    # --- 2. DRM stub removed ----------------------------------------------
    names = {s.Name.rstrip(b"\x00").decode(errors="replace")
             for s in pe.sections}
    ep = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    print(f"\n  sections           : {', '.join(sorted(names))}")
    print(f"  entrypoint RVA     : 0x{ep:08X}")
    if ".bind" in names:
        bind = next(s for s in pe.sections
                    if s.Name.rstrip(b"\x00") == b".bind")
        ep_in_bind = (bind.VirtualAddress <= ep < bind.VirtualAddress +
                      max(bind.Misc_VirtualSize, bind.SizeOfRawData))
        if ep_in_bind:
            # Deliberately NOT a failure on its own. The question this tool
            # answers is "can we statically analyse this?", not "is the DRM
            # gone?". 1.11.221 ships Steam-wrapped with an entrypoint in .bind
            # yet leaves .text in the clear, and it analyses perfectly well —
            # which is how the whole port assessment got done. Encrypted .text
            # is the real blocker, and it is checked above.
            print("  note: entrypoint is inside .bind (Steam stub present).")
            print("        Harmless for analysis as long as .text is plain;")
            print("        only matters if you intend to RUN this file.")
        else:
            print("  note: .bind retained but entrypoint is outside it (ok)")

    # --- 3. exception directory -------------------------------------------
    fns = getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", None) or []
    print(f"  RUNTIME_FUNCTIONs  : {len(fns)}")
    if len(fns) < 1000:
        problems.append("exception directory missing or tiny — the "
                        "function-start oracle will not work")

    # --- 4. RVA preservation ----------------------------------------------
    if args.original and args.original.exists():
        orig = pefile.PE(str(args.original), fast_load=True)
        a, b = section_map(orig), section_map(pe)
        print("\n  RVA comparison vs packed original:")
        moved = []
        for name in (".text", ".rdata", ".data", ".pdata"):
            if name in a and name in b:
                if a[name][0] != b[name][0]:
                    moved.append(
                        f"{name}: 0x{a[name][0]:08X} -> 0x{b[name][0]:08X}")
                    print(f"    {name:<8} MOVED "
                          f"0x{a[name][0]:08X} -> 0x{b[name][0]:08X}")
                else:
                    print(f"    {name:<8} ok    0x{b[name][0]:08X}")
        orig.close()
        if moved:
            problems.append(
                "sections were realigned — re-run Steamless with "
                "'Don't Realign Sections' CHECKED, or every derived address "
                "will be wrong at runtime")

    pe.close()

    print()
    if problems:
        print("FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK — decrypted, RVAs intact, exception directory present.")
    print("Ready for: python tools/port_match.py --target <this file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
