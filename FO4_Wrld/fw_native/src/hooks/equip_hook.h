// M9 equipment-event sender hook.
// Always available for release-grade co-op (observe + equip apply path).

#pragma once

#include <windows.h>
#include <cstdint>

namespace fw::hooks {

bool install_equip_hook(std::uintptr_t module_base);

// Deferred mesh-tx (M9 w4) — message IDs + handlers used by main_menu_hook.
constexpr UINT FW_MSG_DEFERRED_MESH_TX = WM_APP + 0x4E;
void on_deferred_mesh_tx_message();

constexpr UINT FW_MSG_AUTO_RE_EQUIP = WM_APP + 0x4F;
void on_auto_re_equip_message(WPARAM);

} // namespace fw::hooks
