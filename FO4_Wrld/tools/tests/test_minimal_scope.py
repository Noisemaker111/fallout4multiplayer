"""Tests for the FW_MINIMAL scoping logic in the port tooling.

Why these exist: the minimal work-list is what decides how much manual IDA work
Milestone 1 needs, and two pieces of logic can silently corrupt that number.

  1. `strip_non_minimal_regions` interprets `#if FW_MINIMAL` nesting. If it
     drops the wrong branch, addresses that ARE compiled fall off the list and
     nobody re-derives them — the DLL then writes a detour into whatever sits
     at a stale address.

  2. `parse_inline_rvas` attributes each RVA to the first file that mentions it.
     Filtering excluded files AFTER that dedup would drop addresses a surviving
     file also uses. The regression test below pins the ordering.

Run: ./.venv/Scripts/python.exe -m pytest tools/tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from offset_audit import parse_inline_rvas, strip_non_minimal_regions  # noqa: E402
from port_assess import load_minimal_excluded_files  # noqa: E402


def _lines(text: str) -> list[str]:
    """Surviving content lines, ignoring blanks and preprocessor directives.

    Directives are not always blanked: the stripper short-circuits on text that
    never mentions FW_MINIMAL and returns it verbatim. What matters for the
    address scan is which *content* lines survive, so compare only those.
    """
    return [l for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


class TestStripNonMinimalRegions:
    def test_negated_guard_drops_body(self):
        out = strip_non_minimal_regions("A\n#if !FW_MINIMAL\nDROP\n#endif\nB\n")
        assert _lines(out) == ["A", "B"]

    def test_plain_guard_keeps_body(self):
        out = strip_non_minimal_regions("#if FW_MINIMAL\nKEEP\n#endif\n")
        assert _lines(out) == ["KEEP"]

    def test_stub_pattern_keeps_stub_drops_real(self):
        """The shape used by every stubbed header in fw_native/src."""
        out = strip_non_minimal_regions(
            "#if FW_MINIMAL\nSTUB\n#else\nREAL\n#endif\n")
        assert _lines(out) == ["STUB"]

    def test_inverted_stub_pattern(self):
        out = strip_non_minimal_regions(
            "#if !FW_MINIMAL\nREAL\n#else\nSTUB\n#endif\n")
        assert _lines(out) == ["STUB"]

    def test_unrelated_conditional_keeps_both_branches(self):
        """Only FW_MINIMAL is interpreted; this is a scanner, not a compiler."""
        out = strip_non_minimal_regions("#ifdef _WIN32\nX\n#else\nY\n#endif\n")
        assert _lines(out) == ["X", "Y"]

    def test_unrelated_nested_inside_dropped_region_stays_dropped(self):
        out = strip_non_minimal_regions(
            "#if !FW_MINIMAL\n#ifdef Q\nD1\n#else\nD2\n#endif\n#endif\nAFTER\n")
        assert _lines(out) == ["AFTER"]

    def test_minimal_guard_nested_inside_unrelated_conditional(self):
        out = strip_non_minimal_regions(
            "#ifdef Q\n#if !FW_MINIMAL\nDROP\n#endif\nKEEP\n#endif\n")
        assert _lines(out) == ["KEEP"]

    def test_line_numbers_preserved(self):
        """Regions are blanked, not deleted, so reported lines stay honest."""
        src = "A\n#if !FW_MINIMAL\nDROP\n#endif\nB\n"
        assert len(strip_non_minimal_regions(src).splitlines()) == len(src.splitlines())

    def test_text_without_the_macro_is_untouched(self):
        src = "sub_140ABCDEF\n#ifdef X\ny\n#endif\n"
        assert strip_non_minimal_regions(src) == src

    def test_spacing_variants(self):
        for guard in ("#if !FW_MINIMAL", "#  if  ! FW_MINIMAL", "# if !FW_MINIMAL"):
            out = strip_non_minimal_regions(f"{guard}\nDROP\n#endif\nKEEP\n")
            assert _lines(out) == ["KEEP"], guard


class TestInlineRvaScoping:
    def test_skip_filters_before_dedup(self, tmp_path: Path):
        """An RVA in both an excluded and a kept file must survive.

        `aaa_excluded.cpp` sorts first, so a naive implementation attributes the
        RVA to it and then drops it — losing an address `zzz_kept.cpp` needs.
        """
        (tmp_path / "aaa_excluded.cpp").write_text("sub_140ABCDEF\n")
        (tmp_path / "zzz_kept.cpp").write_text("sub_140ABCDEF\n")

        found = parse_inline_rvas(tmp_path, set(), skip={"aaa_excluded.cpp"})
        assert [e.rva for e in found] == [0x0ABCDEF]
        assert found[0].name.startswith("zzz_kept.cpp")

    def test_known_table_rvas_are_not_double_counted(self, tmp_path: Path):
        (tmp_path / "a.cpp").write_text("sub_140ABCDEF\n")
        assert parse_inline_rvas(tmp_path, {0x0ABCDEF}) == []

    def test_minimal_flag_drops_guarded_regions(self, tmp_path: Path):
        (tmp_path / "a.cpp").write_text(
            "sub_140AAAAAA\n#if !FW_MINIMAL\nsub_140BBBBBB\n#endif\n")

        full = {e.rva for e in parse_inline_rvas(tmp_path, set())}
        minimal = {e.rva for e in parse_inline_rvas(tmp_path, set(), minimal=True)}

        assert full == {0x0AAAAAA, 0x0BBBBBB}
        assert minimal == {0x0AAAAAA}


class TestExcludeList:
    def test_every_stem_resolves_to_a_real_source_file(self):
        """CMake fails the configure on a bad stem; catch it here first."""
        src = REPO_ROOT / "fw_native" / "src"
        missing = [f for f in load_minimal_excluded_files()
                   if f.endswith(".cpp") and not (src / f).exists()]
        assert missing == []

    def test_list_is_pure_ascii(self):
        """CMake's file(STRINGS) splits a line at the first non-ASCII byte,
        which turns a comment into a bogus module stem. See START_HERE section 6."""
        raw = (REPO_ROOT / "fw_native" / "minimal_exclude.txt").read_bytes()
        assert all(b < 128 for b in raw)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
