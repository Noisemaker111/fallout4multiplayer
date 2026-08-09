#include "fom_util.h"

#include <windows.h>
#include <shlobj.h>

#include <cstdarg>
#include <cstdio>
#include <cwchar>
#include <vector>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")

namespace fom {

void logw(const wchar_t* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::vwprintf(fmt, ap);
    va_end(ap);
    std::fflush(stdout);
}

std::wstring exe_dir() {
    wchar_t buf[MAX_PATH] = {};
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return L".";
    std::wstring s(buf, n);
    const auto slash = s.find_last_of(L"\\/");
    if (slash == std::wstring::npos) return L".";
    return s.substr(0, slash);
}

std::wstring join_path(const std::wstring& a, const std::wstring& b) {
    if (a.empty()) return b;
    if (a.back() == L'\\' || a.back() == L'/') return a + b;
    return a + L"\\" + b;
}

bool file_exists(const std::wstring& p) {
    const DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

std::string narrow(const std::wstring& w) {
    if (w.empty()) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(),
                                      static_cast<int>(w.size()),
                                      nullptr, 0, nullptr, nullptr);
    if (n <= 0) return {};
    std::string out(static_cast<std::size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), static_cast<int>(w.size()),
                        out.data(), n, nullptr, nullptr);
    return out;
}

std::wstring widen(const std::string& s) {
    if (s.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(),
                                      static_cast<int>(s.size()), nullptr, 0);
    if (n <= 0) return {};
    std::wstring out(static_cast<std::size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()),
                        out.data(), n);
    return out;
}

std::wstring appdata_dir() {
    wchar_t* path = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &path)))
        return L"";
    std::wstring base = path;
    CoTaskMemFree(path);
    const std::wstring dir = join_path(base, L"FoM");
    CreateDirectoryW(dir.c_str(), nullptr);
    return dir;
}

std::uint64_t now_ms() {
    return static_cast<std::uint64_t>(GetTickCount64());
}

std::string to_base36(std::uint64_t v) {
    static const char* kDigits = "0123456789abcdefghijklmnopqrstuvwxyz";
    if (v == 0) return "0";
    char buf[16] = {};
    int i = static_cast<int>(sizeof(buf)) - 1;
    while (v > 0 && i > 0) {
        buf[--i] = kDigits[v % 36];
        v /= 36;
    }
    return std::string(&buf[i]);
}

std::uint64_t from_base36(const std::string& s) {
    if (s.empty() || s.size() > 13) return 0;
    std::uint64_t v = 0;
    for (const char c : s) {
        int d;
        if (c >= '0' && c <= '9')      d = c - '0';
        else if (c >= 'a' && c <= 'z') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'Z') d = c - 'A' + 10;
        else return 0;
        // Overflow guard: 36^13 > 2^64, so the last digit can wrap.
        if (v > (0xFFFFFFFFFFFFFFFFull - static_cast<std::uint64_t>(d)) / 36ull)
            return 0;
        v = v * 36ull + static_cast<std::uint64_t>(d);
    }
    return v;
}

std::string steam_peer_id(std::uint64_t steam_id64) {
    if (steam_id64 == 0) return {};
    // "s" prefix keeps the id from ever starting with a digit and marks the
    // namespace. 1 + 13 = 14 chars worst case, under MAX_CLIENT_ID_LEN (15).
    return "s" + to_base36(steam_id64);
}

}  // namespace fom
