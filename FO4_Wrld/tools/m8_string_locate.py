"""Locate M8/NIF/skin pipeline functions on 1.10.163 via unique strings.

Pulls distinctive strings from re/M8P*.txt dossiers and from ni_offsets.h
comments, then maps each unique string to a function RVA on the target binary.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from fnfingerprint import Image  # noqa: E402
from strxref import StringXrefs  # noqa: E402

# Hand-picked from re/M8P* + ni_offsets.h — must be distinctive.
HINTS: dict[str, list[str]] = {
    # NIF loader public API / registration
    "NIF_LOAD_BY_PATH": ["BSSubIndexTriShape", "BSDynamicTriShape", "BSTriShape"],
    "NIF_FACTORY_REG": ["BSSubIndexTriShape"],
    # Skin
    "BSSKIN": ["BSSkin::Instance", "BSSkin::BoneData"],
    "BSSKIN_INSTANCE": ["BSSkin::Instance"],
    # Scene graph
    "BSFADENODE": ["BSFadeNode"],
    "BSLEAFANIM": ["BSLeafAnimNode"],
    # Materials
    "BGSM": [".bgsm", "BGSM"],
    # Attach / geometry names used by our inject path
    "BASE_MALE_BODY": ["BaseMaleBody"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--extra", nargs="*", default=[])
    args = ap.parse_args()

    print(f"loading {args.exe} ...")
    img = Image(args.exe)
    print("building string xrefs ...")
    xref = StringXrefs(img)
    print(f"  {xref.stats()}\n")

    # Also dump unique strings that look skin/nif related
    unique = xref.unique_strings()
    print("unique skin/nif-related strings (sample):")
    n = 0
    for s, fn in sorted(unique.items(), key=lambda kv: kv[0].lower()):
        sl = s.lower()
        if any(
            k in sl
            for k in (
                "skin",
                "bone",
                "nif",
                "fade",
                "trishape",
                "geometry",
                "bgsm",
                "load3d",
                "attach",
                "skeleton",
                "biped",
                "niav",
                "ninode",
            )
        ):
            print(f"  0x{fn:08X}  {s!r}")
            n += 1
            if n >= 60:
                print("  ...")
                break
    print()

    for name, strs in HINTS.items():
        print(f"=== {name} ===")
        for s in strs:
            fns = xref.by_string.get(s, set())
            uniq = s in unique
            print(f"  {s!r}: {len(fns)} fns  unique={uniq}")
            for fn in sorted(fns)[:8]:
                more = sorted(xref.strings_of(fn))[:6]
                print(f"      0x{fn:08X}  also={more}")
        print()

    for s in args.extra:
        fns = xref.by_string.get(s, set())
        print(f"EXTRA {s!r}: {len(fns)}")
        for fn in sorted(fns)[:12]:
            print(f"  0x{fn:08X}  {sorted(xref.strings_of(fn))[:8]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
