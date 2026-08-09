#include "ghost_registry.h"

#include <mutex>
#include <unordered_map>

#include "../log.h"

namespace fw::native::ghosts {

namespace {

std::mutex g_mutex;
std::unordered_map<std::string, GhostSlot> g_slots;

// The peer the legacy single-ghost paths act on. Set to the first peer that
// gets a body injected, so it stays stable for the lifetime of that ghost
// rather than following whoever most recently sent a packet.
std::string g_primary;

// Caller must hold g_mutex.
GhostSlot* find_locked(const std::string& peer_id) {
    const auto it = g_slots.find(peer_id);
    return it == g_slots.end() ? nullptr : &it->second;
}

// Caller must hold g_mutex. Re-elects the primary peer, preferring one that
// actually has a body so the legacy accessors don't start returning null while
// a live ghost still exists.
void repick_primary_locked() {
    for (const auto& [peer, slot] : g_slots) {
        if (slot.has_body()) { g_primary = peer; return; }
    }
    g_primary = g_slots.empty() ? std::string{} : g_slots.begin()->first;
}

} // namespace

bool ensure(const std::string& peer_id) {
    std::lock_guard lk(g_mutex);
    const auto [it, inserted] = g_slots.try_emplace(peer_id);
    (void)it;
    if (inserted && g_primary.empty()) g_primary = peer_id;
    return inserted;
}

bool forget(const std::string& peer_id) {
    std::lock_guard lk(g_mutex);
    if (g_slots.erase(peer_id) == 0) return false;
    if (g_primary == peer_id) repick_primary_locked();
    FW_LOG("[ghosts] forget peer='%s' (%zu remaining)",
           peer_id.c_str(), g_slots.size());
    return true;
}

std::size_t count() {
    std::lock_guard lk(g_mutex);
    return g_slots.size();
}

std::vector<std::string> peer_ids() {
    std::lock_guard lk(g_mutex);
    std::vector<std::string> out;
    out.reserve(g_slots.size());
    for (const auto& [peer, slot] : g_slots) {
        (void)slot;
        out.push_back(peer);
    }
    return out;
}

void* body_for(const std::string& peer_id) {
    std::lock_guard lk(g_mutex);
    const GhostSlot* slot = find_locked(peer_id);
    return slot ? slot->body : nullptr;
}

void set_body(const std::string& peer_id, void* body, std::uint32_t form_id) {
    std::lock_guard lk(g_mutex);
    GhostSlot& slot = g_slots[peer_id];
    slot.body    = body;
    slot.form_id = form_id;
    // First peer to actually get a body wins the primary slot. Look the
    // current primary up with find_locked rather than operator[] — the latter
    // would insert an empty slot for a stale key and invalidate `slot`.
    if (body) {
        const GhostSlot* cur = find_locked(g_primary);
        if (g_primary.empty() || !cur || !cur->has_body()) g_primary = peer_id;
    }
    FW_LOG("[ghosts] set_body peer='%s' body=%p fid=0x%08X (primary='%s', "
           "%zu peer(s))",
           peer_id.c_str(), body, form_id, g_primary.c_str(), g_slots.size());
}

void* take_body(const std::string& peer_id) {
    std::lock_guard lk(g_mutex);
    GhostSlot* slot = find_locked(peer_id);
    if (!slot) return nullptr;
    void* body = slot->body;
    slot->body = nullptr;
    // The bone tables point into the body we just released; keeping them would
    // hand out dangling NiAVObject pointers on the next pose apply.
    slot->bone_ptrs.clear();
    slot->canonical_names.clear();
    if (g_primary == peer_id) repick_primary_locked();
    return body;
}

bool any_body() {
    std::lock_guard lk(g_mutex);
    for (const auto& [peer, slot] : g_slots) {
        (void)peer;
        if (slot.has_body()) return true;
    }
    return false;
}

// --- primary slot -----------------------------------------------------------

std::string primary_peer() {
    std::lock_guard lk(g_mutex);
    return g_primary;
}

void* primary_body() {
    std::lock_guard lk(g_mutex);
    const GhostSlot* slot = find_locked(g_primary);
    return slot ? slot->body : nullptr;
}

void set_primary_body(void* body, std::uint32_t form_id) {
    std::string peer;
    {
        std::lock_guard lk(g_mutex);
        // No peer threaded through this call path yet: use the existing
        // primary, or a reserved placeholder key if nothing is registered.
        // The placeholder keeps single-ghost bring-up working before any
        // PEER_JOIN has been processed.
        peer = g_primary.empty() ? std::string("<primary>") : g_primary;
    }
    set_body(peer, body, form_id);
}

void* take_primary_body() {
    std::string peer = primary_peer();
    if (peer.empty()) return nullptr;
    return take_body(peer);
}

// --- bone tables ------------------------------------------------------------

bool set_bones(const std::string& peer_id,
               std::vector<std::string> names,
               std::vector<void*> ptrs) {
    if (names.size() != ptrs.size()) {
        FW_ERR("[ghosts] set_bones peer='%s' REJECTED: %zu names vs %zu ptrs — "
               "a desynced pair would drive the wrong joint",
               peer_id.c_str(), names.size(), ptrs.size());
        return false;
    }
    std::lock_guard lk(g_mutex);
    GhostSlot& slot = g_slots[peer_id];
    slot.canonical_names = std::move(names);
    slot.bone_ptrs       = std::move(ptrs);
    return true;
}

std::vector<void*> bone_ptrs_for(const std::string& peer_id) {
    std::lock_guard lk(g_mutex);
    const GhostSlot* slot = find_locked(peer_id);
    return slot ? slot->bone_ptrs : std::vector<void*>{};
}

std::vector<std::string> bone_names_for(const std::string& peer_id) {
    std::lock_guard lk(g_mutex);
    const GhostSlot* slot = find_locked(peer_id);
    return slot ? slot->canonical_names : std::vector<std::string>{};
}

void clear_all() {
    std::lock_guard lk(g_mutex);
    const std::size_t n = g_slots.size();
    g_slots.clear();
    g_primary.clear();
    if (n) FW_LOG("[ghosts] clear_all: dropped %zu slot(s)", n);
}

} // namespace fw::native::ghosts
