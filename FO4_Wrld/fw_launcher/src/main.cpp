// FoM - one-click Fallout 4 multiplayer launcher.
//
// Friend UX:
//   1. Unzip pack (or copy FoM.exe + dxgi.dll into the FO4 folder)
//   2. Double-click FoM.exe
//   3. Host, or accept a Steam invite
//   4. Game starts. Done. No IP typing, no port forwarding.
//
// FoM is also the Steam shell process for the session: it holds the Spacewar
// (AppID 480) lobby, receives invites, and runs the Steam P2P <-> UDP bridge
// for as long as the game is up. See docs/STEAM_SPACEWAR.md for why Spacewar
// and what the overlay honestly shows.
//
// Fallout 4 itself is untouched: classic 1.10.163 + our dxgi.dll, speaking
// the same UDP frames to what it still believes is a local server.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shlobj.h>
#include <tlhelp32.h>

#include <conio.h>

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "agent.h"
#include "fom_util.h"
#include "net/steam_bridge.h"
#include "net/steam_peer_transport.h"
#include "steam/steam_api.h"
#include "steam/steam_session.h"
#include "../../fw_native/src/net/protocol_version.h"

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ws2_32.lib")

using fom::file_exists;
using fom::join_path;
using fom::logw;

namespace {

constexpr std::uint16_t kServerPort = 31337;

// ------------------------------------------------------------------ winsock

bool winsock_once() {
    static bool inited = false;
    static bool ok = false;
    if (!inited) {
        WSADATA w{};
        ok = (WSAStartup(MAKEWORD(2, 2), &w) == 0);
        inited = true;
    }
    return ok;
}

// -------------------------------------------------------------- FO4 folder

std::wstring find_fo4_near(const std::wstring& base) {
    if (file_exists(join_path(base, L"Fallout4.exe"))) return base;
    return L"";
}

std::wstring find_fo4_common() {
    const wchar_t* cands[] = {
        L"C:\\Games\\Steam\\steamapps\\common\\Fallout 4",
        L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout 4",
        L"D:\\SteamLibrary\\steamapps\\common\\Fallout 4",
        L"E:\\SteamLibrary\\steamapps\\common\\Fallout 4",
        L"D:\\Steam\\steamapps\\common\\Fallout 4",
        L"E:\\Steam\\steamapps\\common\\Fallout 4",
    };
    for (const auto* c : cands) {
        if (file_exists(join_path(c, L"Fallout4.exe"))) return c;
    }
    return L"";
}

std::wstring pick_fo4_folder() {
    BROWSEINFOW bi{};
    bi.lpszTitle = L"Select your Fallout 4 folder (contains Fallout4.exe)";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl = SHBrowseForFolderW(&bi);
    if (!pidl) return L"";
    wchar_t path[MAX_PATH] = {};
    if (!SHGetPathFromIDListW(pidl, path)) {
        CoTaskMemFree(pidl);
        return L"";
    }
    CoTaskMemFree(pidl);
    return path;
}

std::wstring resolve_fo4(const std::wstring& self_dir) {
    std::wstring fo4 = find_fo4_near(self_dir);
    if (!fo4.empty()) {
        logw(L"[FoM] FO4 folder = this folder\n");
        return fo4;
    }
    fo4 = find_fo4_common();
    if (!fo4.empty()) {
        logw(L"[FoM] Found FO4:\n       %ls\n", fo4.c_str());
        logw(L"[FoM] Use this? [Y/n]: ");
        char line[32] = {};
        if (std::fgets(line, sizeof(line), stdin)) {
            if (line[0] == 'n' || line[0] == 'N') fo4.clear();
        }
    }
    if (fo4.empty()) {
        logw(L"[FoM] Pick the folder that contains Fallout4.exe...\n");
        fo4 = pick_fo4_folder();
    }
    if (!fo4.empty() && !file_exists(join_path(fo4, L"Fallout4.exe"))) {
        logw(L"[FoM] ERROR: Fallout4.exe not in that folder.\n");
        return L"";
    }
    return fo4;
}

// ------------------------------------------------------------- install/config

bool copy_file(const std::wstring& src, const std::wstring& dst) {
    if (!CopyFileW(src.c_str(), dst.c_str(), FALSE)) {
        logw(L"[FoM] copy failed (err=%lu)\n", GetLastError());
        return false;
    }
    return true;
}

bool install_client(const std::wstring& self_dir, const std::wstring& fo4) {
    const std::wstring payload_dll = join_path(join_path(self_dir, L"payload"), L"dxgi.dll");
    const std::wstring local_dll = join_path(self_dir, L"dxgi.dll");
    const std::wstring dest = join_path(fo4, L"dxgi.dll");

    std::wstring src;
    if (file_exists(payload_dll)) src = payload_dll;
    else if (file_exists(local_dll)) src = local_dll;
    else if (file_exists(dest)) {
        logw(L"[FoM] multiplayer files already installed\n");
        return true;
    } else {
        logw(L"[FoM] ERROR: dxgi.dll missing next to FoM.exe (or payload\\)\n");
        return false;
    }

    if (!copy_file(src, dest)) return false;
    logw(L"[FoM] installed multiplayer client\n");

    const wchar_t* extras[] = {
        L"f4se_loader.exe", L"f4se_1_10_163.dll", L"f4se_steam_loader.dll",
    };
    const std::wstring pdir = join_path(self_dir, L"payload");
    for (const auto* f : extras) {
        const std::wstring s = join_path(pdir, f);
        if (file_exists(s)) {
            copy_file(s, join_path(fo4, f));
        }
    }
    return true;
}

bool write_fw_config(const std::wstring& fo4,
                     const char* server,
                     const char* client_id,
                     const char* ghost_peer) {
    const std::wstring path = join_path(fo4, L"fw_config.ini");
    char body[512];
    std::snprintf(body, sizeof(body),
        "# Auto-written by FoM.exe\n"
        "server = %s\n"
        "client_id = %s\n"
        "ghost_map = %s=0x1CA7D\n"
        "log_level = info\n"
        "auto_load_save =\n"
        "boot_proxy_only = 0\n",
        server, client_id, ghost_peer);

    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        logw(L"[FoM] ERROR: cannot write fw_config.ini\n");
        return false;
    }
    DWORD written = 0;
    const BOOL ok = WriteFile(h, body, static_cast<DWORD>(std::strlen(body)),
                              &written, nullptr);
    CloseHandle(h);
    if (!ok) return false;
    logw(L"[FoM] ready as %hs -> %hs\n", client_id, server);
    return true;
}

std::string first_lan_ipv4() {
    if (!winsock_once()) return "127.0.0.1";
    char host[256] = {};
    if (gethostname(host, sizeof(host)) != 0) return "127.0.0.1";
    addrinfo hints{};
    hints.ai_family = AF_INET;
    addrinfo* res = nullptr;
    if (getaddrinfo(host, nullptr, &hints, &res) != 0 || !res) return "127.0.0.1";
    std::string ip = "127.0.0.1";
    for (addrinfo* p = res; p; p = p->ai_next) {
        auto* sa = reinterpret_cast<sockaddr_in*>(p->ai_addr);
        char buf[64] = {};
        if (InetNtopA(AF_INET, &sa->sin_addr, buf, sizeof(buf))) {
            if (std::strncmp(buf, "127.", 4) != 0) {
                ip = buf;
                break;
            }
        }
    }
    freeaddrinfo(res);
    return ip;
}

void remember_server(const std::string& server) {
    const std::wstring dir = fom::appdata_dir();
    if (dir.empty()) return;
    const std::wstring path = join_path(dir, L"last_server.txt");
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w = 0;
    WriteFile(h, server.c_str(), static_cast<DWORD>(server.size()), &w, nullptr);
    CloseHandle(h);
}

std::string recall_server() {
    const std::wstring dir = fom::appdata_dir();
    if (dir.empty()) return "192.168.1.1:31337";
    const std::wstring path = join_path(dir, L"last_server.txt");
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return "192.168.1.1:31337";
    char buf[128] = {};
    DWORD n = 0;
    ReadFile(h, buf, sizeof(buf) - 1, &n, nullptr);
    CloseHandle(h);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r' || buf[n - 1] == ' '))
        buf[--n] = 0;
    if (n == 0) return "192.168.1.1:31337";
    return std::string(buf);
}

// ------------------------------------------------------------------ process

bool spawn_detached(const std::wstring& app, const std::wstring& args,
                    const std::wstring& cwd, bool minimize) {
    std::wstring cmd = L"\"" + app + L"\"";
    if (!args.empty()) {
        cmd += L" ";
        cmd += args;
    }
    std::vector<wchar_t> mutable_cmd(cmd.begin(), cmd.end());
    mutable_cmd.push_back(0);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    if (minimize) {
        si.dwFlags = STARTF_USESHOWWINDOW;
        si.wShowWindow = SW_SHOWMINNOACTIVE;
    }
    PROCESS_INFORMATION pi{};
    const BOOL ok = CreateProcessW(
        nullptr, mutable_cmd.data(), nullptr, nullptr, FALSE,
        CREATE_NEW_CONSOLE, nullptr,
        cwd.empty() ? nullptr : cwd.c_str(),
        &si, &pi);
    if (!ok) {
        logw(L"[FoM] failed to start process (err=%lu)\n", GetLastError());
        return false;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return true;
}

// f4se_loader.exe launches Fallout4.exe and exits, so we cannot keep a
// process handle. Poll the process list instead.
bool process_running(const wchar_t* image_name) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return false;
    PROCESSENTRY32W pe{};
    pe.dwSize = sizeof(pe);
    bool found = false;
    if (Process32FirstW(snap, &pe)) {
        do {
            if (_wcsicmp(pe.szExeFile, image_name) == 0) {
                found = true;
                break;
            }
        } while (Process32NextW(snap, &pe));
    }
    CloseHandle(snap);
    return found;
}

bool start_server(const std::wstring& self_dir) {
    const std::wstring runtime = join_path(self_dir, L"runtime");
    const std::wstring py_rt = join_path(runtime, L"python.exe");

    const std::wstring candidates_py[] = {
        py_rt,
        join_path(join_path(self_dir, L".venv\\Scripts"), L"python.exe"),
        join_path(join_path(join_path(self_dir, L".."), L".venv\\Scripts"), L"python.exe"),
        join_path(join_path(join_path(join_path(self_dir, L".."), L".."), L".venv\\Scripts"), L"python.exe"),
    };
    const std::wstring candidates_cwd[] = {
        runtime,
        self_dir,
        join_path(self_dir, L".."),
        join_path(join_path(self_dir, L".."), L".."),
    };

    wchar_t path_py[MAX_PATH] = {};
    const DWORD n = SearchPathW(nullptr, L"python.exe", nullptr, MAX_PATH, path_py, nullptr);

    auto try_start = [&](const std::wstring& py, const std::wstring& cwd) -> bool {
        if (!file_exists(py)) return false;
        const std::wstring main_py =
            join_path(join_path(join_path(cwd, L"net"), L"server"), L"main.py");
        if (!file_exists(main_py)) return false;
        logw(L"[FoM] starting multiplayer server...\n");
        return spawn_detached(
            py,
            L"-u -m net.server.main --host 0.0.0.0 --port 31337 --log-level INFO",
            cwd,
            true);
    };

    for (const auto& py : candidates_py) {
        for (const auto& cwd : candidates_cwd) {
            if (try_start(py, cwd)) {
                Sleep(800);
                return true;
            }
        }
    }
    if (n > 0 && n < MAX_PATH) {
        for (const auto& cwd : candidates_cwd) {
            if (try_start(path_py, cwd)) {
                Sleep(800);
                return true;
            }
        }
    }

    logw(L"[FoM] WARNING: could not auto-start server.\n");
    logw(L"       Use a Host pack with runtime\\, or FO4_Wrld repo + Python.\n");
    return false;
}

bool launch_game(const std::wstring& fo4) {
    const std::wstring f4se = join_path(fo4, L"f4se_loader.exe");
    const std::wstring fo4e = join_path(fo4, L"Fallout4.exe");
    if (file_exists(f4se)) {
        logw(L"[FoM] launching game...\n");
        return spawn_detached(f4se, L"", fo4, false);
    }
    if (file_exists(fo4e)) {
        logw(L"[FoM] launching game...\n");
        return spawn_detached(fo4e, L"", fo4, false);
    }
    logw(L"[FoM] ERROR: Fallout4.exe not found\n");
    return false;
}

// --------------------------------------------------------------------- menu

char main_menu() {
    logw(L"\n");
    logw(L"  ========================================\n");
    logw(L"   FalloutWorld multiplayer\n");
    logw(L"  ========================================\n");
    logw(L"\n");
    logw(L"  [1]  Host session     - invite friends, any network\n");
    logw(L"  [2]  Join a friend    - if you have not been invited yet\n");
    logw(L"  [3]  LAN only         - same network, type an IP\n");
    logw(L"  [4]  Install files only\n");
    logw(L"  [5]  Steam invites: %hs\n",
         fom::autostart_enabled() ? "ON  (accept an invite and the game just"
                                    " opens)"
                                  : "OFF (turn on for one-click invites)");
    logw(L"\n");
    logw(L"  You do NOT need to sit here to be invited - see [5].\n");
    logw(L"\n");
    logw(L"  Choice [1/2/3/4/5]: ");
    char line[32] = {};
    if (!std::fgets(line, sizeof(line), stdin)) return 0;
    switch (line[0]) {
    case '1': return 'H';
    case '2': return 'J';
    case '3': return 'L';
    case '4': return 'I';
    case '5': return 'T';
    default:  return 0;
    }
}

// [5] - the difference between "co-op" and "co-op, but coordinate first".
// Returns the state it left things in. Retiring any resident helper is the
// caller's job: an agent running this on itself must exit its own loop, not
// try to message itself.
bool toggle_background_invites() {
    if (fom::autostart_enabled()) {
        fom::disable_autostart();
        logw(L"\n  Steam invites: OFF.\n");
        logw(L"  You will now have to start FoM and pick [2] before a friend\n");
        logw(L"  can invite you. Re-run FoM and press [5] to turn it back on.\n");
    } else {
        if (fom::enable_autostart()) {
            logw(L"\n  Steam invites: ON.\n");
            logw(L"  FoM now keeps a small background helper running so that\n");
            logw(L"  accepting a Steam invite just opens the game - nothing to\n");
            logw(L"  start first, nothing to coordinate.\n");
            logw(L"\n  It is one entry under Windows startup, named\n");
            logw(L"  \"FalloutWorld (FoM)\". Press [5] again to remove it.\n");
        } else {
            logw(L"\n  Could not write the startup entry (permissions?).\n");
        }
    }
    logw(L"\n  Press Enter...");
    (void)std::getchar();
    return fom::autostart_enabled();
}

char lan_menu() {
    logw(L"\n  LAN mode\n");
    logw(L"  [1]  Host (friends type your IP)\n");
    logw(L"  [2]  Join (you type their IP)\n");
    logw(L"  Choice [1/2]: ");
    char line[32] = {};
    if (!std::fgets(line, sizeof(line), stdin)) return 0;
    if (line[0] == '1') return 'H';
    if (line[0] == '2') return 'J';
    return 0;
}

std::string prompt_join_server() {
    const std::string prev = recall_server();
    logw(L"\n  Friend's IP (what Host screen shows)\n");
    logw(L"  Last: %hs\n", prev.c_str());
    logw(L"  IP [Enter = last]: ");
    char line[128] = {};
    if (!std::fgets(line, sizeof(line), stdin)) return prev;
    std::string s = line;
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' '))
        s.pop_back();
    while (!s.empty() && s.front() == ' ') s.erase(s.begin());
    if (s.empty()) s = prev;
    if (s.find(':') == std::string::npos) s += ":31337";
    remember_server(s);
    return s;
}

void pause_exit(int code) {
    logw(L"\nPress Enter to close...\n");
    std::fflush(stdout);
    (void)std::getchar();
    std::exit(code);
}

// ---------------------------------------------------------------- LAN paths

int run_lan(const std::wstring& self_dir, const std::wstring& fo4, char side) {
    if (side == 0) side = lan_menu();
    if (side == 'H') {
        const std::string lan = first_lan_ipv4();
        write_fw_config(fo4, "127.0.0.1:31337", "player_A", "player_B");
        logw(L"\n");
        logw(L"  *** YOU ARE HOST (LAN) ***\n");
        logw(L"  Tell your friend this IP (Join):\n\n");
        logw(L"      %hs\n\n", lan.c_str());
        start_server(self_dir);
        logw(L"[FoM] server on :31337\n");
        if (!launch_game(fo4)) pause_exit(3);
        logw(L"[FoM] game starting.\n");
        Sleep(2500);
        return 0;
    }
    if (side == 'J') {
        const std::string server = prompt_join_server();
        write_fw_config(fo4, server.c_str(), "player_B", "player_A");
        logw(L"\n  Joining %hs ...\n", server.c_str());
        logw(L"  Friend must already be Hosting.\n\n");
        if (!launch_game(fo4)) pause_exit(3);
        Sleep(1500);
        return 0;
    }
    logw(L"[FoM] cancelled.\n");
    return 1;
}

// -------------------------------------------------------------- Steam paths

// Owns the objects that must outlive a Steam session, so the flows below read
// as a story instead of a pile of parameters.
struct SteamRuntime {
    fom::steam::SteamApi     api;
    fom::steam::SteamSession session{api};
    fom::net::SteamPeerTransport transport{api};
    fom::net::SteamUdpBridge bridge;
};

// Pump Steam until `pred` is satisfied or we run out of patience.
template <typename Pred>
bool wait_for(fom::steam::SteamSession& session, int timeout_ms, Pred pred) {
    const std::uint64_t deadline = fom::now_ms() + static_cast<std::uint64_t>(timeout_ms);
    for (;;) {
        session.pump();
        if (pred()) return true;
        if (session.state() == fom::steam::SessionState::Failed) return false;
        if (fom::now_ms() >= deadline) return false;
        Sleep(20);
    }
}

void print_steam_failure(const fom::steam::InitResult& r) {
    logw(L"\n");
    logw(L"  [FoM] Steam session unavailable:\n");
    logw(L"        %hs\n", r.error.c_str());
    logw(L"\n");
    logw(L"        Steam invites need:\n");
    logw(L"          - the Steam client running and signed in\n");
    logw(L"          - steam_api64.dll next to FoM.exe (shipped in the pack)\n");
    logw(L"          - Spacewar owned: Steam > Library > search 'Spacewar',\n");
    logw(L"            install and run it once\n");
    logw(L"\n");
}

// The session loop both Host and Join sit in while the game is up: pump
// Steam, move packets, keep the peer set in sync, react to keys.
void run_session_loop(SteamRuntime& rt, bool host_mode) {
    logw(L"\n");
    logw(L"  ---- session live ----------------------------------------\n");
    if (host_mode) {
        logw(L"   [I] invite a friend via the Steam overlay\n");
    }
    logw(L"   [S] status    [Q] quit (closes the tunnel)\n");
    logw(L"  ----------------------------------------------------------\n");
    logw(L"  Keep this window open - it is the tunnel.\n\n");

    std::uint64_t last_status_ms = 0;
    std::uint64_t game_seen_at_ms = 0;
    bool game_was_seen = false;
    const std::uint64_t started_ms = fom::now_ms();
    std::uint64_t last_proc_check_ms = 0;
    std::uint64_t last_pump_ms = 0;
    std::vector<std::uint64_t> accepted;

    for (;;) {
        const std::uint64_t now = fom::now_ms();

        // Steam callbacks and lobby membership only need housekeeping rates.
        // The DATA path below runs flat out; pumping Steam at the same
        // frequency would hammer the client with lobby queries for nothing.
        if (now - last_pump_ms >= 50) {
            last_pump_ms = now;
            rt.session.pump();

            if (host_mode) {
                const auto peers = rt.session.peers();

                // New lobby members get a loopback socket and a one-time
                // P2P accept (Steam will not deliver their packets otherwise).
                for (const std::uint64_t p : peers) {
                    rt.bridge.ensure_peer(p);
                    if (std::find(accepted.begin(), accepted.end(), p) ==
                        accepted.end()) {
                        rt.transport.accept(p);
                        accepted.push_back(p);
                    }
                }

                // Members who left take their socket with them, so a long
                // session does not accumulate dead loopback sockets.
                for (const std::uint64_t known : rt.bridge.peer_ids()) {
                    if (std::find(peers.begin(), peers.end(), known) ==
                        peers.end()) {
                        logw(L"[FoM] peer left: %hs\n",
                             rt.api.persona_name(known).c_str());
                        rt.bridge.forget_peer(known);
                        accepted.erase(
                            std::remove(accepted.begin(), accepted.end(), known),
                            accepted.end());
                    }
                }
            }
        }

        rt.bridge.poll(2);

        // Exit when the game exits, so the friend does not leave an orphaned
        // tunnel running. Grace period: the game takes a while to appear.
        if (now - last_proc_check_ms > 2000) {
            last_proc_check_ms = now;
            const bool up = process_running(L"Fallout4.exe");
            if (up) {
                game_was_seen = true;
                game_seen_at_ms = now;
            } else if (game_was_seen && now - game_seen_at_ms > 5000) {
                logw(L"\n[FoM] Fallout 4 closed - shutting the session down.\n");
                break;
            } else if (!game_was_seen && now - started_ms > 180000) {
                logw(L"\n[FoM] Fallout 4 never started - shutting down.\n");
                break;
            }
        }

        if (now - last_status_ms > 15000) {
            last_status_ms = now;
            const auto& s = rt.bridge.stats();
            logw(L"[FoM] peers=%zu  in=%llu pkt  out=%llu pkt%ls\n",
                 rt.session.peers().size(),
                 static_cast<unsigned long long>(s.to_server_packets),
                 static_cast<unsigned long long>(s.to_peer_packets),
                 (!host_mode && !rt.bridge.game_attached())
                     ? L"  (waiting for Fallout 4 to connect)"
                     : L"");
        }

        while (_kbhit()) {
            const int c = _getch();
            if (c == 'i' || c == 'I') {
                if (host_mode) {
                    logw(L"[FoM] opening Steam invite overlay "
                         L"(Shift+Tab if it does not appear)...\n");
                    rt.session.invite_overlay();
                }
            } else if (c == 's' || c == 'S') {
                const auto& s = rt.bridge.stats();
                logw(L"[FoM] lobby=%hs  peers=%zu  to_server=%llu/%llu B  "
                     L"to_peer=%llu/%llu B  drops=%llu\n",
                     rt.session.session_code().c_str(),
                     rt.session.peers().size(),
                     static_cast<unsigned long long>(s.to_server_packets),
                     static_cast<unsigned long long>(s.to_server_bytes),
                     static_cast<unsigned long long>(s.to_peer_packets),
                     static_cast<unsigned long long>(s.to_peer_bytes),
                     static_cast<unsigned long long>(s.send_failures));
            } else if (c == 'q' || c == 'Q') {
                logw(L"\n[FoM] closing session.\n");
                return;
            }
        }
    }
}

int run_steam_host(SteamRuntime& rt, const std::wstring& self_dir,
                   const std::wstring& fo4) {
    if (!rt.session.start_host()) {
        logw(L"[FoM] ERROR: %hs\n", rt.session.error().c_str());
        return 1;
    }
    logw(L"[FoM] creating Steam session...\n");
    if (!wait_for(rt.session, 15000, [&] {
            return rt.session.state() == fom::steam::SessionState::Active;
        })) {
        logw(L"[FoM] ERROR: %hs\n",
             rt.session.error().empty() ? "timed out creating the Steam lobby"
                                        : rt.session.error().c_str());
        return 1;
    }

    const std::uint64_t me = rt.api.local_steam_id();
    const std::string client_id = fom::steam_peer_id(me);

    // Accept incoming P2P sessions from anyone who is in our lobby. Steam
    // will not deliver a peer's packets until we do.
    rt.session.set_session_request_handler([&rt](std::uint64_t sid) {
        for (const std::uint64_t p : rt.session.peers()) {
            if (p == sid) {
                rt.transport.accept(sid);
                rt.bridge.ensure_peer(sid);
                logw(L"[FoM] peer connected: %hs\n",
                     rt.api.persona_name(sid).c_str());
                return;
            }
        }
        // Not a lobby member - ignore. Prevents strangers from tunnelling
        // traffic into our campaign server.
    });

    fom::net::SteamUdpBridge::Config bc{};
    bc.host_mode = true;
    bc.server_port = kServerPort;
    if (!rt.bridge.start(bc, &rt.transport)) {
        logw(L"[FoM] ERROR: %hs\n", rt.bridge.last_error().c_str());
        return 1;
    }

    write_fw_config(fo4, "127.0.0.1:31337",
                    client_id.empty() ? "player_A" : client_id.c_str(),
                    "peer");
    start_server(self_dir);

    const std::string lan = first_lan_ipv4();
    logw(L"\n");
    logw(L"  *** YOU ARE HOST ***\n\n");
    logw(L"  Your friends can now join you two ways, both one click:\n");
    logw(L"    - the invite picker opening right now, or [I] any time\n");
    logw(L"    - right-click you in their Steam friends list > Join Game\n\n");
    logw(L"  They do not need FoM open first, and they never type an IP.\n\n");
    logw(L"  Session code (belt and braces):  %hs\n\n",
         rt.session.session_code().c_str());
    logw(L"  Same-network friends can also use LAN Join with:  %hs\n\n",
         lan.c_str());

    // Put the friend picker in front of the host instead of making them hunt
    // for a hotkey. This is the moment they want it.
    rt.session.invite_overlay();

    if (!launch_game(fo4)) return 3;
    run_session_loop(rt, /*host_mode=*/true);
    return 0;
}

int run_steam_join(SteamRuntime& rt, const std::wstring& fo4,
                   std::uint64_t lobby_from_cmdline) {
    std::uint64_t lobby = lobby_from_cmdline;

    if (lobby == 0) {
        logw(L"\n");
        logw(L"  Waiting for your friend...\n\n");
        logw(L"  You do not normally need this screen: with Steam invites on,\n");
        logw(L"  accepting an invite (or pressing Join Game on your friend in\n");
        logw(L"  the Steam friends list) opens the game by itself.\n\n");
        logw(L"  Waiting here works too. Or paste their session code\n");
        logw(L"  (leave empty and press Enter to keep waiting): ");
        std::fflush(stdout);

        // Poll the keyboard while pumping Steam, so an invite that arrives
        // mid-typing still wins.
        std::string typed;
        for (;;) {
            rt.session.pump();
            const std::uint64_t pending = rt.session.take_pending_join_request();
            if (pending != 0) {
                lobby = pending;
                logw(L"\n[FoM] invite accepted.\n");
                break;
            }
            while (_kbhit()) {
                const int c = _getch();
                if (c == '\r' || c == '\n') {
                    if (!typed.empty()) {
                        lobby = fom::from_base36(typed);
                        if (lobby == 0) {
                            logw(L"\n  that code is not valid - try again: ");
                            typed.clear();
                        }
                    } else {
                        logw(L"\n  still waiting for an invite... ");
                    }
                } else if (c == '\b') {
                    if (!typed.empty()) {
                        typed.pop_back();
                        logw(L"\b \b");
                    }
                } else if (c == 3 || c == 27) {  // Ctrl+C / Esc
                    logw(L"\n[FoM] cancelled.\n");
                    return 1;
                } else if (c > 0 && c < 128 && std::isalnum(c) &&
                           typed.size() < 13) {
                    typed.push_back(static_cast<char>(std::tolower(c)));
                    logw(L"%hc", static_cast<char>(c));
                }
            }
            if (lobby != 0) break;
            Sleep(30);
        }
    }

    logw(L"[FoM] joining session...\n");
    if (!rt.session.start_join(lobby)) {
        logw(L"[FoM] ERROR: %hs\n", rt.session.error().c_str());
        return 1;
    }
    if (!wait_for(rt.session, 20000, [&] {
            return rt.session.state() == fom::steam::SessionState::Active;
        })) {
        logw(L"[FoM] ERROR: %hs\n",
             rt.session.error().empty() ? "timed out joining the Steam lobby"
                                        : rt.session.error().c_str());
        return 1;
    }

    // Lobby data can lag the join by a beat; give it a moment before we judge.
    rt.session.request_lobby_data_hint();
    std::uint64_t host = 0;
    wait_for(rt.session, 5000, [&] {
        host = rt.session.host_steam_id();
        return host != 0 && rt.session.lobby_protocol_version() != 0;
    });
    if (host == 0) host = rt.session.host_steam_id();
    if (host == 0) {
        logw(L"[FoM] ERROR: could not work out who is hosting this session.\n");
        return 1;
    }

    const int host_proto = rt.session.lobby_protocol_version();
    if (host_proto != 0 && host_proto != FW_PROTOCOL_VERSION) {
        logw(L"\n[FoM] ERROR: version mismatch.\n");
        logw(L"      Host is on protocol v%d, you are on v%d.\n",
             host_proto, FW_PROTOCOL_VERSION);
        logw(L"      One of you has an older FoM pack - match them up first.\n");
        return 1;
    }

    // Open the P2P session from our side, then start the tunnel.
    rt.transport.accept(host);
    rt.session.set_session_request_handler([&rt, host](std::uint64_t sid) {
        if (sid == host) rt.transport.accept(sid);
    });

    fom::net::SteamUdpBridge::Config bc{};
    bc.host_mode = false;
    bc.server_port = kServerPort;
    bc.host_peer = host;
    if (!rt.bridge.start(bc, &rt.transport)) {
        logw(L"[FoM] ERROR: %hs\n", rt.bridge.last_error().c_str());
        return 1;
    }

    const std::string client_id = fom::steam_peer_id(rt.api.local_steam_id());
    write_fw_config(fo4, "127.0.0.1:31337",
                    client_id.empty() ? "player_B" : client_id.c_str(),
                    "peer");

    const std::string host_name = rt.api.persona_name(host);
    logw(L"\n");
    logw(L"  *** JOINED %hs ***\n\n",
         host_name.empty() ? "your friend's session" : host_name.c_str());
    logw(L"  Traffic is tunnelled through Steam - no IP, no port forwarding.\n\n");

    if (!launch_game(fo4)) return 3;
    run_session_loop(rt, /*host_mode=*/false);
    return 0;
}

// ------------------------------------------------------------- agent mode

// Where Fallout 4 lives, without asking anyone - the agent has no user to
// prompt when an invite lands. The interactive run leaves a note; failing
// that we fall back to the usual suspects.
std::wstring resolve_fo4_unattended(const std::wstring& self_dir) {
    std::wstring fo4 = find_fo4_near(self_dir);
    if (!fo4.empty()) return fo4;
    fo4 = fom::recall_fo4_dir();
    if (!fo4.empty() && file_exists(join_path(fo4, L"Fallout4.exe")))
        return fo4;
    return find_fo4_common();
}

// One Steam session, start to finish. Shared by the interactive run and the
// agent so an invite-driven join is byte-for-byte the same code path a
// hand-driven one takes.
int steam_flow(SteamRuntime& rt, const std::wstring& self_dir,
               const std::wstring& fo4, char mode, std::uint64_t lobby) {
    if (mode == 'H') return run_steam_host(rt, self_dir, fo4);
    return run_steam_join(rt, fo4, lobby);
}

bool steam_client_running() {
    return process_running(L"steam.exe");
}

// The resident agent. Runs windowless, costs nothing, and exists purely so
// that "friend clicks Accept -> Fallout 4 opens" is true even when FoM was
// not already running.
int run_agent(const std::wstring& self_dir) {
    fom::hide_console();
    logw(L"[FoM] background invite agent starting\n");

    SteamRuntime rt;

    // Windows starts us at logon, which is usually before Steam is up. Wait
    // for the client rather than burning a DLL scan every retry.
    for (;;) {
        if (steam_client_running()) {
            const auto init = rt.api.init(self_dir);
            if (init.ok) {
                logw(L"[FoM] agent ready as %hs - waiting for invites\n",
                     rt.api.persona_name(rt.api.local_steam_id()).c_str());
                break;
            }
            logw(L"[FoM] agent: Steam init failed (%hs) - retrying\n",
                 init.error.c_str());
        }
        Sleep(15000);
    }

    fom::AgentServer server;
    if (!server.listening()) {
        logw(L"[FoM] agent: could not open the command pipe; "
             L"double-clicking FoM will not hand off\n");
    }

    for (;;) {
        if (server.quit_requested()) {
            logw(L"[FoM] agent: shutting down on request\n");
            rt.session.leave();
            rt.api.shutdown();
            return 0;
        }

        rt.session.pump();

        char mode = 0;
        std::uint64_t lobby = rt.session.take_pending_join_request();
        if (lobby != 0) {
            // This is the whole point of the agent.
            mode = 'J';
            logw(L"\n[FoM] invite accepted - joining...\n");
        } else {
            fom::AgentCommand cmd{};
            std::uint64_t arg = 0;
            if (server.next(&cmd, &arg)) {
                switch (cmd) {
                case fom::AgentCommand::Host:     mode = 'H'; break;
                case fom::AgentCommand::Join:     mode = 'J'; lobby = arg; break;
                case fom::AgentCommand::ShowMenu: mode = '?'; break;
                case fom::AgentCommand::Lan:      mode = 'L'; break;
                case fom::AgentCommand::Quit:     break;  // handled above
                }
            }
        }

        if (mode == 0) {
            Sleep(100);
            continue;
        }

        // From here we own a window and, shortly, a session. Anything else
        // that tries to drive us gets told to use this window instead.
        server.set_busy(true);
        struct BusyGuard {
            fom::AgentServer& s;
            ~BusyGuard() { s.set_busy(false); }
        } busy_guard{server};

        fom::show_console();
        fom::raise_console();

        const std::wstring fo4 = resolve_fo4_unattended(self_dir);
        if (fo4.empty()) {
            logw(L"\n[FoM] I could not find your Fallout 4 folder.\n");
            logw(L"      Run FoM.exe once by hand and pick it - I will "
                 L"remember it after that.\n\n");
            mode = 0;
            Sleep(6000);
            fom::hide_console();
            continue;
        }

        if (mode == '?') {
            mode = main_menu();
            if (mode == 'T') {
                // The player is toggling the helper from inside the helper.
                if (!toggle_background_invites()) {
                    logw(L"[FoM] stopping the background helper.\n");
                    Sleep(1500);
                    rt.session.leave();
                    rt.api.shutdown();
                    return 0;
                }
                fom::hide_console();
                continue;
            }
            if (mode == 'I') {
                install_client(self_dir, fo4);
                write_fw_config(fo4, "127.0.0.1:31337", "player_A", "peer");
                logw(L"[FoM] install done.\n");
                Sleep(2500);
                fom::hide_console();
                continue;
            }
            if (mode == 0) {
                fom::hide_console();
                continue;
            }
        }

        if (!install_client(self_dir, fo4)) {
            Sleep(6000);
            fom::hide_console();
            continue;
        }
        fom::remember_fo4_dir(fo4);

        if (mode == 'L') {
            run_lan(self_dir, fo4, 0);
        } else {
            const int rc = steam_flow(rt, self_dir, fo4, mode, lobby);
            if (rc != 0) {
                logw(L"\n[FoM] session did not start. Returning to standby.\n");
                Sleep(8000);
            }
        }

        // Tear the session down and go back to sleep, ready for the next one.
        rt.bridge.stop();
        rt.session.leave();
        logw(L"\n[FoM] back on standby - waiting for the next invite.\n");
        Sleep(1500);
        fom::hide_console();
    }
}

// Make sure an agent exists for next time. Called as the interactive process
// exits, so invites work immediately instead of after the next reboot.
void ensure_agent_running(const std::wstring& self_dir) {
    (void)self_dir;
    if (!fom::autostart_enabled()) return;
    if (fom::agent_is_running()) return;

    wchar_t exe[MAX_PATH] = {};
    if (GetModuleFileNameW(nullptr, exe, MAX_PATH) == 0) return;

    // Hand over the single-instance mutex first, or the agent we spawn will
    // immediately lose the race to us and exit.
    fom::release_single_instance();

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi{};
    std::wstring cmd = std::wstring(L"\"") + exe + L"\" --agent";
    std::vector<wchar_t> mutable_cmd(cmd.begin(), cmd.end());
    mutable_cmd.push_back(0);
    // CREATE_NEW_CONSOLE + SW_HIDE, NOT CREATE_NO_WINDOW: the latter gives
    // the child a console with no window at all, so GetConsoleWindow()
    // returns null and the agent could never show itself when an invite
    // arrives. We want a real window that simply starts hidden.
    if (CreateProcessW(nullptr, mutable_cmd.data(), nullptr, nullptr, FALSE,
                       CREATE_NEW_CONSOLE, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
}

// ------------------------------------------------------------------ argv

struct Args {
    char          mode = 0;              // H / J / L / I, 0 = ask
    char          lan_side = 0;          // H / J for --lan
    std::uint64_t connect_lobby = 0;     // Steam's +connect_lobby <id>
    bool          force_lan = false;
    bool          steam_check = false;   // --steam-check diagnostic
    bool          agent = false;         // --agent resident mode
    bool          no_agent = false;      // --no-agent: do not autostart
    bool          quit_agent = false;    // --quit-agent: retire the resident one
};

// `FoM.exe --steam-check` - prove the Steam side is wired up without
// touching Fallout 4. Used by docs/MP_TEST_RUNBOOK.md as the first gate when
// an invite does not work.
int run_steam_check(const std::wstring& self_dir) {
    logw(L"\n[FoM] Steam diagnostic\n\n");

    const auto candidates = fom::steam::find_candidate_steam_dlls(self_dir);
    logw(L"  steam_api64.dll candidates: %zu\n", candidates.size());
    for (const auto& c : candidates) logw(L"    %ls\n", c.c_str());
    logw(L"\n");

    fom::steam::SteamApi api;
    const auto r = api.init(self_dir);
    if (!r.ok) {
        logw(L"  RESULT: FAIL - %hs\n", r.error.c_str());
        return 1;
    }

    logw(L"  loaded:      %ls\n", r.dll_path.c_str());
    logw(L"  app id:      480 (Spacewar)\n");
    logw(L"  steam id:    %llu\n",
         static_cast<unsigned long long>(api.local_steam_id()));
    logw(L"  persona:     %hs\n",
         api.persona_name(api.local_steam_id()).c_str());
    logw(L"  peer_id:     %hs  (what the campaign server will see)\n",
         fom::steam_peer_id(api.local_steam_id()).c_str());
    logw(L"  owns 480:    %hs\n", api.owns_spacewar() ? "yes" : "NO");
    logw(L"  protocol:    v%d\n", FW_PROTOCOL_VERSION);

    // Relay network readiness needs a few pumps to settle.
    fom::steam::SteamSession session(api);
    for (int i = 0; i < 200; ++i) {
        session.pump();
        if (api.relay_status() == fom::steam::kAvailabilityCurrent) break;
        Sleep(25);
    }
    const int relay = api.relay_status();
    logw(L"  relay:       %d %hs\n", relay,
         relay == fom::steam::kAvailabilityCurrent
             ? "(ready - NAT traversal available)"
             : "(not ready yet; usually still fine, Steam keeps trying)");

    // Prove the lobby round trip actually works end to end.
    logw(L"\n  creating a test lobby...\n");
    if (!session.start_host(2)) {
        logw(L"  RESULT: FAIL - %hs\n", session.error().c_str());
        api.shutdown();
        return 1;
    }
    bool active = false;
    for (int i = 0; i < 400; ++i) {
        session.pump();
        if (session.state() == fom::steam::SessionState::Active) {
            active = true;
            break;
        }
        if (session.state() == fom::steam::SessionState::Failed) break;
        Sleep(25);
    }
    if (!active) {
        logw(L"  RESULT: FAIL - %hs\n",
             session.error().empty() ? "lobby creation timed out"
                                     : session.error().c_str());
        api.shutdown();
        return 1;
    }
    logw(L"  lobby id:    %llu\n",
         static_cast<unsigned long long>(session.lobby_id()));
    logw(L"  code:        %hs\n", session.session_code().c_str());
    logw(L"  lobby proto: %d\n", session.lobby_protocol_version());
    logw(L"  lobby host:  %llu\n",
         static_cast<unsigned long long>(session.host_steam_id()));

    const bool proto_ok = session.lobby_protocol_version() == FW_PROTOCOL_VERSION;
    const bool host_ok  = session.host_steam_id() == api.local_steam_id();

    // The "connect" rich-presence key is what puts a "Join Game" button on us
    // in every friend's Steam list. Read back what Steam actually stored.
    const std::string connect =
        api.get_rich_presence(api.local_steam_id(), "connect");
    const bool connect_ok =
        fom::steam::SteamSession::parse_connect_string(connect.c_str()) ==
        session.lobby_id();
    logw(L"  connect:     %hs %hs\n",
         connect.empty() ? "(empty)" : connect.c_str(),
         connect_ok ? "-> friends see a Join Game button"
                    : "-> NOT set; friends-list join will not work");

    logw(L"\n  invites while FoM is closed: %hs\n",
         fom::autostart_enabled()
             ? "ON (background helper starts with Windows)"
             : "OFF - accepting an invite will NOT open the game unless FoM "
               "is already running. Run FoM and press [5].");
    logw(L"  background helper right now:  %hs\n",
         fom::agent_is_running() ? "running" : "not running");

    session.leave();
    api.shutdown();

    const bool pass = proto_ok && host_ok && connect_ok;
    logw(L"\n  RESULT: %hs\n",
         pass ? "PASS - Steam lobby + invite path is live"
              : "FAIL - lobby data or connect string did not round-trip");
    return pass ? 0 : 1;
}

Args parse_args(int argc, wchar_t** argv) {
    Args a{};
    for (int i = 1; i < argc; ++i) {
        const std::wstring s = argv[i];
        if (s == L"+connect_lobby" && i + 1 < argc) {
            a.connect_lobby = _wcstoui64(argv[++i], nullptr, 10);
            a.mode = 'J';
        } else if (s == L"--host") {
            a.mode = 'H';
        } else if (s == L"--join") {
            a.mode = 'J';
        } else if (s == L"--lan") {
            a.force_lan = true;
            a.mode = 'L';
        } else if (s == L"--side" && i + 1 < argc) {
            const std::wstring v = argv[++i];
            a.force_lan = true;
            a.mode = 'L';
            a.lan_side = (v == L"A" || v == L"a") ? 'H' : 'J';
        } else if (s == L"--install") {
            a.mode = 'I';
        } else if (s == L"--steam-check") {
            a.steam_check = true;
        } else if (s == L"--agent") {
            a.agent = true;
        } else if (s == L"--no-agent") {
            a.no_agent = true;
        } else if (s == L"--quit-agent") {
            a.quit_agent = true;
        } else if (s.rfind(L"+connect_lobby", 0) == 0) {
            // Steam sometimes glues the value on: "+connect_lobby109775..."
            const std::uint64_t v = fom::steam::SteamSession::parse_connect_string(
                fom::narrow(s).c_str());
            if (v != 0) {
                a.connect_lobby = v;
                a.mode = 'J';
            }
        }
    }
    return a;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    winsock_once();

    const std::wstring self = fom::exe_dir();
    logw(L"FoM multiplayer\n");

    Args args = parse_args(argc, argv);

    if (args.quit_agent) {
        switch (fom::send_to_agent(fom::AgentCommand::Quit)) {
        case fom::AgentReply::Accepted:
            logw(L"[FoM] background agent stopped.\n");
            return 0;
        case fom::AgentReply::Busy:
            logw(L"[FoM] the agent is mid-session - close its window "
                 L"instead.\n");
            return 1;
        case fom::AgentReply::NoAgent:
            logw(L"[FoM] no background agent was running.\n");
            return 1;
        }
        return 1;
    }

    if (args.steam_check) {
        if (fom::agent_is_running()) {
            logw(L"[FoM] NOTE: the background agent already holds a Steam "
                 L"session.\n"
                 L"      This check will open a second one; invite routing "
                 L"between two\n"
                 L"      sessions is ambiguous. Run --quit-agent first for a "
                 L"clean read.\n");
        }
        const int rc = run_steam_check(self);
        pause_exit(rc);
    }

    // Resident mode. Nothing interactive happens here until an invite lands.
    if (args.agent) {
        if (!fom::claim_single_instance()) {
            // A FoM is already the Steam session for this user; two would
            // just fight over who receives the invite.
            return 0;
        }
        return run_agent(self);
    }

    // Interactive. If the agent already holds the Steam session, hand it the
    // command instead of standing up a second one.
    const bool have_instance = fom::claim_single_instance();
    if (!have_instance) {
        fom::AgentCommand cmd = fom::AgentCommand::ShowMenu;
        std::uint64_t arg = 0;
        if (args.connect_lobby != 0) {
            cmd = fom::AgentCommand::Join;
            arg = args.connect_lobby;
        } else if (args.mode == 'H') {
            cmd = fom::AgentCommand::Host;
        } else if (args.mode == 'J') {
            cmd = fom::AgentCommand::Join;
        } else if (args.mode == 'L') {
            cmd = fom::AgentCommand::Lan;
        }
        switch (fom::send_to_agent(cmd, arg)) {
        case fom::AgentReply::Accepted:
            logw(L"[FoM] FoM is already running - handed over to it.\n"
                 L"      Look for its window.\n");
            Sleep(1800);
            return 0;
        case fom::AgentReply::Busy:
            logw(L"[FoM] FoM already has a session open.\n"
                 L"      Use that window - it is the one holding the "
                 L"tunnel.\n");
            pause_exit(0);
            break;
        case fom::AgentReply::NoAgent:
            // Something holds the mutex but did not answer. Carry on; worst
            // case Steam init fails below and we offer LAN.
            logw(L"[FoM] another FoM seems to be running but did not "
                 L"answer.\n");
            break;
        }
    }

    if (args.mode == 0) args.mode = main_menu();

    if (args.mode == 'T') {
        if (toggle_background_invites()) {
            ensure_agent_running(self);
        } else {
            // Turned off: retire any helper that is still resident, or it
            // would keep answering invites until the next reboot.
            fom::send_to_agent(fom::AgentCommand::Quit);
        }
        return 0;
    }

    if (args.mode == 0) {
        logw(L"[FoM] cancelled.\n");
        pause_exit(1);
    }

    const std::wstring fo4 = resolve_fo4(self);
    if (fo4.empty()) {
        logw(L"[FoM] ERROR: no Fallout 4 folder.\n");
        pause_exit(1);
    }
    logw(L"[FoM] FO4 = %ls\n", fo4.c_str());
    fom::remember_fo4_dir(fo4);

    if (!install_client(self, fo4)) pause_exit(2);

    if (args.mode == 'I') {
        write_fw_config(fo4, "127.0.0.1:31337", "player_A", "peer");
        logw(L"[FoM] install done. Run FoM again -> Host or Join.\n");
        pause_exit(0);
    }

    if (args.mode == 'L') {
        const int rc = run_lan(self, fo4, args.lan_side);
        if (rc != 0) pause_exit(rc);
        return 0;
    }

    // Steam paths. If Steam cannot come up we say why and fall back to LAN
    // rather than dead-ending the player.
    SteamRuntime rt;
    const auto init = rt.api.init(self);
    if (!init.ok) {
        print_steam_failure(init);
        logw(L"  Fall back to LAN mode (same network only)? [Y/n]: ");
        char line[32] = {};
        if (std::fgets(line, sizeof(line), stdin) &&
            (line[0] == 'n' || line[0] == 'N')) {
            pause_exit(4);
        }
        const int rc = run_lan(self, fo4, args.mode == 'H' ? 'H' : 'J');
        if (rc != 0) pause_exit(rc);
        return 0;
    }

    logw(L"[FoM] Steam ready - %hs (steam_api64.dll: %ls)\n",
         rt.api.persona_name(rt.api.local_steam_id()).c_str(),
         init.dll_path.c_str());
    if (init.source == fom::steam::DllSource::Discovered) {
        logw(L"[FoM] NOTE: using a steam_api64.dll borrowed from another "
             L"installed game.\n"
             L"      Ship one next to FoM.exe for players.\n");
    }
    if (!rt.api.owns_spacewar()) {
        logw(L"[FoM] NOTE: your account does not appear to own Spacewar "
             L"(AppID 480).\n"
             L"      If hosting fails, open Steam > Library > search "
             L"'Spacewar' and run it once.\n");
    }

    // First successful Steam run turns on background invites. Without this,
    // a friend's invite goes nowhere unless FoM happens to be open, which is
    // not co-op - it is homework. One HKCU\...\Run entry, removable from the
    // menu, and we say so out loud rather than doing it silently.
    if (!args.no_agent && !fom::autostart_enabled()) {
        if (fom::enable_autostart()) {
            logw(L"[FoM] Steam invites enabled: a small background helper "
                 L"now starts with\n"
                 L"      Windows so accepting an invite just opens the game. "
                 L"Run FoM and\n"
                 L"      press [5] to turn it off.\n");
        }
    }

    const int rc = steam_flow(rt, self, fo4, args.mode, args.connect_lobby);

    rt.bridge.stop();
    rt.session.leave();
    rt.api.shutdown();

    if (rc != 0) pause_exit(rc);
    logw(L"[FoM] done.\n");

    // Leave an agent behind so the next invite lands without anyone having
    // to open FoM first. Releases the single-instance mutex on the way out.
    ensure_agent_running(self);
    Sleep(1200);
    return 0;
}
