#include "steam_bridge.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <mstcpip.h>
#include <windows.h>

#include <cstring>

#pragma comment(lib, "ws2_32.lib")

// Lives in mswsock.h / mstcpip.h depending on SDK vintage; define it
// ourselves rather than depend on which one this toolchain ships.
#ifndef SIO_UDP_CONNRESET
#define SIO_UDP_CONNRESET _WSAIOW(IOC_VENDOR, 12)
#endif

namespace fom::net {

namespace {

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

void make_loopback_addr(std::uint16_t port, sockaddr_in* out) {
    std::memset(out, 0, sizeof(*out));
    out->sin_family = AF_INET;
    out->sin_port = htons(port);
    out->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
}

SOCKET open_udp(std::uint16_t bind_port, bool loopback_only, int* err) {
    if (!winsock_once()) {
        if (err) *err = WSANOTINITIALISED;
        return INVALID_SOCKET;
    }
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) {
        if (err) *err = WSAGetLastError();
        return INVALID_SOCKET;
    }

    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons(bind_port);
    local.sin_addr.s_addr =
        loopback_only ? htonl(INADDR_LOOPBACK) : htonl(INADDR_ANY);
    if (bind(s, reinterpret_cast<sockaddr*>(&local), sizeof(local)) ==
        SOCKET_ERROR) {
        if (err) *err = WSAGetLastError();
        closesocket(s);
        return INVALID_SOCKET;
    }

    // Windows raises WSAECONNRESET on a subsequent recvfrom when a previous
    // sendto drew an ICMP port-unreachable (e.g. the server is not up yet).
    // Suppress it so a not-yet-started server does not kill the socket.
    BOOL behavior = FALSE;
    DWORD bytes = 0;
    WSAIoctl(s, SIO_UDP_CONNRESET, &behavior, sizeof(behavior), nullptr, 0,
             &bytes, nullptr, nullptr);

    u_long nonblock = 1;
    ioctlsocket(s, FIONBIO, &nonblock);
    return s;
}

std::uint64_t now_ms() {
    return static_cast<std::uint64_t>(GetTickCount64());
}

}  // namespace

SteamUdpBridge::~SteamUdpBridge() {
    stop();
}

bool SteamUdpBridge::start(const Config& cfg, IPeerTransport* transport) {
    stop();
    cfg_ = cfg;
    transport_ = transport;
    last_error_.clear();
    rx_buf_.resize(kBridgeBufferSize);

    if (!transport_) {
        last_error_ = "no peer transport";
        return false;
    }

    if (cfg_.host_mode) {
        // Cache where the campaign server is listening. Per-peer sockets are
        // created lazily as peers appear.
        make_loopback_addr(cfg_.server_port,
                           reinterpret_cast<sockaddr_in*>(server_addr_));
    } else {
        if (cfg_.host_peer == 0) {
            last_error_ = "join mode requires a host peer id";
            return false;
        }
        int err = 0;
        const SOCKET s = open_udp(cfg_.server_port, /*loopback_only=*/true, &err);
        if (s == INVALID_SOCKET) {
            if (err == WSAEADDRINUSE) {
                last_error_ =
                    "port " + std::to_string(cfg_.server_port) +
                    " on 127.0.0.1 is already in use - a FalloutWorld server "
                    "from a previous Host run is probably still running. "
                    "Close it and retry.";
            } else {
                last_error_ = "could not bind 127.0.0.1:" +
                              std::to_string(cfg_.server_port) +
                              " (winsock error " + std::to_string(err) + ")";
            }
            return false;
        }
        local_sock_ = static_cast<std::uintptr_t>(s);
    }

    running_ = true;
    return true;
}

void SteamUdpBridge::stop() {
    for (auto& [peer, link] : peers_) {
        (void)peer;
        if (link.sock != 0)
            closesocket(static_cast<SOCKET>(link.sock));
    }
    peers_.clear();
    if (local_sock_ != 0) {
        closesocket(static_cast<SOCKET>(local_sock_));
        local_sock_ = 0;
    }
    game_addr_known_ = false;
    running_ = false;
    transport_ = nullptr;
}

void SteamUdpBridge::ensure_peer(std::uint64_t peer) {
    if (!running_ || !cfg_.host_mode || peer == 0) return;
    if (peers_.count(peer)) return;

    int err = 0;
    // Ephemeral loopback port - this is what the Python server will see as
    // this peer's source address, keeping one session per peer.
    const SOCKET s = open_udp(0, /*loopback_only=*/true, &err);
    if (s == INVALID_SOCKET) {
        last_error_ = "could not open a loopback socket for peer (winsock "
                      "error " + std::to_string(err) + ")";
        return;
    }
    PeerLink link{};
    link.sock = static_cast<std::uintptr_t>(s);
    link.last_rx_ms = now_ms();
    peers_.emplace(peer, link);
    stats_.active_peers = peers_.size();
}

std::vector<std::uint64_t> SteamUdpBridge::peer_ids() const {
    std::vector<std::uint64_t> out;
    out.reserve(peers_.size());
    for (const auto& [peer, link] : peers_) {
        (void)link;
        out.push_back(peer);
    }
    return out;
}

void SteamUdpBridge::forget_peer(std::uint64_t peer) {
    auto it = peers_.find(peer);
    if (it == peers_.end()) return;
    if (it->second.sock != 0)
        closesocket(static_cast<SOCKET>(it->second.sock));
    peers_.erase(it);
    stats_.active_peers = peers_.size();
    if (transport_) transport_->close(peer);
}

// ------------------------------------------------------- peer -> local UDP

void SteamUdpBridge::on_peer_datagram(std::uint64_t peer,
                                      const std::uint8_t* data,
                                      std::size_t len) {
    if (len == 0 || len > kBridgeBufferSize) {
        ++stats_.dropped_oversize;
        return;
    }

    if (cfg_.host_mode) {
        ensure_peer(peer);
        auto it = peers_.find(peer);
        if (it == peers_.end()) return;
        it->second.last_rx_ms = now_ms();
        const auto* sa = reinterpret_cast<const sockaddr_in*>(server_addr_);
        const int n = sendto(static_cast<SOCKET>(it->second.sock),
                             reinterpret_cast<const char*>(data),
                             static_cast<int>(len), 0,
                             reinterpret_cast<const sockaddr*>(sa),
                             sizeof(*sa));
        if (n == SOCKET_ERROR) {
            ++stats_.send_failures;
            return;
        }
    } else {
        // Joiner: only the host may speak to us, and only once the local game
        // has told us which ephemeral port it is listening on.
        if (peer != cfg_.host_peer) return;
        if (!game_addr_known_) return;
        const auto* ga = reinterpret_cast<const sockaddr_in*>(game_addr_);
        const int n = sendto(static_cast<SOCKET>(local_sock_),
                             reinterpret_cast<const char*>(data),
                             static_cast<int>(len), 0,
                             reinterpret_cast<const sockaddr*>(ga),
                             sizeof(*ga));
        if (n == SOCKET_ERROR) {
            ++stats_.send_failures;
            return;
        }
    }

    ++stats_.to_server_packets;
    stats_.to_server_bytes += len;
}

// ------------------------------------------------------- local UDP -> peer

void SteamUdpBridge::drain_udp() {
    // Bounded per iteration so a flood cannot starve the Steam pump.
    constexpr int kMaxPerSocket = 64;

    auto forward = [&](SOCKET s, std::uint64_t peer, bool learn_game_addr) {
        for (int i = 0; i < kMaxPerSocket; ++i) {
            sockaddr_in from{};
            int from_len = sizeof(from);
            const int n = recvfrom(s,
                                   reinterpret_cast<char*>(rx_buf_.data()),
                                   static_cast<int>(rx_buf_.size()), 0,
                                   reinterpret_cast<sockaddr*>(&from),
                                   &from_len);
            if (n == SOCKET_ERROR) {
                const int err = WSAGetLastError();
                if (err == WSAEWOULDBLOCK) return;
                if (err == WSAECONNRESET) continue;  // stale ICMP, ignore
                return;
            }
            if (n <= 0) continue;

            if (learn_game_addr) {
                // First datagram from Fallout4.exe teaches us its ephemeral
                // source port; that is where replies must go.
                if (!game_addr_known_) {
                    std::memcpy(game_addr_, &from, sizeof(from));
                    game_addr_known_ = true;
                } else {
                    auto* known = reinterpret_cast<sockaddr_in*>(game_addr_);
                    if (known->sin_port != from.sin_port) {
                        // The game restarted and picked a new port. Follow it.
                        std::memcpy(game_addr_, &from, sizeof(from));
                    }
                }
            }

            if (transport_->send(peer, rx_buf_.data(),
                                 static_cast<std::size_t>(n))) {
                ++stats_.to_peer_packets;
                stats_.to_peer_bytes += static_cast<std::uint64_t>(n);
            } else {
                ++stats_.send_failures;
            }
        }
    };

    if (cfg_.host_mode) {
        for (auto& [peer, link] : peers_) {
            if (link.sock == 0) continue;
            forward(static_cast<SOCKET>(link.sock), peer,
                    /*learn_game_addr=*/false);
        }
    } else if (local_sock_ != 0) {
        forward(static_cast<SOCKET>(local_sock_), cfg_.host_peer,
                /*learn_game_addr=*/true);
    }
}

// -------------------------------------------------------------------- poll

void SteamUdpBridge::poll(int timeout_ms) {
    if (!running_ || !transport_) return;

    transport_->poll([this](std::uint64_t peer, const std::uint8_t* data,
                            std::size_t len) {
        on_peer_datagram(peer, data, len);
    });

    // Wait for local UDP readiness so we do not spin a core.
    fd_set rfds;
    FD_ZERO(&rfds);
    int nfds = 0;
    if (cfg_.host_mode) {
        for (const auto& [peer, link] : peers_) {
            (void)peer;
            if (link.sock == 0) continue;
            if (nfds >= FD_SETSIZE) break;
            FD_SET(static_cast<SOCKET>(link.sock), &rfds);
            ++nfds;
        }
    } else if (local_sock_ != 0) {
        FD_SET(static_cast<SOCKET>(local_sock_), &rfds);
        ++nfds;
    }

    if (nfds == 0) {
        // Nothing to watch yet (host with no peers). Do not call select()
        // with an empty set - Windows fails it with WSAEINVAL.
        if (timeout_ms > 0) Sleep(static_cast<DWORD>(timeout_ms));
        return;
    }

    timeval tv{};
    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    const int sel = select(0, &rfds, nullptr, nullptr, &tv);
    if (sel > 0) drain_udp();
}

}  // namespace fom::net
