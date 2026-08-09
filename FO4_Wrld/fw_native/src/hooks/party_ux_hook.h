// Tier A2 — party travel UX (debug hotkeys).
//
//   F8  Teleport local player to primary remote peer last known pos
//   F9  Summon party: broadcast my pos; other peers teleport to me
//
// Not a MinHook detour — a light poll thread (50 ms) that edge-triggers
// on key state. Teleport itself runs on the FO4 main thread via
// fw::dispatch (MoveTo is TLS-affine).

#pragma once

#include <cstdint>

namespace fw::hooks {

// Starts the hotkey poll thread. Returns true if spawned.
bool start_party_ux(std::uintptr_t module_base);

void stop_party_ux();

} // namespace fw::hooks
