// Hook on TESObjectREFR::AddObjectToContainer (vt[0x7A]).
// Captures TAKE (and some PUT) paths for container co-op sync.
//
// Always compiled for release-grade co-op (no longer FW_MINIMAL-stubbed).

#pragma once

#include <cstdint>

namespace fw::hooks {

bool install_container_hook(std::uintptr_t module_base);

// Feedback-loop guard for remote container/door/lock/equip applies.
extern thread_local bool tls_applying_remote;

struct ApplyingRemoteGuard {
    ApplyingRemoteGuard();
    ~ApplyingRemoteGuard();
    ApplyingRemoteGuard(const ApplyingRemoteGuard&) = delete;
    ApplyingRemoteGuard& operator=(const ApplyingRemoteGuard&) = delete;
};

} // namespace fw::hooks
