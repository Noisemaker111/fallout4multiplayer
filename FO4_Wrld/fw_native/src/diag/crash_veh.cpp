#include "crash_veh.h"

#include <windows.h>
#include <atomic>
#include <cstdint>

#include "../log.h"

// MinHook's length disassembler — already linked via minhook.x64.lib.
// Used only to skip one faulting instruction on a known boot AV.
extern "C" {
#include "../../deps/MinHook/src/hde/hde64.h"
}

namespace fw::diag {

namespace {

std::atomic<std::uint64_t> g_av_count{0};
std::atomic<std::uint64_t> g_boot_skip_count{0};
std::uintptr_t g_game_base = 0;
std::uintptr_t g_game_end  = 0;
std::uintptr_t g_self_base = 0;
std::uintptr_t g_self_end  = 0;
DWORD g_boot_tick_ms = 0;  // GetTickCount at install; boot window = 45s
// Tight-loop breaker: consecutive skips inside the same ~4KB page.
std::uintptr_t g_loop_page = 0;
std::uint32_t  g_loop_streak = 0;

// Fake "this" for the known boot null-deref function.
// Layout:
//   [0x0000] object header — slot 0 = &g_boot_dummy_vtable
//   [0x0008..] child / field pointers → interior of this buffer
//   [0x1000] g_boot_dummy_vtable[256] — every slot = &boot_dummy_method
//
// boot_dummy_method is a real executable stub that returns 0 so virtual
// calls through the dummy do not DEP-fault (that killed the process
// after BOOT-DUMMY re-exec with no second AV logged).
alignas(64) std::uint8_t g_boot_dummy_this[0x4000] = {};
alignas(64) void* g_boot_dummy_vtable[256] = {};
std::atomic<bool> g_boot_dummy_seeded{false};

// Must be extern "C" + no C++ linkage so the address is a plain code ptr.
extern "C" std::uint64_t __fastcall boot_dummy_method(void* /*this_*/) noexcept {
    return 0;
}

void seed_boot_dummy_once() noexcept {
    bool expected = false;
    if (!g_boot_dummy_seeded.compare_exchange_strong(expected, true))
        return;
    for (auto& slot : g_boot_dummy_vtable) {
        slot = reinterpret_cast<void*>(&boot_dummy_method);
    }
    auto* base = g_boot_dummy_this;
    auto* child = base + 0x200;
    // this→vtable
    *reinterpret_cast<void**>(base + 0) = &g_boot_dummy_vtable[0];
    // Common pointer offsets → child (also vtable-bearing).
    // Cover every 8-byte slot through 0x200 (child lives at +0x200).
    // Live smokes fault at +0x20, +0x180, +0x1D0, +0x368 — pad deep.
    *reinterpret_cast<void**>(child + 0) = &g_boot_dummy_vtable[0];
    for (std::size_t off = 0x08; off + sizeof(void*) <= 0x200; off += 8) {
        *reinterpret_cast<void**>(base + off) = child;
        *reinterpret_cast<void**>(child + off) = child;
    }
    // Deep fields past the child region → still point at child so
    // [this+0x1D0]/[this+0x368] reads are non-null.
    for (std::size_t off = 0x200; off + sizeof(void*) <= 0x800; off += 8) {
        *reinterpret_cast<void**>(base + off) = child;
    }
}

// 2026-08-02: every 1.10.163 smoke dies ~1–2s after the main window
// appears with AV at game RVA 0x9E5CC0 (null child, fault READ addr=0x20).
// Sibling at 0xA01FB0 has the same pattern. Both get early-return during
// the boot window so the main menu can stay up for LoadGame / singleton
// re-derive. Cap skips so a real death spiral still dies.
constexpr std::uintptr_t kBootAvFnStart  = 0x9E5C90;
constexpr std::uintptr_t kBootAvFnEnd    = 0x9E5EA3;
constexpr std::uintptr_t kBootAvFn2Start = 0xA01FB0;
constexpr std::uintptr_t kBootAvFn2End   = 0xA020A9;
constexpr std::uint64_t  kBootAvMaxSkips = 16384;

bool is_game_code(std::uintptr_t addr) noexcept {
    if (addr < g_game_base || addr >= g_game_end) return false;
    // Reject .data / .rdata: FO4 code lives well below the big data
    // blob (vtables ~0x2Dxxxxx). A false ret into data EXECs and kills.
    const auto rva = addr - g_game_base;
    if (rva >= 0x02C00000) return false;
    MEMORY_BASIC_INFORMATION mbi{};
    if (!VirtualQuery(reinterpret_cast<const void*>(addr), &mbi,
                      sizeof(mbi))) {
        return false;
    }
    const DWORD p = mbi.Protect & 0xFF;
    return p == PAGE_EXECUTE || p == PAGE_EXECUTE_READ ||
           p == PAGE_EXECUTE_READWRITE || p == PAGE_EXECUTE_WRITECOPY;
}

// Try common post-prologue ret slots; return first executable game addr.
bool try_boot_retnull(EXCEPTION_POINTERS* info, std::uintptr_t rsp,
                      std::uint64_t* out_ret) noexcept {
    // Common layouts seen in these two fns:
    //   push rdi; sub rsp, 0x60  → ret @ +0x68
    //   push rdi; sub rsp, 0x40  → ret @ +0x48
    //   push rdi; sub rsp, 0x20  → ret @ +0x28
    const std::uint32_t ret_offs[] = {0x68, 0x48, 0x28, 0x38, 0x58, 0x78};
    __try {
        const auto* sp = reinterpret_cast<const std::uint64_t*>(rsp);
        for (const auto off : ret_offs) {
            const auto ret = sp[off / 8];
            if (is_game_code(static_cast<std::uintptr_t>(ret))) {
                info->ContextRecord->Rax = 0;
                info->ContextRecord->Rip = static_cast<DWORD64>(ret);
                info->ContextRecord->Rsp =
                    static_cast<DWORD64>(rsp + off + 8);
                *out_ret = ret;
                return true;
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return false;
}

LONG WINAPI veh_handler(EXCEPTION_POINTERS* info) noexcept {
    if (!info || !info->ExceptionRecord || !info->ContextRecord) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    const DWORD code = info->ExceptionRecord->ExceptionCode;
    // Only access violations — skip C++/SEH expected exceptions
    // (0xE06D7363 C++, 0x80000003 BP, etc).
    if (code != EXCEPTION_ACCESS_VIOLATION) {
        return EXCEPTION_CONTINUE_SEARCH;
    }

    const auto rip = static_cast<std::uintptr_t>(info->ContextRecord->Rip);

    // Skip faults inside our own DLL — those are the SEH-caged
    // safe_read_X helpers doing intentional unsafe-deref tests. Their
    // __try/__except handlers will catch them; we'd just spam the log.
    if (g_self_base != 0 && rip >= g_self_base && rip < g_self_end) {
        return EXCEPTION_CONTINUE_SEARCH;
    }

    const auto n = g_av_count.fetch_add(1, std::memory_order_relaxed);
    // Do NOT early-return on count here — boot-window skip must still run
    // even after hundreds of AVs (tight loops at e.g. 0x21AB0xx).

    const auto rsp = static_cast<std::uintptr_t>(info->ContextRecord->Rsp);
    const auto rbp = static_cast<std::uintptr_t>(info->ContextRecord->Rbp);
    const auto rcx = static_cast<std::uintptr_t>(info->ContextRecord->Rcx);
    const auto rdx = static_cast<std::uintptr_t>(info->ContextRecord->Rdx);
    const auto r8  = static_cast<std::uintptr_t>(info->ContextRecord->R8);

    const auto fault_kind = info->ExceptionRecord->ExceptionInformation[0];
    const auto fault_addr = info->ExceptionRecord->ExceptionInformation[1];

    const bool rip_in_game =
        g_game_base != 0 && rip >= g_game_base && rip < g_game_end;
    const std::uintptr_t rva =
        rip_in_game ? (rip - g_game_base) : 0;

    // Throttle full AV dumps — boot-window skip can fire hundreds of times.
    if (n < 12 || (n % 64) == 0) {
        FW_ERR("[crash-veh] AV #%llu rip=0x%llX RVA=0x%llX in_game=%d "
               "fault=%s addr=0x%llX rsp=0x%llX rbp=0x%llX "
               "rcx=0x%llX rdx=0x%llX r8=0x%llX",
               static_cast<unsigned long long>(n),
               static_cast<unsigned long long>(rip),
               static_cast<unsigned long long>(rva),
               rip_in_game ? 1 : 0,
               fault_kind == 0 ? "READ" : (fault_kind == 1 ? "WRITE" :
                                           (fault_kind == 8 ? "EXEC" : "?")),
               static_cast<unsigned long long>(fault_addr),
               static_cast<unsigned long long>(rsp),
               static_cast<unsigned long long>(rbp),
               static_cast<unsigned long long>(rcx),
               static_cast<unsigned long long>(rdx),
               static_cast<unsigned long long>(r8));
        __try {
            const std::uint64_t* sp =
                reinterpret_cast<const std::uint64_t*>(rsp);
            FW_ERR("[crash-veh]   stack: %016llX %016llX %016llX %016llX "
                   "%016llX %016llX %016llX %016llX",
                   sp[0], sp[1], sp[2], sp[3], sp[4], sp[5], sp[6], sp[7]);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            FW_ERR("[crash-veh]   stack: <unreadable>");
        }
    }

    // First 45s after VEH install: keep the process alive through the
    // boot null-walk long enough for hwnd-poll LoadGame to fire.
    // (Log claimed 45000ms but the gate was 8000 — process died mid-cascade.)
    constexpr DWORD kBootWindowMs = 45000;
    const DWORD now = GetTickCount();
    const bool boot =
        g_boot_tick_ms != 0 && (now - g_boot_tick_ms) < kBootWindowMs;
    const bool low_or_poison =
        fault_addr < 0x10000 ||
        fault_addr == static_cast<std::uintptr_t>(
            static_cast<std::intptr_t>(-1));
    const bool in_boot_av_fn =
        (rva >= kBootAvFnStart && rva < kBootAvFnEnd) ||
        (rva >= kBootAvFn2Start && rva < kBootAvFn2End);

    if (boot && rip_in_game && (low_or_poison || in_boot_av_fn)) {
        const auto skips =
            g_boot_skip_count.fetch_add(1, std::memory_order_relaxed);
        if (skips < kBootAvMaxSkips) {
            // Prefer early-return out of the known null-child helpers
            // (and any boot low-page AV): keeps the process at main menu.
            if (in_boot_av_fn || low_or_poison) {
                std::uint64_t ret = 0;
                if (try_boot_retnull(info, rsp, &ret)) {
                    if (skips < 12 || (skips % 64) == 0) {
                        FW_WRN("[crash-veh] BOOT-RETNULL RVA 0x%llX "
                               "ret=0x%llX skip#%llu",
                               static_cast<unsigned long long>(rva),
                               static_cast<unsigned long long>(ret),
                               static_cast<unsigned long long>(skips + 1));
                    }
                    return EXCEPTION_CONTINUE_EXECUTION;
                }
            }
            // Fallback: inject dummy + skip faulting insn.
            if (rcx == 0 || low_or_poison) {
                seed_boot_dummy_once();
                info->ContextRecord->Rcx =
                    reinterpret_cast<DWORD64>(&g_boot_dummy_this[0]);
                if (info->ContextRecord->Rax < 0x10000) {
                    info->ContextRecord->Rax =
                        reinterpret_cast<DWORD64>(
                            &g_boot_dummy_this[0x200]);
                }
            }
            unsigned len = 4;
            __try {
                hde64s hs{};
                len = hde64_disasm(
                    reinterpret_cast<const void*>(rip), &hs);
                if ((hs.flags & F_ERROR) || len == 0 || len > 15)
                    len = 4;
            } __except (EXCEPTION_EXECUTE_HANDLER) {
                len = 4;
            }
            info->ContextRecord->Rip =
                static_cast<DWORD64>(rip + len);
            const std::uintptr_t page = rip & ~std::uintptr_t{0xFFF};
            if (page == g_loop_page) {
                ++g_loop_streak;
            } else {
                g_loop_page = page;
                g_loop_streak = 1;
            }
            if (g_loop_streak > 64) {
                info->ContextRecord->Rip =
                    static_cast<DWORD64>((page + 0x1000));
                g_loop_streak = 0;
                FW_WRN("[crash-veh] BOOT-PAGE-BREAK page=0x%llX",
                       static_cast<unsigned long long>(page));
            }
            if (skips < 12 || (skips % 64) == 0) {
                FW_WRN("[crash-veh] BOOT-SKIP addr=0x%llX RVA=0x%llX "
                       "skip#%llu",
                       static_cast<unsigned long long>(fault_addr),
                       static_cast<unsigned long long>(rva),
                       static_cast<unsigned long long>(skips + 1));
            }
            return EXCEPTION_CONTINUE_EXECUTION;
        }
    }

    return EXCEPTION_CONTINUE_SEARCH;
}

// B6.6w5 Build 23 — backup unhandled-exception filter. The VEH only
// catches exceptions that go through the OS exception dispatcher
// (AVs, divide-by-zero, etc). Heap corruption / fast-fail (e.g.,
// `RtlReportFatalFailure`, `__fastfail`, double-free detection by
// RtlpHeap*) calls `TerminateProcess` DIRECTLY — VEH never fires.
//
// Build 22 log showed VEH missed the SUBSTITUTE crash (28 unrelated
// AVs from `fire_actor_weapon` were caught, but the actual game
// crash @ 19:01:55.004 left no VEH entry → not a regular AV).
//
// `SetUnhandledExceptionFilter` runs as a last-resort for the regular
// exception path. It WON'T catch `__fastfail` either — Windows 10+
// fast-fail bypasses everything by design — but it's still useful for
// the cases where SEH __try/__except didn't catch something.
//
// For true `__fastfail` we'd need WerRegisterRuntimeExceptionModule or
// a debugger attached. Not implementing that here.
LONG WINAPI unhandled_filter(EXCEPTION_POINTERS* info) noexcept {
    if (!info || !info->ExceptionRecord) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    const DWORD code = info->ExceptionRecord->ExceptionCode;
    const auto rip = info->ContextRecord
        ? static_cast<std::uintptr_t>(info->ContextRecord->Rip)
        : 0u;
    const std::uintptr_t rva =
        g_game_base != 0 && rip >= g_game_base && rip < g_game_end
            ? (rip - g_game_base)
            : 0;
    FW_ERR("[crash-veh] UNHANDLED code=0x%08X rip=0x%llX RVA=0x%llX",
           code,
           static_cast<unsigned long long>(rip),
           static_cast<unsigned long long>(rva));
    return EXCEPTION_CONTINUE_SEARCH;
}

} // namespace

void install_crash_veh(std::uintptr_t module_base) {
    g_game_base = module_base;
    // Fallout4.exe is ~70MB; use 256MB upper bound. False positives only
    // if an allocation lands within that span, which is fine for our
    // "is RIP in game code?" filter purpose.
    g_game_end  = module_base + 0x10000000;

    // Resolve our own DLL base/size via a pointer inside this TU so we
    // can skip RIPs from our own SEH-caged helpers.
    HMODULE self = nullptr;
    if (GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(&install_crash_veh),
            &self) && self) {
        g_self_base = reinterpret_cast<std::uintptr_t>(self);
        g_self_end  = g_self_base + 0x00400000;  // 4MB upper bound; our DLL is <2MB
    }

    AddVectoredExceptionHandler(/*first=*/1, veh_handler);

    // Backup: runs only if no SEH __try/__except caught the exception
    // AND the VEH chain didn't return EXCEPTION_CONTINUE_EXECUTION.
    // For AVs in engine code that nobody handles, this fires JUST
    // before the OS terminates the process.
    SetUnhandledExceptionFilter(unhandled_filter);

    g_boot_tick_ms = GetTickCount();

    FW_LOG("[crash-veh] installed (VEH priority=first + Unhandled filter) "
           "game=[0x%llX..0x%llX] self=[0x%llX..0x%llX] "
           "boot_av_fn=[0x%llX..0x%llX) boot_window_ms=45000",
           static_cast<unsigned long long>(g_game_base),
           static_cast<unsigned long long>(g_game_end),
           static_cast<unsigned long long>(g_self_base),
           static_cast<unsigned long long>(g_self_end),
           static_cast<unsigned long long>(kBootAvFnStart),
           static_cast<unsigned long long>(kBootAvFnEnd));
}

} // namespace fw::diag
