"""Fill worklists from known_lookup + matcher, PE-verify, write offsets.h."""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from known_lookup import MAP, load_db  # type: ignore  # noqa: E402

EXE = Path(r"C:\Games\Steam\steamapps\common\Fallout 4\Fallout4.exe.unpacked.exe")
WL_MIN = REPO / "docs" / "port_worklist_minimal.csv"
WL_FULL = REPO / "docs" / "port_worklist.csv"
MATCHED = REPO / "docs" / "port_matched.csv"


def load_matcher() -> dict[str, tuple[str, str]]:
    """name -> (ported_rva_hex, confidence) for exact/strong only."""
    out: dict[str, tuple[str, str]] = {}
    with MATCHED.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            conf = row.get("confidence") or ""
            if conf not in ("exact", "strong"):
                continue
            name = row.get("name") or ""
            addr = (row.get("ported_rva") or "").strip()
            if not name or not addr or ":" in name:
                continue
            out[name] = (addr if addr.startswith("0x") else f"0x{addr}", conf)
    return out


def fill_worklist(path: Path, ports: dict[str, tuple[str, str]]) -> int:
    """ports: name -> (hex_addr, verified_tag). Prefer higher-trust tags."""
    trust = {
        "matcher-exact": 3,
        "addresslib": 2,
        "matcher-strong": 1,
        "addresslib-unverified": 1,
        "commonlib": 0,
    }
    rows = []
    n = 0
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        fields = r.fieldnames or []
        for row in r:
            name = row.get("name") or ""
            if name in ports:
                new_addr, new_tag = ports[name]
                old_tag = (row.get("verified") or "").strip()
                # Prefer exact matcher over addresslib when both exist
                if not row.get("ported_addr") or trust.get(new_tag, 0) > trust.get(
                    old_tag, 0
                ):
                    row["ported_addr"] = new_addr
                    row["verified"] = new_tag
                    n += 1
            rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return n


def main() -> int:
    db = load_db(REPO / "tools" / "addresslib" / "offsets-1-10-163-0.csv")
    ports: dict[str, tuple[str, str]] = {}

    # Address library MAP
    for name, (rid, _note) in MAP.items():
        addr = db.get(rid)
        if addr is not None:
            # ENGINE_HEAP: prefer matcher-exact if present
            tag = "addresslib"
            ports[name] = (f"0x{addr:08X}", tag)

    # Matcher exact/strong overrides when higher trust
    for name, (addr, conf) in load_matcher().items():
        tag = f"matcher-{conf}"
        ports[name] = (addr if addr.startswith("0x") else f"0x{int(addr, 16):08X}", tag)

    # Force ENGINE_HEAP to matcher-exact if available (same source RVA identity)
    m = load_matcher()
    if "ENGINE_HEAP_ALLOC_RVA" in m:
        addr, conf = m["ENGINE_HEAP_ALLOC_RVA"]
        ports["ENGINE_HEAP_ALLOC_RVA"] = (
            addr if addr.startswith("0x") else f"0x{int(addr, 16):08X}",
            f"matcher-{conf}",
        )
    if "MEMMGR_ALLOC_RVA" in m:
        # alias — same source as ENGINE_HEAP on NG
        pass

    print("port set:")
    for k, (a, t) in sorted(ports.items()):
        print(f"  {k:36} {a}  [{t}]")

    for wl in (WL_MIN, WL_FULL):
        n = fill_worklist(wl, ports)
        print(f"filled/updated {n} rows in {wl.name}")

    # PE-verify + write
    cmd = [
        sys.executable,
        str(REPO / "tools" / "apply_ports.py"),
        "--exe",
        str(EXE),
        "--worklist",
        str(WL_MIN),
        "--write",
    ]
    print("\n" + " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    # known_lookup.load_db expects Path
    from known_lookup import load_db as _ld  # noqa

    raise SystemExit(main())
