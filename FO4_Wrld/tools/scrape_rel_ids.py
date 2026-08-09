"""Scrape CommonLibF4 REL::ID usages into a searchable table."""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

_ID = re.compile(r"REL::ID\((\d+)\)")
_CLASS = re.compile(
    r"^\s*(?:class|struct)\s+(?:(?:__declspec|alignas)\s*\([^)]*\)\s*)*(\w+)"
)
_FUNC = re.compile(
    r"^\s*(?:\[\[.*?\]\]\s*)*"
    r"(?:static\s+|virtual\s+|inline\s+|constexpr\s+|explicit\s+)*"
    r"(?:[\w:<>\*&,\s]+?\s+)?"
    r"(\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?(?:noexcept\s*)?[{;]?\s*$"
)


def scrape(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.rglob("*.h")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        cls = ""
        func = ""
        depth = 0
        pending_cls: str | None = None
        stack: list[tuple[str, int]] = []
        for i, line in enumerate(lines):
            m = _CLASS.match(line)
            if m and not line.rstrip().endswith(";"):
                pending_cls = m.group(1)
            opens = line.count("{")
            closes = line.count("}")
            if opens:
                depth += opens
                if pending_cls:
                    stack.append((pending_cls, depth))
                    cls = pending_cls
                    pending_cls = None
            if closes:
                depth -= closes
                while stack and stack[-1][1] > depth:
                    stack.pop()
                cls = stack[-1][0] if stack else ""

            fm = _FUNC.match(line)
            if fm and not line.strip().startswith("//"):
                func = fm.group(1)

            for im in _ID.finditer(line):
                rows.append(
                    {
                        "id": int(im.group(1)),
                        "class": cls,
                        "func": func,
                        "file": path.relative_to(root).as_posix(),
                        "line": i + 1,
                        "text": line.strip()[:120],
                    }
                )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commonlib", type=Path, required=True)
    ap.add_argument("-o", type=Path, default=None)
    ap.add_argument("--grep", default="", help="substring filter on class/func/text")
    args = ap.parse_args()
    inc = args.commonlib / "CommonLibF4" / "include"
    if not inc.is_dir():
        inc = args.commonlib / "include"
    rows = scrape(inc)
    g = args.grep.lower()
    if g:
        rows = [
            r
            for r in rows
            if g in r["class"].lower()
            or g in r["func"].lower()
            or g in r["text"].lower()
            or g in r["file"].lower()
        ]
    if args.o:
        with args.o.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            if rows:
                w.writeheader()
                w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {args.o}")
    else:
        for r in rows:
            print(
                f"{r['id']:8}  {r['class'] or '-':28}  {r['func'] or '-':32}  "
                f"{r['file']}:{r['line']}"
            )
        print(f"\n{len(rows)} hits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
