// B4 + B6.7: capture Papyrus world-state mutations and broadcast to peers.
//
// Hooks:
//   GlobalVariable.SetValue   (offsets::PAPYRUS_GLOBALVAR_SETVALUE_RVA)
//   Quest.SetCurrentStageID   (offsets::PAPYRUS_QUEST_SETSTAGE_RVA)
//   Actor.SetFactionRank      (offsets::PAPYRUS_ACTOR_SETFACTIONRANK_RVA)
//
// Observe-only detours: always call through. Local mutations enqueue SET
// to the server; receive-side apply uses engine helpers + main-thread
// dispatch where TLS affinity requires it.
//
// Faction ranks are filtered to the local PlayerCharacter only — NPC
// faction mutations are not campaign-narrative for co-op Pip-Boy state.

#pragma once

#include <cstdint>

namespace fw::hooks {

// Installs GlobalVar + QuestStage + SetFactionRank hooks.
// Returns true only if ALL hooks installed successfully.
bool install_worldstate_hooks(std::uintptr_t module_base);

} // namespace fw::hooks
