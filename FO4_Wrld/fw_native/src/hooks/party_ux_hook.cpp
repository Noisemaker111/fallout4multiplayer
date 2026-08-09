#include "party_ux_hook.h"

#include <windows.h>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <thread>

#include "../engine/engine_calls.h"
#include "../log.h"
#include "../main_thread_dispatch.h"
#include "../net/client.h"
#include "../net/protocol.h"
#include "../offsets.h"

namespace fw::hooks {

namespace {

constexpr DWORD kPollMs = 50;
constexpr int   kVkTeleportToPeer = VK_F8;
constexpr int   kVkSummonParty    = VK_F9;

std::atomic<bool> g_stop{false};
std::thread g_thread;

bool key_edge(int vk, bool& was_down) {
    const bool down = (GetAsyncKeyState(vk) & 0x8000) != 0;
    const bool edge = down && !was_down;
    was_down = down;
    return edge;
}

// SEH-only helper: no C++ objects with destructors (MSVC C2712).
bool seh_read_local_player_pose(float* out_x, float* out_y, float* out_z,
                                float* out_yaw, std::uint32_t* out_cell) {
    void* player = fw::engine::get_local_player();
    if (!player || !out_x || !out_y || !out_z || !out_yaw || !out_cell) {
        return false;
    }
    bool ok = false;
    __try {
        const auto* base = reinterpret_cast<const std::uint8_t*>(player);
        *out_x = *reinterpret_cast<const float*>(base + offsets::POS_OFF);
        *out_y = *reinterpret_cast<const float*>(base + offsets::POS_OFF + 4);
        *out_z = *reinterpret_cast<const float*>(base + offsets::POS_OFF + 8);
        *out_yaw = *reinterpret_cast<const float*>(base + offsets::ROT_OFF + 8);
        *out_cell = 0;
        void* cell = *reinterpret_cast<void* const*>(
            base + offsets::PARENT_CELL_OFF);
        if (cell) {
            *out_cell = *reinterpret_cast<const std::uint32_t*>(
                reinterpret_cast<const std::uint8_t*>(cell) +
                offsets::FORMID_OFF);
        }
        ok = true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        ok = false;
    }
    return ok;
}

void poll_loop(std::uintptr_t /*module_base*/) {
    bool f8_down = false;
    bool f9_down = false;
    FW_LOG("[party_ux] hotkeys armed: F8=teleport-to-peer, F9=summon-party");

    while (!g_stop.load(std::memory_order_relaxed)) {
        Sleep(kPollMs);

        if (!fw::net::client().is_connected()) continue;

        if (key_edge(kVkTeleportToPeer, f8_down)) {
            const auto snap = fw::net::client().get_remote_snapshot();
            if (!snap.has_state) {
                FW_LOG("[party_ux] F8: no remote peer snapshot yet");
                continue;
            }
            fw::dispatch::PendingPartyTeleportOp op{};
            op.pos_x   = snap.pos[0];
            op.pos_y   = snap.pos[1];
            op.pos_z   = snap.pos[2];
            // rot[2] is yaw radians (Bethesda) on POS payloads.
            op.yaw_rad = snap.rot[2];
            op.cell_id = snap.cell_id;
            FW_LOG("[party_ux] F8: teleport → peer=%s pos=(%.1f,%.1f,%.1f) "
                   "cell=0x%X",
                   snap.peer_id.c_str(), op.pos_x, op.pos_y, op.pos_z,
                   op.cell_id);
            fw::dispatch::enqueue_party_teleport(op);
        }

        if (key_edge(kVkSummonParty, f9_down)) {
            float x = 0, y = 0, z = 0, yaw = 0;
            std::uint32_t cell_id = 0;
            if (!seh_read_local_player_pose(&x, &y, &z, &yaw, &cell_id) ||
                !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) {
                FW_LOG("[party_ux] F9: failed to read local pos");
                continue;
            }
            FW_LOG("[party_ux] F9: summon party to (%.1f,%.1f,%.1f) cell=0x%X",
                   x, y, z, cell_id);
            fw::net::client().enqueue_party_warp(x, y, z, yaw, cell_id);
        }
    }
}

} // namespace

bool start_party_ux(std::uintptr_t module_base) {
    if (g_thread.joinable()) return true;
    g_stop.store(false, std::memory_order_relaxed);
    try {
        g_thread = std::thread(poll_loop, module_base);
    } catch (...) {
        FW_ERR("[party_ux] failed to spawn poll thread");
        return false;
    }
    return true;
}

void stop_party_ux() {
    g_stop.store(true, std::memory_order_relaxed);
    if (g_thread.joinable()) g_thread.join();
}

} // namespace fw::hooks
