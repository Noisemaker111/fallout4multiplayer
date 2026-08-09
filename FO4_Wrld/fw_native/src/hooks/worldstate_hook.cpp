#include "worldstate_hook.h"

#include <windows.h>
#include <cstdint>

#include "container_hook.h"  // tls_applying_remote / ApplyingRemoteGuard
#include "../engine/engine_calls.h"
#include "../hook_manager.h"
#include "../log.h"
#include "../offsets.h"
#include "../net/client.h"
#include "../net/protocol.h"

namespace fw::hooks {

namespace {

// Papyrus GlobalVariable.SetValue (1.10.163 @ 0x13F0A40)
using GlobalVarSetValueFn = std::uint8_t (*)(
    void* vm, std::uint32_t stack_id, void* global_obj, float new_value);

// Papyrus Quest.SetCurrentStageID (1.10.163 @ 0x1449550)
using QuestSetStageFn = char (*)(
    void* vm, std::uint32_t stack_id, void* quest_obj, std::uint32_t stage);

// Papyrus Actor.SetFactionRank (1.10.163 @ 0x138AC30)
using ActorSetFactionRankFn = void (*)(
    void* vm, std::uint32_t stack_id, void* actor, void* faction, int rank);

// Game.RewardPlayerXP (1.10.163 @ 0x13C1EA0)
// void(VM*, stackId, StaticFunctionTag*, int amount, bool suppress)
using RewardPlayerXPFn = void (*)(
    void* vm, std::uint32_t stack_id, void* tag, int amount, bool suppress);

GlobalVarSetValueFn    g_orig_global_set_value     = nullptr;
QuestSetStageFn        g_orig_quest_set_stage      = nullptr;
ActorSetFactionRankFn  g_orig_actor_set_faction    = nullptr;
RewardPlayerXPFn       g_orig_reward_player_xp     = nullptr;

// Weather.SetActive(VM*, stackId, TESWeather* self, bool override, bool accelerate)
using WeatherSetActiveFn = void (*)(
    void* vm, std::uint32_t stack_id, void* weather,
    bool ab_override, bool ab_accelerate);
WeatherSetActiveFn g_orig_weather_set_active = nullptr;

// Actor.SetPlayerTeammate(VM*, stackId, Actor* self, bool teammate,
//                         bool canDoFavor, bool?)
// Thunk at 0x1391550 packs bools then jmps to worker — we hook the thunk.
using SetPlayerTeammateFn = void (*)(
    void* vm, std::uint32_t stack_id, void* actor,
    bool teammate, bool can_do_favor, bool a6);
SetPlayerTeammateFn g_orig_set_player_teammate = nullptr;

// Cell.SetCleared(VM*, stackId, TESObjectCELL* self, bool cleared)
// Thunk 0x13AB820 packs and jumps to worker — hook the thunk.
using CellSetClearedFn = void (*)(
    void* vm, std::uint32_t stack_id, void* cell, bool cleared);
CellSetClearedFn g_orig_cell_set_cleared = nullptr;

// Actor.SetRelationshipRank(VM*, stackId, Actor* self, Actor* other, int rank)
using SetRelationshipRankFn = void (*)(
    void* vm, std::uint32_t stack_id, void* actor_a, void* actor_b, int rank);
SetRelationshipRankFn g_orig_set_relationship_rank = nullptr;

// Game.PassTime(VM*, stackId, StaticFunctionTag*, int hours)
// Wait/Sleep funnel — fan-out so peers advance the same game hours.
using PassTimeFn = void (*)(
    void* vm, std::uint32_t stack_id, void* tag, int hours);
PassTimeFn g_orig_pass_time = nullptr;

// ObjectReference.AddItem(VM*, stackId, REFR* self, TESForm* item,
//                      int count, bool silent) — returns bool (success).
// Quest reward path when silent==true (or KEY form) and self is local PC.
using ObjectRefAddItemFn = bool (*)(
    void* vm, std::uint32_t stack_id, void* self, void* item,
    int count, bool silent);
ObjectRefAddItemFn g_orig_objectref_additem = nullptr;

std::uint8_t __fastcall detour_global_set_value(
    void* vm, std::uint32_t stack_id, void* global_obj, float new_value)
{
    __try {
        if (global_obj && !tls_applying_remote) {
            const auto* bytes = reinterpret_cast<const std::uint8_t*>(global_obj);
            std::uint32_t form_id = 0;
            std::uint32_t flags   = 0;
            __try {
                form_id = *reinterpret_cast<const std::uint32_t*>(
                    bytes + offsets::FORMID_OFF);
                flags = *reinterpret_cast<const std::uint32_t*>(
                    bytes + offsets::FLAGS_OFF);
            } __except (EXCEPTION_EXECUTE_HANDLER) {}

            const bool is_const =
                (flags & offsets::TESGLOBAL_FLAG_CONST) != 0;

            if (form_id != 0 && !is_const) {
                FW_LOG("[worldstate] GlobalVar.SetValue form=0x%X value=%g",
                       form_id, new_value);
                fw::net::client().enqueue_global_var_set(
                    form_id, static_cast<double>(new_value));
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_global_set_value");
    }

    if (g_orig_global_set_value) {
        return g_orig_global_set_value(vm, stack_id, global_obj, new_value);
    }
    return 0;
}

char __fastcall detour_quest_set_stage(
    void* vm, std::uint32_t stack_id, void* quest_obj, std::uint32_t stage)
{
    __try {
        if (quest_obj && !tls_applying_remote) {
            std::uint32_t form_id = 0;
            __try {
                const auto* bytes =
                    reinterpret_cast<const std::uint8_t*>(quest_obj);
                form_id = *reinterpret_cast<const std::uint32_t*>(
                    bytes + offsets::FORMID_OFF);
            } __except (EXCEPTION_EXECUTE_HANDLER) {}

            if (form_id != 0 && stage <= 0xFFFFu) {
                FW_LOG("[worldstate] Quest.SetCurrentStageID form=0x%X stage=%u",
                       form_id, stage);
                fw::net::client().enqueue_quest_stage_set(
                    form_id, static_cast<std::uint16_t>(stage));
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_quest_set_stage");
    }

    if (g_orig_quest_set_stage) {
        return g_orig_quest_set_stage(vm, stack_id, quest_obj, stage);
    }
    return 0;
}

// Naked-ish: rank is the 5th integer arg. MSVC puts it at [rsp+28h] on entry
// before our prolog runs. MinHook trampoline preserves the stack, so when we
// are entered the 5th arg is still at the caller's [rsp+28h] relative to
// OUR entry rsp (same as original). We declare it as a formal parameter so
// the compiler places it correctly.
void __fastcall detour_actor_set_faction_rank(
    void* vm, std::uint32_t stack_id, void* actor, void* faction, int rank)
{
    __try {
        if (actor && faction && !tls_applying_remote) {
            // Only local PlayerCharacter — story/Pip-Boy faction list.
            void* local_pc = fw::engine::get_local_player();
            if (local_pc && actor == local_pc) {
                std::uint32_t faction_form_id = 0;
                __try {
                    const auto* bytes =
                        reinterpret_cast<const std::uint8_t*>(faction);
                    faction_form_id = *reinterpret_cast<const std::uint32_t*>(
                        bytes + offsets::FORMID_OFF);
                } __except (EXCEPTION_EXECUTE_HANDLER) {}

                if (faction_form_id != 0) {
                    FW_LOG("[worldstate] Actor.SetFactionRank (PC) "
                           "faction=0x%X rank=%d",
                           faction_form_id, rank);
                    fw::net::client().enqueue_faction_rank_set(
                        faction_form_id, rank);
                }
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_actor_set_faction_rank");
    }

    if (g_orig_actor_set_faction) {
        g_orig_actor_set_faction(vm, stack_id, actor, faction, rank);
    }
}

void __fastcall detour_reward_player_xp(
    void* vm, std::uint32_t stack_id, void* tag, int amount, bool suppress)
{
    __try {
        // Skip re-emit when applying a remote proximity XP grant.
        if (!tls_applying_remote && amount != 0) {
            FW_LOG("[worldstate] Game.RewardPlayerXP amount=%d suppress=%d",
                   amount, suppress ? 1 : 0);
            fw::net::client().enqueue_xp_grant(static_cast<std::int32_t>(amount));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_reward_player_xp");
    }

    if (g_orig_reward_player_xp) {
        g_orig_reward_player_xp(vm, stack_id, tag, amount, suppress);
    }
}

void __fastcall detour_weather_set_active(
    void* vm, std::uint32_t stack_id, void* weather,
    bool ab_override, bool ab_accelerate)
{
    __try {
        if (weather && !tls_applying_remote) {
            std::uint32_t form_id = 0;
            __try {
                form_id = *reinterpret_cast<const std::uint32_t*>(
                    reinterpret_cast<const std::uint8_t*>(weather) +
                    offsets::FORMID_OFF);
            } __except (EXCEPTION_EXECUTE_HANDLER) {}
            if (form_id != 0) {
                FW_LOG("[worldstate] Weather.SetActive form=0x%X override=%d",
                       form_id, ab_override ? 1 : 0);
                fw::net::client().enqueue_weather_set(form_id);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_weather_set_active");
    }
    if (g_orig_weather_set_active) {
        g_orig_weather_set_active(vm, stack_id, weather,
                                  ab_override, ab_accelerate);
    }
}

void __fastcall detour_set_player_teammate(
    void* vm, std::uint32_t stack_id, void* actor,
    bool teammate, bool can_do_favor, bool a6)
{
    __try {
        if (actor && !tls_applying_remote) {
            std::uint32_t form_id = 0;
            __try {
                form_id = *reinterpret_cast<const std::uint32_t*>(
                    reinterpret_cast<const std::uint8_t*>(actor) +
                    offsets::FORMID_OFF);
            } __except (EXCEPTION_EXECUTE_HANDLER) {}
            if (form_id != 0) {
                FW_LOG("[worldstate] SetPlayerTeammate form=0x%X teammate=%d "
                       "favor=%d",
                       form_id, teammate ? 1 : 0, can_do_favor ? 1 : 0);
                fw::engine::note_local_companion(form_id, teammate);
                fw::net::client().enqueue_companion_set(
                    form_id, teammate, can_do_favor);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_set_player_teammate");
    }
    if (g_orig_set_player_teammate) {
        g_orig_set_player_teammate(vm, stack_id, actor,
                                   teammate, can_do_favor, a6);
    }
}

void __fastcall detour_cell_set_cleared(
    void* vm, std::uint32_t stack_id, void* cell, bool cleared)
{
    __try {
        if (cell && !tls_applying_remote) {
            std::uint32_t form_id = 0;
            __try {
                form_id = *reinterpret_cast<const std::uint32_t*>(
                    reinterpret_cast<const std::uint8_t*>(cell) +
                    offsets::FORMID_OFF);
            } __except (EXCEPTION_EXECUTE_HANDLER) {}
            if (form_id != 0) {
                FW_LOG("[worldstate] Cell.SetCleared form=0x%X cleared=%d",
                       form_id, cleared ? 1 : 0);
                fw::net::client().enqueue_cell_cleared(form_id, cleared);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_cell_set_cleared");
    }
    if (g_orig_cell_set_cleared) {
        g_orig_cell_set_cleared(vm, stack_id, cell, cleared);
    }
}

void __fastcall detour_set_relationship_rank(
    void* vm, std::uint32_t stack_id, void* actor_a, void* actor_b, int rank)
{
    __try {
        if (actor_a && actor_b && !tls_applying_remote) {
            std::uint32_t form_a = 0, form_b = 0;
            __try {
                form_a = *reinterpret_cast<const std::uint32_t*>(
                    reinterpret_cast<const std::uint8_t*>(actor_a) +
                    offsets::FORMID_OFF);
                form_b = *reinterpret_cast<const std::uint32_t*>(
                    reinterpret_cast<const std::uint8_t*>(actor_b) +
                    offsets::FORMID_OFF);
            } __except (EXCEPTION_EXECUTE_HANDLER) {}
            if (form_a != 0 && form_b != 0) {
                FW_LOG("[worldstate] SetRelationshipRank 0x%X->0x%X rank=%d",
                       form_a, form_b, rank);
                fw::net::client().enqueue_relationship_set(
                    form_a, form_b, static_cast<std::int32_t>(rank));
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_set_relationship_rank");
    }
    if (g_orig_set_relationship_rank) {
        g_orig_set_relationship_rank(vm, stack_id, actor_a, actor_b, rank);
    }
}

void __fastcall detour_pass_time(
    void* vm, std::uint32_t stack_id, void* tag, int hours)
{
    __try {
        if (!tls_applying_remote && hours != 0) {
            // Cap at 30 days of game-hours for sanity (server also caps).
            int emit_hours = hours;
            if (emit_hours > 720) emit_hours = 720;
            if (emit_hours < -720) emit_hours = -720;
            FW_LOG("[worldstate] Game.PassTime hours=%d", emit_hours);
            fw::net::client().enqueue_time_pass(
                static_cast<std::int32_t>(emit_hours));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_pass_time");
    }
    if (g_orig_pass_time) {
        g_orig_pass_time(vm, stack_id, tag, hours);
    }
}

bool __fastcall detour_objectref_additem(
    void* vm, std::uint32_t stack_id, void* self, void* item,
    int count, bool silent)
{
    __try {
        // Quest-reward policy: silent adds to local PC, plus KEY forms even
        // when non-silent (story keys must not strand the co-op partner).
        if (!tls_applying_remote && self && item && count > 0) {
            void* local_pc = fw::engine::get_local_player();
            if (local_pc && self == local_pc) {
                std::uint32_t item_form_id = 0;
                std::uint8_t form_type = 0;
                __try {
                    const auto* bytes =
                        reinterpret_cast<const std::uint8_t*>(item);
                    item_form_id = *reinterpret_cast<const std::uint32_t*>(
                        bytes + offsets::FORMID_OFF);
                    form_type = *reinterpret_cast<const std::uint8_t*>(
                        bytes + offsets::FORMTYPE_OFF);
                } __except (EXCEPTION_EXECUTE_HANDLER) {}

                const bool is_key =
                    (form_type == offsets::FORMTYPE_KEY);
                const bool should_grant =
                    (silent || is_key) &&
                    item_form_id != 0 &&
                    item_form_id != offsets::FORM_CAPS_SEPTIMS &&
                    count <= offsets::ITEM_GRANT_MAX_COUNT;

                if (should_grant) {
                    FW_LOG("[worldstate] ObjectRef.AddItem (PC grant) "
                           "item=0x%X count=%d silent=%d key=%d",
                           item_form_id, count, silent ? 1 : 0,
                           is_key ? 1 : 0);
                    fw::net::client().enqueue_item_grant(
                        item_form_id, static_cast<std::int32_t>(count),
                        silent || is_key);
                }
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        FW_ERR("[worldstate] SEH in detour_objectref_additem");
    }
    if (g_orig_objectref_additem) {
        return g_orig_objectref_additem(vm, stack_id, self, item, count, silent);
    }
    return false;
}

} // namespace

bool install_worldstate_hooks(std::uintptr_t module_base) {
    bool all_ok = true;

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_GLOBALVAR_SETVALUE_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_global_set_value),
            reinterpret_cast<void**>(&g_orig_global_set_value));
        if (!ok) {
            FW_ERR("[worldstate] GlobalVar.SetValue hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] GlobalVar.SetValue hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_GLOBALVAR_SETVALUE_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_QUEST_SETSTAGE_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_quest_set_stage),
            reinterpret_cast<void**>(&g_orig_quest_set_stage));
        if (!ok) {
            FW_ERR("[worldstate] Quest.SetCurrentStageID hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] Quest.SetCurrentStageID hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_QUEST_SETSTAGE_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_ACTOR_SETFACTIONRANK_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_actor_set_faction_rank),
            reinterpret_cast<void**>(&g_orig_actor_set_faction));
        if (!ok) {
            FW_ERR("[worldstate] Actor.SetFactionRank hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] Actor.SetFactionRank hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_ACTOR_SETFACTIONRANK_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_REWARD_PLAYER_XP_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_reward_player_xp),
            reinterpret_cast<void**>(&g_orig_reward_player_xp));
        if (!ok) {
            FW_ERR("[worldstate] Game.RewardPlayerXP hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] Game.RewardPlayerXP hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_REWARD_PLAYER_XP_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_WEATHER_SETACTIVE_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_weather_set_active),
            reinterpret_cast<void**>(&g_orig_weather_set_active));
        if (!ok) {
            FW_ERR("[worldstate] Weather.SetActive hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] Weather.SetActive hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_WEATHER_SETACTIVE_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_SET_PLAYER_TEAMMATE_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_set_player_teammate),
            reinterpret_cast<void**>(&g_orig_set_player_teammate));
        if (!ok) {
            FW_ERR("[worldstate] SetPlayerTeammate hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] SetPlayerTeammate hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_SET_PLAYER_TEAMMATE_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_CELL_SETCLEARED_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_cell_set_cleared),
            reinterpret_cast<void**>(&g_orig_cell_set_cleared));
        if (!ok) {
            FW_ERR("[worldstate] Cell.SetCleared hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] Cell.SetCleared hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_CELL_SETCLEARED_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_SET_RELATIONSHIP_RANK_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_set_relationship_rank),
            reinterpret_cast<void**>(&g_orig_set_relationship_rank));
        if (!ok) {
            FW_ERR("[worldstate] SetRelationshipRank hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] SetRelationshipRank hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_SET_RELATIONSHIP_RANK_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_PASS_TIME_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_pass_time),
            reinterpret_cast<void**>(&g_orig_pass_time));
        if (!ok) {
            FW_ERR("[worldstate] Game.PassTime hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] Game.PassTime hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_PASS_TIME_RVA));
        }
    }

    {
        const auto target_ea =
            module_base + offsets::PAPYRUS_OBJECTREF_ADDITEM_RVA;
        const bool ok = install(
            reinterpret_cast<void*>(target_ea),
            reinterpret_cast<void*>(&detour_objectref_additem),
            reinterpret_cast<void**>(&g_orig_objectref_additem));
        if (!ok) {
            FW_ERR("[worldstate] ObjectRef.AddItem hook FAILED at 0x%llX",
                   static_cast<unsigned long long>(target_ea));
            all_ok = false;
        } else {
            FW_LOG("[worldstate] ObjectRef.AddItem hook OK RVA 0x%lX",
                   static_cast<unsigned long>(
                       offsets::PAPYRUS_OBJECTREF_ADDITEM_RVA));
        }
    }

    return all_ok;
}

} // namespace fw::hooks
