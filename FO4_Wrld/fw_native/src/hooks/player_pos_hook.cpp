#include "player_pos_hook.h"

#include <windows.h>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <system_error>
#include <thread>

#include "../engine/engine_calls.h"
#include "../log.h"
#include "../offsets.h"
#include "../net/client.h"
#include "../net/protocol.h"

namespace fw::hooks {

namespace {

constexpr DWORD POLL_INTERVAL_MS = 50;      // match Frida JS 20 Hz
constexpr DWORD LOG_THROTTLE_MS  = 2000;    // emit at most one INFO per 2s
constexpr DWORD GAMEHOUR_POLL_MS = 2000;    // B6.11: GameHour every 2s
constexpr DWORD WEATHER_POLL_MS  = 5000;    // B6.11: weather form every 5s
constexpr DWORD CELL_CLEARED_POLL_MS = 3000; // B6.9: cleared flag every 3s
constexpr DWORD CELL_TRAVEL_DEBOUNCE_MS = 2500; // B3: auto-follow after load door
constexpr float MIN_MOVE_UNITS   = 4.0f;    // ignore sub-unit jitter
constexpr float GAMEHOUR_EPS     = 0.001f;  // hours (~3.6 s at scale 1)
// B3: when local player changes parentCell, broadcast PARTY_WARP so peers
// can follow through load doors / fast travel (reuses A2 summon path).
constexpr bool kAutoPartyFollowOnCellChange = true;

std::atomic<bool> g_stop{false};
std::thread g_thread;

struct Vec3 { float x, y, z; };

bool float_finite_bounded(float v) {
    return std::isfinite(v) && std::fabs(v) < 1.0e7f;
}

// SEH-safe deref: returns fallback on access violation.
template <typename T>
T safe_read(const void* addr, T fallback) noexcept {
    if (!addr) return fallback;
    __try { return *reinterpret_cast<const T*>(addr); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return fallback; }
}

void poll_loop(std::uintptr_t module_base) {
    auto* singleton_slot = reinterpret_cast<void**>(
        module_base + offsets::PLAYER_SINGLETON_RVA);

    Vec3 last{0, 0, 0};
    bool has_last = false;
    DWORD last_log_ms = 0;
    DWORD last_gamehour_ms = 0;
    DWORD last_weather_ms = 0;
    DWORD last_cell_cleared_ms = 0;
    DWORD last_cell_travel_ms = 0;
    float last_gamehour = -1.0f;
    bool has_gamehour = false;
    std::uint32_t last_weather_form = 0;
    std::uint32_t last_cell_id = 0;
    bool has_cell = false;
    bool last_cell_cleared = false;
    bool has_cell_cleared = false;
    std::uint64_t reads_total = 0;
    std::uint64_t reads_valid = 0;

    // B6.11: resolve GameHour TESGlobal once (form 0x38).
    auto* lookup = reinterpret_cast<void* (*)(std::uint32_t)>(
        module_base + offsets::LOOKUP_BY_FORMID_RVA);
    void* game_hour_form = nullptr;

    while (!g_stop.load(std::memory_order_relaxed)) {
        Sleep(POLL_INTERVAL_MS);

        __try {
            void* player = safe_read<void*>(singleton_slot, nullptr);
            if (!player) continue;

            const auto* base = reinterpret_cast<const std::uint8_t*>(player);

            // formID must be 0x14 for PlayerCharacter — during main menu /
            // intro the struct can be pre-populated with garbage.
            const auto formid = safe_read<std::uint32_t>(
                base + offsets::FORMID_OFF, 0u);
            if (formid != offsets::PLAYER_FORMID) continue;

            // parentCell must be non-null — gate same as Frida era.
            const auto* cell = safe_read<void*>(
                base + offsets::PARENT_CELL_OFF, nullptr);
            if (!cell) continue;

            // v11 (B6 prologue): read parentCell.formID so receiver can
            // CULL the ghost when peers are in different cells. Cell is a
            // TESObjectCELL → inherits TESForm → formID at +0x14.
            const auto cell_form_id = safe_read<std::uint32_t>(
                reinterpret_cast<const std::uint8_t*>(cell) + offsets::FORMID_OFF,
                0u);

            const Vec3 pos{
                safe_read<float>(base + offsets::POS_OFF,     0.0f),
                safe_read<float>(base + offsets::POS_OFF + 4, 0.0f),
                safe_read<float>(base + offsets::POS_OFF + 8, 0.0f),
            };
            const Vec3 rot{
                safe_read<float>(base + offsets::ROT_OFF,     0.0f),
                safe_read<float>(base + offsets::ROT_OFF + 4, 0.0f),
                safe_read<float>(base + offsets::ROT_OFF + 8, 0.0f),
            };
            if (!float_finite_bounded(pos.x) ||
                !float_finite_bounded(pos.y) ||
                !float_finite_bounded(pos.z) ||
                !float_finite_bounded(rot.x) ||
                !float_finite_bounded(rot.y) ||
                !float_finite_bounded(rot.z)) {
                continue;
            }

            ++reads_total;
            ++reads_valid;

            // Push POS_STATE to the server every tick (no throttle). The
            // Python server already handles rate limiting; we stream at
            // 20 Hz just like the old Frida-era did. Logging stays throttled.
            {
                using namespace std::chrono;
                fw::net::PosStatePayload p{};
                p.x = pos.x; p.y = pos.y; p.z = pos.z;
                p.rx = rot.x; p.ry = rot.y; p.rz = rot.z;
                p.timestamp_ms = duration_cast<milliseconds>(
                    system_clock::now().time_since_epoch()).count();
                p.cell_id = cell_form_id;   // v11 — B6 prologue
                fw::net::client().enqueue_pos_state(p);
            }

            // B6.11: low-Hz GameHour push via existing GLOBAL_VAR path.
            // Only the peer that advances time (Wait/Sleep/scripts) will
            // fire SetValue; polling catches continuous TOD drift too.
            {
                const DWORD now_gh = GetTickCount();
                if (now_gh - last_gamehour_ms >= GAMEHOUR_POLL_MS) {
                    last_gamehour_ms = now_gh;
                    if (!game_hour_form && lookup) {
                        __try {
                            game_hour_form = lookup(
                                offsets::GLOBAL_FORM_GAME_HOUR);
                        } __except (EXCEPTION_EXECUTE_HANDLER) {
                            game_hour_form = nullptr;
                        }
                    }
                    if (game_hour_form) {
                        float hour = 0.f;
                        bool ok_h = false;
                        __try {
                            hour = *reinterpret_cast<const float*>(
                                reinterpret_cast<const std::uint8_t*>(
                                    game_hour_form) +
                                offsets::TESGLOBAL_VALUE_OFF);
                            ok_h = std::isfinite(hour);
                        } __except (EXCEPTION_EXECUTE_HANDLER) {
                            ok_h = false;
                        }
                        if (ok_h &&
                            (!has_gamehour ||
                             std::fabs(hour - last_gamehour) > GAMEHOUR_EPS)) {
                            last_gamehour = hour;
                            has_gamehour = true;
                            fw::net::client().enqueue_global_var_set(
                                offsets::GLOBAL_FORM_GAME_HOUR,
                                static_cast<double>(hour));
                            FW_DBG("[pos] GameHour sync value=%.4f", hour);
                        }
                    }
                }
            }

            // B6.11: poll active weather formID (complements SetActive hook).
            {
                const DWORD now_w = GetTickCount();
                if (now_w - last_weather_ms >= WEATHER_POLL_MS) {
                    last_weather_ms = now_w;
                    const std::uint32_t wid =
                        fw::engine::get_current_weather_form_id();
                    if (wid != 0 && wid != last_weather_form) {
                        last_weather_form = wid;
                        fw::net::client().enqueue_weather_set(wid);
                        FW_DBG("[pos] weather sync form=0x%X", wid);
                    }
                }
            }

            // B3: cell-travel party follow — load door / coc / fast travel.
            // After cell_id changes, summon peers to our new position
            // (debounced so multi-frame cell stream doesn't spam warps).
            if (kAutoPartyFollowOnCellChange && cell_form_id != 0) {
                if (has_cell && cell_form_id != last_cell_id) {
                    const DWORD now_ct = GetTickCount();
                    if (now_ct - last_cell_travel_ms >= CELL_TRAVEL_DEBOUNCE_MS) {
                        last_cell_travel_ms = now_ct;
                        FW_LOG("[pos] CELL_TRAVEL 0x%X -> 0x%X — party follow "
                               "warp to (%.1f,%.1f,%.1f)",
                               last_cell_id, cell_form_id,
                               pos.x, pos.y, pos.z);
                        fw::net::client().enqueue_party_warp(
                            pos.x, pos.y, pos.z, rot.z, cell_form_id);
                    }
                    // Reset cleared poll when entering a new cell.
                    has_cell_cleared = false;
                }
                last_cell_id = cell_form_id;
                has_cell = true;
            }

            // B6.9: poll current cell cleared flag; emit on rising edge
            // (or first observation if already cleared — join-time catch-up
            // is bootstrap; this catches in-session clears).
            if (cell_form_id != 0) {
                const DWORD now_cc = GetTickCount();
                if (now_cc - last_cell_cleared_ms >= CELL_CLEARED_POLL_MS) {
                    last_cell_cleared_ms = now_cc;
                    bool cleared = false;
                    if (fw::engine::get_cell_cleared(cell_form_id, &cleared)) {
                        if (!has_cell_cleared || cleared != last_cell_cleared) {
                            if (cleared) {
                                // Only push "cleared=true" transitions to avoid
                                // spamming uncleared cells every 3s on join.
                                fw::net::client().enqueue_cell_cleared(
                                    cell_form_id, true);
                                FW_LOG("[pos] cell 0x%X CLEARED — broadcast",
                                       cell_form_id);
                            }
                            last_cell_cleared = cleared;
                            has_cell_cleared = true;
                        }
                    }
                }
            }

            const DWORD now = GetTickCount();
            float dx = 0, dy = 0, dz = 0, dist = 0;
            if (has_last) {
                dx = pos.x - last.x;
                dy = pos.y - last.y;
                dz = pos.z - last.z;
                dist = std::sqrt(dx * dx + dy * dy + dz * dz);
            }

            const bool moved = (!has_last) || (dist >= MIN_MOVE_UNITS);
            const bool throttled_ok =
                (now - last_log_ms) >= LOG_THROTTLE_MS;

            if (moved && throttled_ok) {
                FW_LOG("[pos] pos=(%.1f, %.1f, %.1f) d=%.1f  reads=%llu",
                       pos.x, pos.y, pos.z, dist,
                       static_cast<unsigned long long>(reads_valid));
                last = pos;
                has_last = true;
                last_log_ms = now;
            } else if (!has_last) {
                last = pos;
                has_last = true;
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            // Rare — only happens if memory is unmapped between the
            // singleton slot check and the actual reads. Next iteration.
        }
    }

    FW_LOG("[pos] poll thread stopping (total_valid_reads=%llu)",
           static_cast<unsigned long long>(reads_valid));
}

} // namespace

bool start_player_pos_poll(std::uintptr_t module_base) {
    if (g_thread.joinable()) {
        FW_WRN("[pos] start called twice — ignoring");
        return true;
    }
    g_stop.store(false);
    try {
        g_thread = std::thread(poll_loop, module_base);
    } catch (const std::system_error& e) {
        FW_ERR("[pos] failed to spawn poll thread: %s", e.what());
        return false;
    }
    FW_LOG("[pos] poll thread started at %u ms interval (throttled INFO @ %u ms, min move %.1f u)",
           POLL_INTERVAL_MS, LOG_THROTTLE_MS, MIN_MOVE_UNITS);
    return true;
}

void stop_player_pos_poll() {
    g_stop.store(true);
    if (g_thread.joinable()) {
        g_thread.join();
    }
}

} // namespace fw::hooks
