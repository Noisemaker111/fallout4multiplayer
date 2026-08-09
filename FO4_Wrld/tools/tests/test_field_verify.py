"""Tests for the CommonLibF4 layout parser behind tools/field_verify.py.

These matter more than they look. Every failure mode of this parser is SILENT
and reports as "verified": if a class is misidentified, its members land under
the wrong key, the MAP lookup misses, and the constant is reported UNMAPPED
rather than wrong. Two real bugs found while writing it, both pinned below:

  * `class __declspec(novtable) TESForm` recorded the class as "__declspec",
    so every core TESForm/TESObjectREFR field silently failed to resolve.
  * Tracking "the last class seen" instead of brace depth filed TESForm's
    members under whatever nested functor was declared last.

Run: ./.venv/Scripts/python.exe -m pytest tools/tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from field_verify import Layout, parse_commonlib, resolve  # noqa: E402


def _lib(tmp_path: Path, **headers: str) -> Layout:
    inc = tmp_path / "include"
    inc.mkdir(parents=True, exist_ok=True)
    for name, text in headers.items():
        (inc / f"{name}.h").write_text(text, encoding="utf-8")
    return parse_commonlib(tmp_path)


class TestClassDetection:
    def test_declspec_attribute_is_not_the_class_name(self, tmp_path):
        lay = _lib(tmp_path, a="""
	class __declspec(novtable) TESForm :
		public BaseFormComponent
	{
	public:
		// members
		std::uint32_t formID;  // 14
	};
	static_assert(sizeof(TESForm) == 0x20);
""")
        assert lay.members.get("TESForm.formID") == 0x14
        assert not any(k.startswith("__declspec") for k in lay.members)

    def test_alignas_attribute_is_skipped(self, tmp_path):
        lay = _lib(tmp_path, a="""
	struct alignas(16) NiPoint3A
	{
		float x;  // 00
	};
""")
        assert lay.members.get("NiPoint3A.x") == 0x00

    def test_nested_class_does_not_steal_outer_members(self, tmp_path):
        """The bug that motivated brace tracking."""
        lay = _lib(tmp_path, a="""
	class Outer
	{
	public:
		struct Nested
		{
			std::uint32_t inner;  // 04
		};

		// members
		std::uint32_t outerField;  // 20
	};
""")
        assert lay.members.get("Nested.inner") == 0x04
        assert lay.members.get("Outer.outerField") == 0x20

    def test_forward_declaration_is_ignored(self, tmp_path):
        lay = _lib(tmp_path, a="""
	class BGSInventoryList;

	class Real
	{
		std::uint32_t f;  // 08
	};
""")
        assert lay.members.get("Real.f") == 0x08
        assert not any(k.startswith("BGSInventoryList.") for k in lay.members)

    def test_sizeof_assert_is_collected(self, tmp_path):
        lay = _lib(tmp_path, a="""
	class BGSInventoryItem
	{
		TESBoundObject* object;  // 00
	};
	static_assert(sizeof(BGSInventoryItem) == 0x10);
""")
        assert lay.sizes.get("BGSInventoryItem") == 0x10


class TestResolve:
    @pytest.fixture
    def layout(self):
        return Layout(
            members={
                "TESObjectREFR.data": 0xC0,
                "OBJ_REFR.location": 0x10,
                "NiAVObject.local": 0x30,
                "NiTransform.translate": 0x30,
            },
            sizes={},
        )

    def test_single_member(self, layout):
        assert resolve("TESObjectREFR.data", layout) == 0xC0

    def test_composed_members(self, layout):
        """REFR.data(0xC0) + OBJ_REFR.location(0x10) == POS_OFF 0xD0."""
        assert resolve("TESObjectREFR.data + OBJ_REFR.location", layout) == 0xD0

    def test_composed_with_literal(self, layout):
        assert resolve("NiAVObject.local + NiTransform.translate", layout) == 0x60
        assert resolve("NiAVObject.local + 0x10", layout) == 0x40

    def test_unknown_symbol_returns_none_rather_than_zero(self, layout):
        """A missing symbol must not silently resolve to 0 and 'confirm'."""
        assert resolve("Nope.missing", layout) is None
        assert resolve("NiAVObject.local + Nope.missing", layout) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
