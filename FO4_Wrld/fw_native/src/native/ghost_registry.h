// Per-peer ghost registry.
//
// Everything about a remote player's visible body that used to live in file-
// scope singletons inside scene_inject.cpp, gathered into one record per peer.
//
// Why this exists
// ---------------
// The client was built around exactly one remote player. The body root
// (`g_injected_cube`), the canonical joint-name list and the resolved bone
// pointer table were all single globals. That is correct for two players and
// silently wrong for three: two peers in different poses would write the same
// `g_ghost_bone_ptrs`, so every ghost would animate as whichever peer's packet
// arrived last.
//
// The server has already been verified to fan out to 10 peers
// (net/tests/test_multipeer.py) and the net layer now keeps per-peer position
// and pose snapshots, so this is the remaining single-writer bottleneck.
//
// Staging
// -------
// Migration is deliberately incremental. `primary()` returns the one slot the
// current code path uses, so existing call sites can be routed through the
// registry without any behaviour change and with the build staying green —
// see docs/MULTIPEER.md. Only once every consumer is peer-aware does raising
// the slot count become a behaviour change worth runtime-testing.
//
// Threading
// ---------
// Ghost bodies are created and mutated on the FO4 main thread (scene-graph
// work is not thread-safe), but the body pointer is *read* from the net thread
// and the render thread. All access goes through the mutex below; the returned
// pointers are raw engine nodes whose lifetime is owned by the main thread, so
// callers must not cache them across frames.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace fw::native::ghosts {

// One remote player's visible body.
struct GhostSlot {
    // BSFadeNode* root of the injected body subtree, attached under
    // ShadowSceneNode. Null until inject succeeds for this peer.
    void* body = nullptr;

    // Synthetic form id this peer's ghost is registered under. Peers get
    // GHOST_FORMID_BASE + n so the server can address each one distinctly;
    // with a single ghost this was a hardcoded constant.
    std::uint32_t form_id = 0;

    // Canonical joint names for this body, in the deterministic sorted order
    // both sender and receiver walk. Index i here matches index i in the
    // received quaternion array.
    std::vector<std::string> canonical_names;

    // Resolved NiAVObject* per canonical joint — parallel to canonical_names.
    // These are the same entries the GPU reads via the bones_pri pointer cache
    // after re-cache, which is why they must be per-body and never shared.
    std::vector<void*> bone_ptrs;

    bool has_body() const noexcept { return body != nullptr; }
};

// Create the slot for `peer_id` if absent, and return whether it was created.
bool ensure(const std::string& peer_id);

// Drop a peer's slot. Does NOT detach or free the body — scene-graph teardown
// is the caller's job on the main thread; this only forgets our reference.
// Returns true if a slot was actually removed.
bool forget(const std::string& peer_id);

// Number of registered peers (with or without a body injected yet).
std::size_t count();

// Peers currently registered, in unspecified order.
std::vector<std::string> peer_ids();

// --- body pointer -----------------------------------------------------------

// Body root for one peer, or null if unknown / not yet injected.
void* body_for(const std::string& peer_id);

// Record the injected body root for a peer. Creates the slot if needed.
void set_body(const std::string& peer_id, void* body, std::uint32_t form_id);

// Clear one peer's body pointer and return what it was (null if none).
// The caller owns detaching the returned node.
void* take_body(const std::string& peer_id);

// True if any peer has a body injected.
bool any_body();

// --- primary slot (single-ghost compatibility) ------------------------------
//
// The transitional accessors. `primary_peer()` is the peer the legacy
// single-ghost code paths act on: the first peer to get a body, falling back
// to the first registered peer. While only one ghost exists these are exactly
// equivalent to the old globals.
//
// New code should take a peer_id instead. Each remaining use of these is a
// site that still needs converting before the peer count can be raised.

std::string primary_peer();

// Body root of the primary peer, or null. Direct replacement for
// `g_injected_cube.load()`.
void* primary_body();

// Set the primary peer's body. Direct replacement for `g_injected_cube.store()`
// during bring-up, when the peer id isn't threaded through the call path yet.
void set_primary_body(void* body, std::uint32_t form_id);

// Clear and return the primary body. Replacement for
// `g_injected_cube.exchange(nullptr)`.
void* take_primary_body();

// --- bone tables ------------------------------------------------------------

// Replace a peer's canonical joint table. Sizes must match; a mismatch is
// rejected (returns false) rather than half-applied, because a desynced
// name/pointer pair would drive the wrong joint.
bool set_bones(const std::string& peer_id,
               std::vector<std::string> names,
               std::vector<void*> ptrs);

// Copy out a peer's bone pointers. Empty if the peer is unknown or the body
// hasn't been walked yet. Copied, not referenced: the caller iterates without
// holding the registry lock.
std::vector<void*> bone_ptrs_for(const std::string& peer_id);

// Copy out a peer's canonical joint names.
std::vector<std::string> bone_names_for(const std::string& peer_id);

// Remove every slot. Called on world teardown / DLL shutdown. Does not free
// bodies — see forget().
void clear_all();

} // namespace fw::native::ghosts
