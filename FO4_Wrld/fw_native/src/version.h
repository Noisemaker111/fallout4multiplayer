// Version fingerprint check for Fallout4.exe.
//
// All our reverse-engineered RVAs (kill hook, container vt[0x7A], pos
// offsets, etc.) are being retargeted to Fallout 4 version 1.10.163.0
// (classic / pre-next-gen frozen build). If the game binary is anything
// else — next-gen 1.11.x, a Creation Club update that relinks — those
// RVAs are junk and installing hooks on them crashes the game reliably.
//
// This module is the gate: DllMain's init thread calls `check()` and
// refuses to proceed to hook installation unless the check returns
// `Match`. On mismatch we stay inert (log + forward-only) so FO4 still
// boots and the user can diagnose.
//
// Port status: see docs/START_HERE.md. Do not relax this gate.

#pragma once

#include <string>

namespace fw::version {

// Expected binary version. Target build for the 1.10.163 port (2026-07-28).
constexpr const char* EXPECTED = "1.10.163.0";

// Hard safety for the in-progress port. The version gate only proves the
// *binary* is 1.10.163 — it does NOT prove our RVAs are correct. Automated
// recovery topped out at ~17% on the FW_MINIMAL code set (see
// docs/START_HERE.md step H). Installing MinHook detours against mid-function
// addresses corrupts the process. Keep this false until
// `tools/offset_audit.py --minimal` scores ≥95%, then flip to true in the
// same change that lands the residual IDA ports.
// 2026-08-02: offset_audit --minimal = 100% on 1.10.163 unpacked.
constexpr bool PORT_READY = true;

enum class Result {
    Match,        // exact expected version
    Mismatch,     // resolvable version, but different from EXPECTED
    Unresolvable, // couldn't read VERSIONINFO — treat as Mismatch
};

// Reads the VERSIONINFO resource of the currently-loaded Fallout4.exe
// and compares against EXPECTED. `actual_out` (if non-null) is set to
// the resolved "MAJOR.MINOR.PATCH.BUILD" string (empty on Unresolvable).
Result check(std::string* actual_out = nullptr);

} // namespace fw::version
