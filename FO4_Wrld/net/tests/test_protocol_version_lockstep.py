"""The wire version must be identical in Python, C++, and the Steam lobby.

A silent mismatch is the worst failure mode this project has: both sides
start, both sides think they are fine, and the session desyncs in ways that
look like gameplay bugs. Since fw_launcher now advertises the protocol
version as Steam lobby data (so a joiner is rejected *before* Fallout 4
launches), there are three copies of the number to keep honest.

fw_native/src/net/protocol_version.h is the single source of truth for the
C++ side; protocol.h static_asserts against it and fw_launcher includes it
directly. This test pins the Python side to the same value.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from net.protocol import PROTOCOL_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_HEADER = REPO_ROOT / "fw_native" / "src" / "net" / "protocol_version.h"


def _read_native_version() -> int:
    text = VERSION_HEADER.read_text(encoding="utf-8")
    match = re.search(r"#define\s+FW_PROTOCOL_VERSION\s+(\d+)", text)
    assert match, f"FW_PROTOCOL_VERSION not found in {VERSION_HEADER}"
    return int(match.group(1))


def test_version_header_exists() -> None:
    assert VERSION_HEADER.is_file(), (
        f"{VERSION_HEADER} is missing; fw_launcher includes it to advertise "
        "the protocol version in Steam lobby data"
    )


def test_python_and_native_protocol_versions_match() -> None:
    native = _read_native_version()
    assert native == PROTOCOL_VERSION, (
        f"protocol version drift: net/protocol.py says {PROTOCOL_VERSION}, "
        f"{VERSION_HEADER.name} says {native}. Bump both in the same commit."
    )


def test_protocol_header_uses_the_shared_macro() -> None:
    """protocol.h must not re-declare the number, or the macro is decorative."""
    protocol_h = REPO_ROOT / "fw_native" / "src" / "net" / "protocol.h"
    text = protocol_h.read_text(encoding="utf-8")
    match = re.search(
        r"constexpr\s+std::uint8_t\s+PROTOCOL_VERSION\s*=\s*([^;]+);", text
    )
    assert match, "PROTOCOL_VERSION definition not found in protocol.h"
    assert "FW_PROTOCOL_VERSION" in match.group(1), (
        "protocol.h hardcodes the protocol version instead of using "
        "FW_PROTOCOL_VERSION from protocol_version.h"
    )


@pytest.mark.parametrize("peer_id", ["s1", "skxuogunoclj", "s3w5e11264sg0f"])
def test_steam_derived_peer_ids_are_acceptable_to_the_server(peer_id: str) -> None:
    """fw_launcher derives client_id from SteamID64 as 's' + base36.

    net/server/state.py accept_peer() caps ids at MAX_CLIENT_ID_LEN and only
    allows [A-Za-z0-9_-]. base36 of any uint64 is at most 13 characters, so
    the prefixed form is at most 14 - this pins that headroom so a future
    identity change cannot silently start getting peers rejected.
    """
    from net.protocol import MAX_CLIENT_ID_LEN

    assert len(peer_id) <= MAX_CLIENT_ID_LEN
    assert peer_id.isascii()
    assert all(c.isalnum() or c in "_-" for c in peer_id)


def test_max_uint64_peer_id_still_fits() -> None:
    from net.protocol import MAX_CLIENT_ID_LEN

    def to_base36(value: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        if value == 0:
            return "0"
        out = ""
        while value:
            value, rem = divmod(value, 36)
            out = digits[rem] + out
        return out

    worst_case = "s" + to_base36(2**64 - 1)
    assert len(worst_case) <= MAX_CLIENT_ID_LEN, worst_case
