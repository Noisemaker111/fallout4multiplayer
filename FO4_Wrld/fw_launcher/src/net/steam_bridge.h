// SteamUdpBridge - splices the existing FO4_Wrld UDP protocol onto a
// peer-to-peer transport, without changing a single byte on the wire.
//
// This is the piece that makes "join over the internet with no typed IP"
// work while dxgi.dll and net/server/main.py stay exactly as they are.
//
// HOST MODE
//
//   Fallout4.exe + dxgi.dll ──UDP──> 127.0.0.1:31337 ──> net/server/main.py
//                                                             ▲
//   remote peer ──Steam P2P──> FoM bridge ──UDP from a         │
//                              dedicated loopback socket ──────┘
//
//   One loopback socket is allocated PER remote peer. That matters: the
//   Python server keys sessions on the source (ip, port) tuple
//   (net/server/state.py `_sessions_by_addr`), so funnelling every peer
//   through one socket would collapse them all into a single session. With a
//   socket each, the server sees 127.0.0.1:<distinct ephemeral> per peer and
//   its multi-peer fan-out (net/tests/test_multipeer.py) works untouched.
//
// JOIN MODE
//
//   Fallout4.exe + dxgi.dll ──UDP──> 127.0.0.1:31337 ──> FoM bridge
//                                                            │
//                                                        Steam P2P
//                                                            ▼
//                                                       host's bridge
//
//   The bridge binds the port the game thinks the server lives on, so the
//   joiner's fw_config.ini says `server = 127.0.0.1:31337` forever - no
//   public IP, no port forwarding, no "what's your IPv4".
//
// The bridge is protocol-agnostic on purpose: it forwards opaque datagrams.
// A protocol bump needs no change here.
//
// Threading: single-threaded. Call start(), then poll() in a loop.

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "peer_transport.h"

namespace fom::net {

// Wire buffer: MAX_FRAME_SIZE is 1412 (12 B header + 1400 B payload).
// 2048 leaves headroom without ever truncating a legal frame.
constexpr std::size_t kBridgeBufferSize = 2048;

struct BridgeStats {
    std::uint64_t to_server_packets = 0;   // peer  -> local UDP
    std::uint64_t to_server_bytes   = 0;
    std::uint64_t to_peer_packets   = 0;   // local UDP -> peer
    std::uint64_t to_peer_bytes     = 0;
    std::uint64_t dropped_oversize  = 0;
    std::uint64_t send_failures     = 0;
    std::size_t   active_peers      = 0;
};

class SteamUdpBridge {
public:
    struct Config {
        // true  = we run the campaign server; fan peers out to it.
        // false = we are a joiner; impersonate the server locally.
        bool          host_mode = true;

        // Host mode: where net/server/main.py is listening (127.0.0.1:31337).
        // Join mode: the port the local game will send to, which we bind.
        std::uint16_t server_port = 31337;

        // Join mode only: which peer to tunnel the game's traffic to.
        std::uint64_t host_peer = 0;
    };

    SteamUdpBridge() = default;
    ~SteamUdpBridge();

    SteamUdpBridge(const SteamUdpBridge&) = delete;
    SteamUdpBridge& operator=(const SteamUdpBridge&) = delete;

    // Returns false and fills last_error() on failure (the common one being
    // "join mode but something already holds 127.0.0.1:31337", i.e. a stray
    // server process from a previous Host run).
    bool start(const Config& cfg, IPeerTransport* transport);

    // One iteration: drain the peer transport into UDP, then wait up to
    // `timeout_ms` for UDP traffic and drain that back out to peers.
    void poll(int timeout_ms);

    void stop();

    // Host mode: pre-create the loopback socket for a peer we already know
    // about (a lobby member). Safe to call repeatedly. In join mode this is
    // a no-op - a joiner only ever talks to the host.
    void ensure_peer(std::uint64_t peer);

    // Drop a peer that left the lobby, closing its loopback socket.
    void forget_peer(std::uint64_t peer);

    // Peers we currently hold a loopback socket for (host mode).
    std::vector<std::uint64_t> peer_ids() const;

    const BridgeStats& stats() const { return stats_; }
    const std::string& last_error() const { return last_error_; }
    bool  running() const { return running_; }

    // Has the local game actually connected to us yet? Join mode only -
    // true once a datagram has arrived from Fallout4.exe.
    bool  game_attached() const { return game_addr_known_; }

private:
    struct PeerLink {
        std::uintptr_t sock = 0;      // SOCKET, loopback, host mode only
        std::uint64_t  last_rx_ms = 0;
    };

    void on_peer_datagram(std::uint64_t peer, const std::uint8_t* data,
                          std::size_t len);
    void drain_udp();

    Config          cfg_{};
    IPeerTransport* transport_ = nullptr;
    bool            running_ = false;
    std::string     last_error_;
    BridgeStats     stats_{};

    // Host mode: one socket per peer. Join mode: unused.
    std::unordered_map<std::uint64_t, PeerLink> peers_;

    // Join mode: the socket bound to server_port that the local game targets.
    std::uintptr_t  local_sock_ = 0;
    bool            game_addr_known_ = false;
    unsigned char   game_addr_[16] = {0};   // sockaddr_in of Fallout4.exe

    // Host mode: cached sockaddr_in of 127.0.0.1:server_port.
    unsigned char   server_addr_[16] = {0};

    std::vector<std::uint8_t> rx_buf_;
};

}  // namespace fom::net
