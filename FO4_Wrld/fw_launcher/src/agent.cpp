#include "agent.h"

#include <windows.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <utility>

#include "fom_util.h"

namespace fom {

namespace {

// "Local\" scopes both to the logon session, which is what we want: two
// different users on one PC each get their own FoM.
constexpr const wchar_t* kMutexName = L"Local\\FoM_SingleInstance_v1";
constexpr const wchar_t* kPipeName  = L"\\\\.\\pipe\\FoM_agent_v1";
constexpr const wchar_t* kRunKey =
    L"Software\\Microsoft\\Windows\\CurrentVersion\\Run";
constexpr const wchar_t* kRunValue = L"FalloutWorld (FoM)";

HANDLE g_instance_mutex = nullptr;

constexpr std::uint32_t kStatusOk   = 0;
constexpr std::uint32_t kStatusBusy = 1;

struct PipeMessage {
    std::uint32_t command;
    std::uint32_t status;   // reply only
    std::uint64_t lobby;
};

std::wstring agent_command_line() {
    wchar_t buf[MAX_PATH] = {};
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return L"";
    return std::wstring(L"\"") + buf + L"\" --agent";
}

}  // namespace

// ---------------------------------------------------------------- instance

bool claim_single_instance() {
    if (g_instance_mutex) return true;
    g_instance_mutex = CreateMutexW(nullptr, TRUE, kMutexName);
    if (!g_instance_mutex) return false;
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(g_instance_mutex);
        g_instance_mutex = nullptr;
        return false;
    }
    return true;
}

void release_single_instance() {
    if (!g_instance_mutex) return;
    ReleaseMutex(g_instance_mutex);
    CloseHandle(g_instance_mutex);
    g_instance_mutex = nullptr;
}

bool agent_is_running() {
    // WaitNamedPipe against a pipe nobody created fails immediately, so this
    // doubles as a liveness probe without connecting.
    if (WaitNamedPipeW(kPipeName, 50)) return true;
    return GetLastError() != ERROR_FILE_NOT_FOUND;
}

// --------------------------------------------------------------------- IPC

namespace {

// Named-pipe I/O with a hard ceiling on how long it can take.
//
// CallNamedPipe is the obvious API and the wrong one: its timeout covers
// only *waiting for a free instance*, and the reply read after that blocks
// forever. An agent stuck on a menu prompt would therefore hang every
// FoM.exe the player double-clicks. Overlapped I/O with an explicit wait is
// the only way to bound it.
bool timed_io(HANDLE h, void* buf, DWORD len, bool write, DWORD timeout_ms,
              DWORD* done) {
    OVERLAPPED io{};
    io.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!io.hEvent) return false;

    BOOL ok = write ? WriteFile(h, buf, len, done, &io)
                    : ReadFile(h, buf, len, done, &io);
    if (!ok && GetLastError() == ERROR_IO_PENDING) {
        if (WaitForSingleObject(io.hEvent, timeout_ms) == WAIT_OBJECT_0) {
            ok = GetOverlappedResult(h, &io, done, FALSE);
        } else {
            CancelIoEx(h, &io);
            WaitForSingleObject(io.hEvent, 500);
            ok = FALSE;
        }
    }
    CloseHandle(io.hEvent);
    return ok == TRUE;
}

}  // namespace

AgentReply send_to_agent(AgentCommand cmd, std::uint64_t lobby) {
    if (!WaitNamedPipeW(kPipeName, 1500)) return AgentReply::NoAgent;

    HANDLE h = CreateFileW(kPipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                           OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
    if (h == INVALID_HANDLE_VALUE) return AgentReply::NoAgent;

    DWORD mode = PIPE_READMODE_MESSAGE;
    SetNamedPipeHandleState(h, &mode, nullptr, nullptr);

    PipeMessage msg{};
    msg.command = static_cast<std::uint32_t>(cmd);
    msg.lobby = lobby;

    AgentReply reply = AgentReply::NoAgent;
    DWORD n = 0;
    if (timed_io(h, &msg, sizeof(msg), /*write=*/true, 2000, &n) &&
        n == sizeof(msg)) {
        PipeMessage back{};
        if (timed_io(h, &back, sizeof(back), /*write=*/false, 3000, &n) &&
            n == sizeof(back)) {
            reply = (back.status == kStatusBusy) ? AgentReply::Busy
                                                 : AgentReply::Accepted;
        }
    }
    CloseHandle(h);
    return reply;
}

struct AgentServer::Impl {
    HANDLE              thread = nullptr;
    HANDLE              stop_event = nullptr;
    std::atomic<bool>   busy{false};
    std::atomic<bool>   quit{false};
    std::atomic<bool>   listening{false};
    std::mutex          mutex;
    std::deque<std::pair<AgentCommand, std::uint64_t>> queue;
};

namespace {

// One connection, start to finish. Returns false if the pipe should be
// rebuilt.
void serve_one(HANDLE pipe, AgentServer::Impl* impl) {
    PipeMessage msg{};
    DWORD n = 0;
    if (!timed_io(pipe, &msg, sizeof(msg), /*write=*/false, 3000, &n) ||
        n != sizeof(msg)) {
        return;
    }

    const auto cmd = static_cast<AgentCommand>(msg.command);
    const bool busy = impl->busy.load();

    PipeMessage reply{};
    reply.command = msg.command;
    reply.lobby = msg.lobby;

    if (busy) {
        // Refuse rather than queue: a session can last hours, and silently
        // acting on a command the player issued long ago is worse than
        // telling them to use the window that is already open.
        reply.status = kStatusBusy;
    } else {
        reply.status = kStatusOk;
        if (cmd == AgentCommand::Quit) {
            impl->quit.store(true);
        } else {
            std::lock_guard<std::mutex> lk(impl->mutex);
            impl->queue.emplace_back(cmd, msg.lobby);
        }
    }

    DWORD written = 0;
    timed_io(pipe, &reply, sizeof(reply), /*write=*/true, 2000, &written);
    FlushFileBuffers(pipe);
}

DWORD WINAPI agent_server_thread(LPVOID param) {
    auto* impl = static_cast<AgentServer::Impl*>(param);

    while (WaitForSingleObject(impl->stop_event, 0) != WAIT_OBJECT_0) {
        HANDLE pipe = CreateNamedPipeW(
            kPipeName,
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES,
            sizeof(PipeMessage), sizeof(PipeMessage), 0, nullptr);
        if (pipe == INVALID_HANDLE_VALUE) {
            impl->listening.store(false);
            Sleep(1000);
            continue;
        }
        impl->listening.store(true);

        OVERLAPPED ov{};
        ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        bool connected = false;
        if (ConnectNamedPipe(pipe, &ov)) {
            connected = true;
        } else {
            const DWORD err = GetLastError();
            if (err == ERROR_PIPE_CONNECTED) {
                connected = true;
            } else if (err == ERROR_IO_PENDING) {
                HANDLE waits[2] = { ov.hEvent, impl->stop_event };
                const DWORD w = WaitForMultipleObjects(2, waits, FALSE, INFINITE);
                if (w == WAIT_OBJECT_0) {
                    DWORD t = 0;
                    connected = GetOverlappedResult(pipe, &ov, &t, FALSE) != 0;
                } else {
                    CancelIoEx(pipe, &ov);
                }
            }
        }

        if (connected) serve_one(pipe, impl);

        DisconnectNamedPipe(pipe);
        CloseHandle(ov.hEvent);
        CloseHandle(pipe);
    }

    impl->listening.store(false);
    return 0;
}

}  // namespace

AgentServer::AgentServer() : impl_(new Impl()) {
    impl_->stop_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    impl_->thread = CreateThread(nullptr, 0, agent_server_thread, impl_, 0,
                                 nullptr);
    // Give the listener a moment to publish the pipe so that a FoM.exe
    // launched right behind us finds it.
    for (int i = 0; i < 50 && !impl_->listening.load(); ++i) Sleep(10);
}

AgentServer::~AgentServer() {
    if (!impl_) return;
    SetEvent(impl_->stop_event);
    // Unblock a listener parked in ConnectNamedPipe.
    HANDLE poke = CreateFileW(kPipeName, GENERIC_READ | GENERIC_WRITE, 0,
                              nullptr, OPEN_EXISTING, 0, nullptr);
    if (poke != INVALID_HANDLE_VALUE) CloseHandle(poke);
    if (impl_->thread) {
        WaitForSingleObject(impl_->thread, 3000);
        CloseHandle(impl_->thread);
    }
    if (impl_->stop_event) CloseHandle(impl_->stop_event);
    delete impl_;
    impl_ = nullptr;
}

bool AgentServer::listening() const {
    return impl_ && impl_->listening.load();
}

void AgentServer::set_busy(bool busy) {
    if (impl_) impl_->busy.store(busy);
}

bool AgentServer::quit_requested() const {
    return impl_ && impl_->quit.load();
}

bool AgentServer::next(AgentCommand* cmd_out, std::uint64_t* lobby_out) {
    if (!impl_) return false;
    std::lock_guard<std::mutex> lk(impl_->mutex);
    if (impl_->queue.empty()) return false;
    const auto item = impl_->queue.front();
    impl_->queue.pop_front();
    if (cmd_out) *cmd_out = item.first;
    if (lobby_out) *lobby_out = item.second;
    return true;
}

// ---------------------------------------------------------------- autostart

bool autostart_enabled() {
    HKEY key = nullptr;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, kRunKey, 0, KEY_READ, &key) !=
        ERROR_SUCCESS)
        return false;
    wchar_t buf[1024] = {};
    DWORD size = sizeof(buf);
    DWORD type = 0;
    const LSTATUS rc =
        RegQueryValueExW(key, kRunValue, nullptr, &type,
                         reinterpret_cast<LPBYTE>(buf), &size);
    RegCloseKey(key);
    if (rc != ERROR_SUCCESS || type != REG_SZ) return false;

    // Stale entry pointing at a FoM.exe that has moved or been deleted is
    // worse than no entry - report it as disabled so we rewrite it.
    const std::wstring want = agent_command_line();
    return !want.empty() && want == buf;
}

bool enable_autostart() {
    const std::wstring cmd = agent_command_line();
    if (cmd.empty()) return false;

    HKEY key = nullptr;
    if (RegCreateKeyExW(HKEY_CURRENT_USER, kRunKey, 0, nullptr, 0,
                        KEY_SET_VALUE, nullptr, &key, nullptr) != ERROR_SUCCESS)
        return false;
    const LSTATUS rc = RegSetValueExW(
        key, kRunValue, 0, REG_SZ,
        reinterpret_cast<const BYTE*>(cmd.c_str()),
        static_cast<DWORD>((cmd.size() + 1) * sizeof(wchar_t)));
    RegCloseKey(key);
    return rc == ERROR_SUCCESS;
}

bool disable_autostart() {
    HKEY key = nullptr;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, kRunKey, 0, KEY_SET_VALUE, &key) !=
        ERROR_SUCCESS)
        return false;
    const LSTATUS rc = RegDeleteValueW(key, kRunValue);
    RegCloseKey(key);
    return rc == ERROR_SUCCESS || rc == ERROR_FILE_NOT_FOUND;
}

// ------------------------------------------------------------ remembered FO4

void remember_fo4_dir(const std::wstring& dir) {
    const std::wstring base = appdata_dir();
    if (base.empty() || dir.empty()) return;
    const std::wstring path = join_path(base, L"fo4_path.txt");
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    const std::string utf8 = narrow(dir);
    DWORD w = 0;
    WriteFile(h, utf8.data(), static_cast<DWORD>(utf8.size()), &w, nullptr);
    CloseHandle(h);
}

std::wstring recall_fo4_dir() {
    const std::wstring base = appdata_dir();
    if (base.empty()) return L"";
    const std::wstring path = join_path(base, L"fo4_path.txt");
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE) return L"";
    char buf[1024] = {};
    DWORD n = 0;
    ReadFile(h, buf, sizeof(buf) - 1, &n, nullptr);
    CloseHandle(h);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r' ||
                     buf[n - 1] == ' '))
        buf[--n] = 0;
    if (n == 0) return L"";
    return widen(std::string(buf, n));
}

// ----------------------------------------------------------------- console

namespace {

// An agent with no console window can never show the player anything, which
// would turn any failure into a silent one. If we somehow ended up without a
// window (a launcher that used CREATE_NO_WINDOW, say), build one.
void ensure_console_window() {
    if (GetConsoleWindow()) return;
    FreeConsole();
    if (!AllocConsole()) return;
    FILE* f = nullptr;
    freopen_s(&f, "CONOUT$", "w", stdout);
    freopen_s(&f, "CONOUT$", "w", stderr);
    freopen_s(&f, "CONIN$", "r", stdin);
}

}  // namespace

void hide_console() {
    if (HWND h = GetConsoleWindow()) ShowWindow(h, SW_HIDE);
}

void show_console() {
    ensure_console_window();
    if (HWND h = GetConsoleWindow()) ShowWindow(h, SW_SHOWNORMAL);
}

void raise_console() {
    ensure_console_window();
    HWND h = GetConsoleWindow();
    if (!h) return;
    ShowWindow(h, SW_SHOWNORMAL);
    // SetForegroundWindow is refused for a background process unless we ask
    // nicely; the flash is the honest fallback when Windows says no.
    if (!SetForegroundWindow(h)) {
        FLASHWINFO fi{};
        fi.cbSize = sizeof(fi);
        fi.hwnd = h;
        fi.dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG;
        fi.uCount = 3;
        FlashWindowEx(&fi);
    }
}

}  // namespace fom
