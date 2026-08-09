// Pluggable datagram transport for the FO4_Wrld client.
//
// The client used to talk to UdpSocket directly. It now goes through this
// interface so the byte stream can be carried by something other than a raw
// Winsock UDP socket without the client loop knowing or caring.
//
// Today there is exactly one implementation, UdpTransport, and behaviour is
// bit-for-bit what it always was.
//
// The reason the seam exists: FoM.exe tunnels co-op traffic over Steam P2P
// (fw_launcher/src/net/steam_bridge.*) so friends can join without typing an
// IP. In milestone 1 that tunnel terminates OUTSIDE the game - FoM binds
// 127.0.0.1:31337 on the joining machine and the game still speaks plain UDP
// to what it believes is a local server. That keeps dxgi.dll completely out
// of the Steam API, which matters because Fallout 4 already runs its own
// SteamAPI session under its own AppID and a second init inside our DLL is a
// fight we do not need to pick.
//
// When we do want to remove that last loopback hop, a SteamTransport
// implementing this interface is the whole change: make_transport() picks it
// up from config and client.cpp is untouched.
//
// Contract (deliberately identical to the old UdpSocket semantics):
//   - datagram oriented, unreliable, unordered; reliability lives above, in
//     ReliableChannel
//   - recv() blocks up to timeout_ms, returns bytes, 0 on timeout, -1 on error
//   - not thread-safe; the client's worker thread owns the instance

#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

#include "../config.h"

namespace fw::net {

class ITransport {
public:
    virtual ~ITransport() = default;

    // Establish the connection to the configured server. False on failure
    // (inspect last_error()).
    virtual bool open() = 0;
    virtual void close() = 0;

    // Send one datagram. True if the transport accepted the whole thing.
    virtual bool send(const void* data, std::size_t len) = 0;

    // Wait up to timeout_ms for one datagram. >0 = bytes received,
    // 0 = timeout / ignorable, -1 = error.
    virtual int recv(void* buffer, std::size_t buffer_len, int timeout_ms) = 0;

    virtual bool is_open()    const noexcept = 0;
    virtual int  last_error() const noexcept = 0;

    // For logging, e.g. "udp".
    virtual const char* name() const noexcept = 0;
};

// Builds the transport described by `cfg`. Never returns null.
std::unique_ptr<ITransport> make_transport(const config::Settings& cfg);

}  // namespace fw::net
