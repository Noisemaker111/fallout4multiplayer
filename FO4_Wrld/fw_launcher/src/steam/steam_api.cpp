#include "steam_api.h"

#include <windows.h>

#include <cstdio>
#include <cstdlib>

#include "../fom_util.h"

namespace fom::steam {

namespace {

// ---- flat-API function pointer types -------------------------------------
// Names and signatures verified against the exports of a shipping
// steam_api64.dll (SDK 1.60-era). See docs/STEAM_SPACEWAR.md.

using PFN_InitFlat        = int  (__cdecl*)(char* /*SteamErrMsg[1024]*/);
using PFN_Init            = bool (__cdecl*)();
using PFN_Shutdown        = void (__cdecl*)();
using PFN_GetHSteamPipe   = std::int32_t (__cdecl*)();
using PFN_GetHSteamUser   = std::int32_t (__cdecl*)();

using PFN_MD_Init         = void (__cdecl*)();
using PFN_MD_RunFrame     = void (__cdecl*)(std::int32_t);
using PFN_MD_GetNext      = bool (__cdecl*)(std::int32_t, CallbackMsg*);
using PFN_MD_FreeLast     = void (__cdecl*)(std::int32_t);
using PFN_MD_GetResult    = bool (__cdecl*)(std::int32_t, std::uint64_t, void*,
                                            int, int, bool*);

using PFN_FindOrCreateUser = void* (__cdecl*)(std::int32_t, const char*);
using PFN_Accessor         = void* (__cdecl*)();

using PFN_User_GetSteamID  = std::uint64_t (__cdecl*)(void*);
using PFN_Apps_BIsSubApp   = bool (__cdecl*)(void*, std::uint32_t);

using PFN_MM_CreateLobby   = std::uint64_t (__cdecl*)(void*, int, int);
using PFN_MM_JoinLobby     = std::uint64_t (__cdecl*)(void*, std::uint64_t);
using PFN_MM_LeaveLobby    = void (__cdecl*)(void*, std::uint64_t);
using PFN_MM_SetLobbyData  = bool (__cdecl*)(void*, std::uint64_t, const char*,
                                             const char*);
using PFN_MM_GetLobbyData  = const char* (__cdecl*)(void*, std::uint64_t,
                                                    const char*);
using PFN_MM_SetJoinable   = bool (__cdecl*)(void*, std::uint64_t, bool);
using PFN_MM_SetLobbyType  = bool (__cdecl*)(void*, std::uint64_t, int);
using PFN_MM_NumMembers    = int  (__cdecl*)(void*, std::uint64_t);
using PFN_MM_MemberByIndex = std::uint64_t (__cdecl*)(void*, std::uint64_t, int);
using PFN_MM_GetOwner      = std::uint64_t (__cdecl*)(void*, std::uint64_t);
using PFN_MM_RequestData   = bool (__cdecl*)(void*, std::uint64_t);

using PFN_Fr_InviteOverlay = void (__cdecl*)(void*, std::uint64_t);
using PFN_Fr_SetRichPres   = bool (__cdecl*)(void*, const char*, const char*);
using PFN_Fr_ClearRichPres = void (__cdecl*)(void*);
using PFN_Fr_FriendName    = const char* (__cdecl*)(void*, std::uint64_t);
using PFN_Fr_PersonaName   = const char* (__cdecl*)(void*);

// NOTE: the flat API takes SteamNetworkingIdentity BY VALUE (Valve's
// generator expands `const SteamNetworkingIdentity&` into a value param).
// Declaring it by value here makes MSVC emit the same MS-x64 hidden-pointer
// convention the SDK build used.
using PFN_NM_SendToUser    = int  (__cdecl*)(void*, NetIdentity, const void*,
                                             std::uint32_t, int, int);
using PFN_NM_ReceiveOnChan = int  (__cdecl*)(void*, int, NetMessage**, int);
using PFN_NM_AcceptSession = bool (__cdecl*)(void*, NetIdentity);
using PFN_NM_CloseSession  = bool (__cdecl*)(void*, NetIdentity);
using PFN_Msg_Release      = void (__cdecl*)(NetMessage*);

using PFN_NU_InitRelay     = void (__cdecl*)(void*);
using PFN_NU_RelayStatus   = int  (__cdecl*)(void*, void*);

// Every export this build depends on. If a candidate DLL is missing any of
// them it is too old (ISteamNetworkingMessages landed in SDK 1.46, manual
// dispatch in 1.47) and we must not load it.
const char* const kRequiredExports[] = {
    "SteamAPI_ManualDispatch_Init",
    "SteamAPI_ManualDispatch_RunFrame",
    "SteamAPI_ManualDispatch_GetNextCallback",
    "SteamAPI_ManualDispatch_FreeLastCallback",
    "SteamAPI_ManualDispatch_GetAPICallResult",
    "SteamAPI_ISteamNetworkingMessages_SendMessageToUser",
    "SteamAPI_ISteamNetworkingMessages_ReceiveMessagesOnChannel",
    "SteamAPI_ISteamNetworkingMessages_AcceptSessionWithUser",
    "SteamAPI_SteamNetworkingMessage_t_Release",
    "SteamAPI_ISteamMatchmaking_CreateLobby",
};

bool has_required_exports(HMODULE m) {
    for (const char* n : kRequiredExports) {
        if (!GetProcAddress(m, n)) return false;
    }
    return true;
}

void set_env(const wchar_t* k, const wchar_t* v) {
    SetEnvironmentVariableW(k, v);
}

void write_steam_appid(const std::wstring& dir) {
    const std::wstring path = fom::join_path(dir, L"steam_appid.txt");
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                           nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    const char body[] = "480\n";
    DWORD w = 0;
    WriteFile(h, body, static_cast<DWORD>(sizeof(body) - 1), &w, nullptr);
    CloseHandle(h);
}

// Depth-limited hunt for steam_api64.dll under `dir`. Games bury the
// redistributable in wildly different places (root, bin\x64,
// *_Data\Plugins\x86_64, Engine\Binaries\ThirdParty\...), so a bounded walk
// beats a hand-written list of layouts. Stops at the first hit per game and
// respects a global budget so scanning a big library stays sub-second.
void scan_for_steam_dll(const std::wstring& dir, int depth_left,
                        int* budget, std::vector<std::wstring>* out) {
    if (depth_left < 0 || *budget <= 0) return;
    --*budget;

    const std::wstring direct = fom::join_path(dir, L"steam_api64.dll");
    if (fom::file_exists(direct)) {
        out->push_back(direct);
        return;  // one per game is plenty
    }
    if (depth_left == 0) return;

    WIN32_FIND_DATAW fd{};
    HANDLE h = FindFirstFileW(fom::join_path(dir, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        if (fd.cFileName[0] == L'.') continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) continue;
        scan_for_steam_dll(fom::join_path(dir, fd.cFileName), depth_left - 1,
                           budget, out);
        if (*budget <= 0) break;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

// Enumerate Steam library roots from the client's libraryfolders.vdf.
void collect_steam_libraries(std::vector<std::wstring>* out) {
    const wchar_t* roots[] = {
        L"C:\\Program Files (x86)\\Steam",
        L"C:\\Games\\Steam",
        L"C:\\Steam",
        L"D:\\Steam",
        L"D:\\SteamLibrary",
        L"E:\\Steam",
        L"E:\\SteamLibrary",
    };
    for (const auto* r : roots) {
        const std::wstring common =
            fom::join_path(fom::join_path(r, L"steamapps"), L"common");
        const DWORD a = GetFileAttributesW(common.c_str());
        if (a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY))
            out->push_back(common);
    }
}

}  // namespace

// ------------------------------------------------------------------ finding

std::vector<std::wstring> find_candidate_steam_dlls(const std::wstring& exe_dir) {
    std::vector<std::wstring> out;

    // 1. Shipped with the pack - the path we actually want players on.
    const std::wstring local = fom::join_path(exe_dir, L"steam_api64.dll");
    if (fom::file_exists(local)) out.push_back(local);

    const std::wstring in_payload =
        fom::join_path(fom::join_path(exe_dir, L"payload"), L"steam_api64.dll");
    if (fom::file_exists(in_payload)) out.push_back(in_payload);

    // 2. Explicit override for developers.
    wchar_t env[MAX_PATH] = {};
    if (GetEnvironmentVariableW(L"FOM_STEAM_API_DLL", env, MAX_PATH) > 0) {
        if (fom::file_exists(env)) out.push_back(env);
    }

    // 3. Dev fallback: any installed Steam game ships a redistributable
    //    steam_api64.dll. Scanning for one lets a developer test the Steam
    //    path before the pack has its own copy. Player packs always hit (1).
    std::vector<std::wstring> libs;
    collect_steam_libraries(&libs);
    int budget = 6000;  // directories we are willing to stat, total
    for (const auto& lib : libs) {
        WIN32_FIND_DATAW fd{};
        HANDLE h = FindFirstFileW(fom::join_path(lib, L"*").c_str(), &fd);
        if (h == INVALID_HANDLE_VALUE) continue;
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
            if (fd.cFileName[0] == L'.') continue;
            scan_for_steam_dll(fom::join_path(lib, fd.cFileName),
                               /*depth_left=*/5, &budget, &out);
        } while (FindNextFileW(h, &fd) && budget > 0);
        FindClose(h);
        if (budget <= 0) break;
    }
    return out;
}

// -------------------------------------------------------------------- init

SteamApi::~SteamApi() {
    shutdown();
}

void* SteamApi::proc(const char* name) const {
    if (!dll_) return nullptr;
    return reinterpret_cast<void*>(
        GetProcAddress(static_cast<HMODULE>(dll_), name));
}

// Resolve an interface pointer. Tries the versioned flat accessor exports
// (newest first), then falls back to SteamInternal_FindOrCreateUserInterface
// with the canonical version string - the same two-path approach
// fw_native/src/steam/steam_id.cpp uses.
void* SteamApi::resolve_interface(const char* prefix,
                                  const char* version_string,
                                  int version_lo, int version_hi,
                                  bool steamapi_suffix) const {
    for (int v = version_hi; v >= version_lo; --v) {
        char name[160];
        if (steamapi_suffix)
            std::snprintf(name, sizeof(name), "SteamAPI_%s_SteamAPI_v%03d",
                          prefix, v);
        else
            std::snprintf(name, sizeof(name), "SteamAPI_%s_v%03d", prefix, v);
        if (auto* fn = reinterpret_cast<PFN_Accessor>(proc(name))) {
            void* p = fn();
            if (p) return p;
        }
    }

    auto* find = reinterpret_cast<PFN_FindOrCreateUser>(
        proc("SteamInternal_FindOrCreateUserInterface"));
    auto* huser = reinterpret_cast<PFN_GetHSteamUser>(
        proc("SteamAPI_GetHSteamUser"));
    if (find && huser && version_string) {
        return find(huser(), version_string);
    }
    return nullptr;
}

InitResult SteamApi::init(const std::wstring& exe_dir) {
    InitResult r{};

    // Force the Spacewar app identity BEFORE SteamAPI_Init. Env vars win
    // over steam_appid.txt, but we write both: steam_appid.txt is read
    // relative to the current directory, so pin that to exe_dir too.
    set_env(L"SteamAppId", L"480");
    set_env(L"SteamGameId", L"480");
    write_steam_appid(exe_dir);
    SetCurrentDirectoryW(exe_dir.c_str());

    const auto candidates = find_candidate_steam_dlls(exe_dir);
    if (candidates.empty()) {
        r.error =
            "no steam_api64.dll found. Put the Steamworks redistributable "
            "steam_api64.dll next to FoM.exe (see docs/STEAM_SPACEWAR.md).";
        return r;
    }

    const std::wstring shipped = fom::join_path(exe_dir, L"steam_api64.dll");
    const std::wstring shipped_payload =
        fom::join_path(fom::join_path(exe_dir, L"payload"), L"steam_api64.dll");
    wchar_t override_path[MAX_PATH] = {};
    GetEnvironmentVariableW(L"FOM_STEAM_API_DLL", override_path, MAX_PATH);

    for (const auto& path : candidates) {
        HMODULE m = LoadLibraryW(path.c_str());
        if (!m) continue;
        if (!has_required_exports(m)) {
            FreeLibrary(m);
            continue;
        }
        dll_ = m;
        owns_dll_ = true;
        r.dll_path = path;
        if (path == shipped || path == shipped_payload)
            r.source = DllSource::NextToExe;
        else if (override_path[0] && path == override_path)
            r.source = DllSource::EnvOverride;
        else
            r.source = DllSource::Discovered;
        break;
    }

    if (!dll_) {
        r.error =
            "every steam_api64.dll found is too old for Steam P2P "
            "(needs SDK 1.47+ with ISteamNetworkingMessages). Ship the "
            "Steamworks redistributable next to FoM.exe.";
        return r;
    }

    // SteamAPI_Init: modern DLLs export SteamAPI_InitFlat (returns
    // ESteamAPIInitResult, 0 == OK); older ones only SteamAPI_Init (bool).
    bool inited = false;
    if (auto* init_flat = reinterpret_cast<PFN_InitFlat>(
            proc("SteamAPI_InitFlat"))) {
        char errmsg[1024] = {};
        const int rc = init_flat(errmsg);
        inited = (rc == 0);
        if (!inited) {
            r.error = std::string("SteamAPI_Init failed: ") +
                      (errmsg[0] ? errmsg : "unknown");
        }
    } else if (auto* init_old = reinterpret_cast<PFN_Init>(
                   proc("SteamAPI_Init"))) {
        inited = init_old();
        if (!inited) r.error = "SteamAPI_Init returned false";
    } else {
        r.error = "steam_api64.dll exports no SteamAPI_Init";
    }

    if (!inited) {
        if (r.error.empty()) r.error = "SteamAPI_Init failed";
        r.error += " (is the Steam client running and signed in?)";
        shutdown();
        return r;
    }

    auto* get_pipe = reinterpret_cast<PFN_GetHSteamPipe>(
        proc("SteamAPI_GetHSteamPipe"));
    pipe_ = get_pipe ? get_pipe() : 0;
    if (pipe_ == 0) {
        r.error = "SteamAPI_GetHSteamPipe returned 0";
        shutdown();
        return r;
    }

    // Switch to manual dispatch. From here SteamAPI_RunCallbacks() must
    // never be called on this process.
    if (auto* md_init = reinterpret_cast<PFN_MD_Init>(
            proc("SteamAPI_ManualDispatch_Init"))) {
        md_init();
    }

    i_user_        = resolve_interface("SteamUser", "SteamUser023", 19, 23, false);
    i_friends_     = resolve_interface("SteamFriends", "SteamFriends017", 15, 18, false);
    i_matchmaking_ = resolve_interface("SteamMatchmaking", "SteamMatchMaking009", 9, 9, false);
    i_apps_        = resolve_interface("SteamApps", "STEAMAPPS_INTERFACE_VERSION008", 6, 8, false);
    i_messages_    = resolve_interface("SteamNetworkingMessages", "SteamNetworkingMessages002", 2, 2, true);
    i_utils_net_   = resolve_interface("SteamNetworkingUtils", "SteamNetworkingUtils004", 3, 4, true);

    if (!i_matchmaking_ || !i_messages_) {
        r.error = "could not resolve ISteamMatchmaking / "
                  "ISteamNetworkingMessages from steam_api64.dll";
        shutdown();
        return r;
    }

    if (i_user_) {
        if (auto* get_id = reinterpret_cast<PFN_User_GetSteamID>(
                proc("SteamAPI_ISteamUser_GetSteamID"))) {
            local_steam_id_ = get_id(i_user_);
        }
    }

    // Start warming Steam's relay network now so the first P2P send does not
    // pay the full handshake latency. Safe no-op if unavailable.
    if (i_utils_net_) {
        if (auto* relay = reinterpret_cast<PFN_NU_InitRelay>(
                proc("SteamAPI_ISteamNetworkingUtils_InitRelayNetworkAccess"))) {
            relay(i_utils_net_);
        }
    }

    ready_ = true;
    r.ok = true;
    return r;
}

void SteamApi::shutdown() {
    if (dll_) {
        if (ready_) {
            if (auto* sd = reinterpret_cast<PFN_Shutdown>(
                    proc("SteamAPI_Shutdown"))) {
                sd();
            }
        }
        if (owns_dll_) FreeLibrary(static_cast<HMODULE>(dll_));
        dll_ = nullptr;
        owns_dll_ = false;
    }
    ready_ = false;
    pipe_ = 0;
    i_user_ = i_friends_ = i_matchmaking_ = nullptr;
    i_messages_ = i_utils_net_ = i_apps_ = nullptr;
}

// -------------------------------------------------------------------- pump

void SteamApi::pump(ICallbackSink* sink) {
    if (!ready_ || !sink) return;

    auto* run_frame = reinterpret_cast<PFN_MD_RunFrame>(
        proc("SteamAPI_ManualDispatch_RunFrame"));
    auto* get_next = reinterpret_cast<PFN_MD_GetNext>(
        proc("SteamAPI_ManualDispatch_GetNextCallback"));
    auto* free_last = reinterpret_cast<PFN_MD_FreeLast>(
        proc("SteamAPI_ManualDispatch_FreeLastCallback"));
    auto* get_result = reinterpret_cast<PFN_MD_GetResult>(
        proc("SteamAPI_ManualDispatch_GetAPICallResult"));
    if (!run_frame || !get_next || !free_last) return;

    auto dispatch = [&](int id, const void* data, int size) {
        switch (id) {
        case kCB_LobbyCreated:
            if (size >= static_cast<int>(sizeof(LobbyCreated)))
                sink->on_lobby_created(*static_cast<const LobbyCreated*>(data));
            break;
        case kCB_LobbyEnter:
            if (size >= static_cast<int>(sizeof(LobbyEnter)))
                sink->on_lobby_enter(*static_cast<const LobbyEnter*>(data));
            break;
        case kCB_LobbyChatUpdate:
            if (size >= static_cast<int>(sizeof(LobbyChatUpdate)))
                sink->on_lobby_chat_update(
                    *static_cast<const LobbyChatUpdate*>(data));
            break;
        case kCB_GameLobbyJoinRequested:
            if (size >= static_cast<int>(sizeof(GameLobbyJoinRequested)))
                sink->on_join_requested(
                    *static_cast<const GameLobbyJoinRequested*>(data));
            break;
        case kCB_RichPresenceJoinRequested:
            if (size >= static_cast<int>(sizeof(GameRichPresenceJoinRequested)))
                sink->on_rich_presence_join(
                    *static_cast<const GameRichPresenceJoinRequested*>(data));
            break;
        case kCB_MessagesSessionRequest:
            if (size >= static_cast<int>(sizeof(MessagesSessionRequest)))
                sink->on_session_request(
                    *static_cast<const MessagesSessionRequest*>(data));
            break;
        default:
            break;
        }
    };

    run_frame(pipe_);

    CallbackMsg msg{};
    while (get_next(pipe_, &msg)) {
        if (msg.callback == kCB_SteamAPICallCompleted && get_result &&
            msg.param_size >= static_cast<std::int32_t>(sizeof(APICallCompleted))) {
            // Async call result (CreateLobby, JoinLobby, ...). The payload is
            // in Steam's result store, not in the callback message.
            const auto* done =
                reinterpret_cast<const APICallCompleted*>(msg.param);
            std::vector<std::uint8_t> buf(done->param_size ? done->param_size : 1);
            bool failed = false;
            if (get_result(pipe_, done->async_call, buf.data(),
                           static_cast<int>(done->param_size),
                           done->callback, &failed) && !failed) {
                dispatch(done->callback, buf.data(),
                         static_cast<int>(done->param_size));
            }
        } else {
            dispatch(msg.callback, msg.param, msg.param_size);
        }
        free_last(pipe_);
    }
}

// ---------------------------------------------------------------- identity

std::string SteamApi::persona_name(std::uint64_t steam_id) const {
    if (!ready_ || !i_friends_ || steam_id == 0) return {};
    if (steam_id == local_steam_id_) {
        if (auto* fn = reinterpret_cast<PFN_Fr_PersonaName>(
                proc("SteamAPI_ISteamFriends_GetPersonaName"))) {
            const char* n = fn(i_friends_);
            if (n) return n;
        }
    }
    if (auto* fn = reinterpret_cast<PFN_Fr_FriendName>(
            proc("SteamAPI_ISteamFriends_GetFriendPersonaName"))) {
        const char* n = fn(i_friends_, steam_id);
        if (n && *n) return n;
    }
    return {};
}

bool SteamApi::owns_spacewar() const {
    if (!ready_ || !i_apps_) return true;  // can't tell - assume yes
    if (auto* fn = reinterpret_cast<PFN_Apps_BIsSubApp>(
            proc("SteamAPI_ISteamApps_BIsSubscribedApp"))) {
        return fn(i_apps_, kSpacewarAppId);
    }
    return true;
}

// ------------------------------------------------------------- matchmaking

std::uint64_t SteamApi::create_lobby(int lobby_type, int max_members) {
    if (!ready_) return 0;
    auto* fn = reinterpret_cast<PFN_MM_CreateLobby>(
        proc("SteamAPI_ISteamMatchmaking_CreateLobby"));
    return fn ? fn(i_matchmaking_, lobby_type, max_members) : 0;
}

std::uint64_t SteamApi::join_lobby(std::uint64_t lobby) {
    if (!ready_) return 0;
    auto* fn = reinterpret_cast<PFN_MM_JoinLobby>(
        proc("SteamAPI_ISteamMatchmaking_JoinLobby"));
    return fn ? fn(i_matchmaking_, lobby) : 0;
}

void SteamApi::leave_lobby(std::uint64_t lobby) {
    if (!ready_ || lobby == 0) return;
    if (auto* fn = reinterpret_cast<PFN_MM_LeaveLobby>(
            proc("SteamAPI_ISteamMatchmaking_LeaveLobby")))
        fn(i_matchmaking_, lobby);
}

bool SteamApi::set_lobby_data(std::uint64_t lobby, const char* k, const char* v) {
    if (!ready_) return false;
    auto* fn = reinterpret_cast<PFN_MM_SetLobbyData>(
        proc("SteamAPI_ISteamMatchmaking_SetLobbyData"));
    return fn ? fn(i_matchmaking_, lobby, k, v) : false;
}

std::string SteamApi::get_lobby_data(std::uint64_t lobby, const char* k) const {
    if (!ready_) return {};
    auto* fn = reinterpret_cast<PFN_MM_GetLobbyData>(
        proc("SteamAPI_ISteamMatchmaking_GetLobbyData"));
    if (!fn) return {};
    const char* v = fn(i_matchmaking_, lobby, k);
    return v ? std::string(v) : std::string();
}

bool SteamApi::set_lobby_joinable(std::uint64_t lobby, bool joinable) {
    if (!ready_) return false;
    auto* fn = reinterpret_cast<PFN_MM_SetJoinable>(
        proc("SteamAPI_ISteamMatchmaking_SetLobbyJoinable"));
    return fn ? fn(i_matchmaking_, lobby, joinable) : false;
}

bool SteamApi::set_lobby_type(std::uint64_t lobby, int lobby_type) {
    if (!ready_) return false;
    auto* fn = reinterpret_cast<PFN_MM_SetLobbyType>(
        proc("SteamAPI_ISteamMatchmaking_SetLobbyType"));
    return fn ? fn(i_matchmaking_, lobby, lobby_type) : false;
}

int SteamApi::lobby_member_count(std::uint64_t lobby) const {
    if (!ready_) return 0;
    auto* fn = reinterpret_cast<PFN_MM_NumMembers>(
        proc("SteamAPI_ISteamMatchmaking_GetNumLobbyMembers"));
    return fn ? fn(i_matchmaking_, lobby) : 0;
}

std::uint64_t SteamApi::lobby_member(std::uint64_t lobby, int index) const {
    if (!ready_) return 0;
    auto* fn = reinterpret_cast<PFN_MM_MemberByIndex>(
        proc("SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex"));
    return fn ? fn(i_matchmaking_, lobby, index) : 0;
}

std::uint64_t SteamApi::lobby_owner(std::uint64_t lobby) const {
    if (!ready_) return 0;
    auto* fn = reinterpret_cast<PFN_MM_GetOwner>(
        proc("SteamAPI_ISteamMatchmaking_GetLobbyOwner"));
    return fn ? fn(i_matchmaking_, lobby) : 0;
}

bool SteamApi::request_lobby_data(std::uint64_t lobby) {
    if (!ready_) return false;
    auto* fn = reinterpret_cast<PFN_MM_RequestData>(
        proc("SteamAPI_ISteamMatchmaking_RequestLobbyData"));
    return fn ? fn(i_matchmaking_, lobby) : false;
}

void SteamApi::open_invite_overlay(std::uint64_t lobby) {
    if (!ready_ || !i_friends_ || lobby == 0) return;
    if (auto* fn = reinterpret_cast<PFN_Fr_InviteOverlay>(
            proc("SteamAPI_ISteamFriends_ActivateGameOverlayInviteDialog")))
        fn(i_friends_, lobby);
}

bool SteamApi::set_rich_presence(const char* key, const char* value) {
    if (!ready_ || !i_friends_ || !key) return false;
    auto* fn = reinterpret_cast<PFN_Fr_SetRichPres>(
        proc("SteamAPI_ISteamFriends_SetRichPresence"));
    return fn ? fn(i_friends_, key, value) : false;
}

std::string SteamApi::get_rich_presence(std::uint64_t steam_id,
                                        const char* key) const {
    if (!ready_ || !i_friends_ || !key) return {};
    using PFN = const char* (__cdecl*)(void*, std::uint64_t, const char*);
    auto* fn = reinterpret_cast<PFN>(
        proc("SteamAPI_ISteamFriends_GetFriendRichPresence"));
    if (!fn) return {};
    const char* v = fn(i_friends_, steam_id, key);
    return v ? std::string(v) : std::string();
}

void SteamApi::clear_rich_presence() {
    if (!ready_ || !i_friends_) return;
    if (auto* fn = reinterpret_cast<PFN_Fr_ClearRichPres>(
            proc("SteamAPI_ISteamFriends_ClearRichPresence")))
        fn(i_friends_);
}

// ------------------------------------------------------------------ p2p

bool SteamApi::send_to_user(std::uint64_t steam_id, const void* data,
                            std::uint32_t len, int channel) {
    if (!ready_ || steam_id == 0 || len == 0) return false;
    auto* fn = reinterpret_cast<PFN_NM_SendToUser>(
        proc("SteamAPI_ISteamNetworkingMessages_SendMessageToUser"));
    if (!fn) return false;
    const NetIdentity id = NetIdentity::from_steam_id(steam_id);
    const int rc = fn(i_messages_, id, data, len,
                      kSendUnreliable | kSendNoNagle, channel);
    return rc == kEResultOK;
}

int SteamApi::receive_on_channel(int channel, NetMessage** out, int max) {
    if (!ready_) return 0;
    auto* fn = reinterpret_cast<PFN_NM_ReceiveOnChan>(
        proc("SteamAPI_ISteamNetworkingMessages_ReceiveMessagesOnChannel"));
    return fn ? fn(i_messages_, channel, out, max) : 0;
}

void SteamApi::release_message(NetMessage* m) {
    if (!m) return;
    if (auto* fn = reinterpret_cast<PFN_Msg_Release>(
            proc("SteamAPI_SteamNetworkingMessage_t_Release")))
        fn(m);
}

bool SteamApi::accept_session(std::uint64_t steam_id) {
    if (!ready_ || steam_id == 0) return false;
    auto* fn = reinterpret_cast<PFN_NM_AcceptSession>(
        proc("SteamAPI_ISteamNetworkingMessages_AcceptSessionWithUser"));
    if (!fn) return false;
    return fn(i_messages_, NetIdentity::from_steam_id(steam_id));
}

void SteamApi::close_session(std::uint64_t steam_id) {
    if (!ready_ || steam_id == 0) return;
    if (auto* fn = reinterpret_cast<PFN_NM_CloseSession>(
            proc("SteamAPI_ISteamNetworkingMessages_CloseSessionWithUser")))
        fn(i_messages_, NetIdentity::from_steam_id(steam_id));
}

int SteamApi::relay_status() const {
    if (!ready_ || !i_utils_net_) return -1;
    auto* fn = reinterpret_cast<PFN_NU_RelayStatus>(
        proc("SteamAPI_ISteamNetworkingUtils_GetRelayNetworkStatus"));
    return fn ? fn(i_utils_net_, nullptr) : -1;
}

}  // namespace fom::steam
