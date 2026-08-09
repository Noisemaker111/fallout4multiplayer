// Small shared helpers for FoM.exe. Extracted from main.cpp so the Steam
// and bridge modules can log / touch paths without duplicating boilerplate.

#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <cstdint>
#include <string>

namespace fom {

// stdout logger. Same wide-printf semantics main.cpp always used.
void logw(const wchar_t* fmt, ...);

std::wstring exe_dir();
std::wstring join_path(const std::wstring& a, const std::wstring& b);
bool         file_exists(const std::wstring& p);

std::string  narrow(const std::wstring& w);
std::wstring widen(const std::string& s);

// %LOCALAPPDATA%\FoM (created on demand). Empty string on failure.
std::wstring appdata_dir();

// Monotonic milliseconds (GetTickCount64).
std::uint64_t now_ms();

// SteamID64 -> a peer_id that satisfies the server's rules
// (net/server/state.py accept_peer: <=15 chars, [A-Za-z0-9_-]).
//
// base36 of a real SteamID64 (~7.65e16) is 11 chars, so "s" + base36
// gives a 12-char id with room to spare. Deterministic and collision-free
// because the mapping is injective over uint64.
std::string steam_peer_id(std::uint64_t steam_id64);

// base36 encode/decode for session codes (lobby IDs shown to humans).
std::string  to_base36(std::uint64_t v);
std::uint64_t from_base36(const std::string& s);  // 0 on malformed

}  // namespace fom
