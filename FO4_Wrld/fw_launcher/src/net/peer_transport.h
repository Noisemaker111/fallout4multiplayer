// Abstract peer-to-peer datagram transport, keyed by a 64-bit peer id.
//
// The real implementation is Steam's ISteamNetworkingMessages (peer id ==
// SteamID64). The test implementation is an in-process loopback pair. Keeping
// SteamUdpBridge behind this interface is what lets the bridge be unit-tested
// without a Steam client, a second machine, or two Steam accounts.
//
// Semantics deliberately mirror UDP: unordered, unreliable, datagram
// boundaries preserved, no connection state visible to the caller. That is
// exactly what our protocol already assumes (fw_native/src/net/reliable.*
// owns sequencing and retransmit), so the bridge never has to parse a frame.

#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>

namespace fom::net {

// Invoked once per received datagram. `data` is only valid for the duration
// of the call.
using PeerRecvFn =
    std::function<void(std::uint64_t peer, const std::uint8_t* data,
                       std::size_t len)>;

class IPeerTransport {
public:
    virtual ~IPeerTransport() = default;

    // Send one datagram to `peer`. Returns false if it could not be handed
    // to the transport at all (a true send failure; ordinary packet loss is
    // invisible here, as with UDP).
    virtual bool send(std::uint64_t peer, const void* data,
                      std::size_t len) = 0;

    // Drain everything received since the last call. Returns the number of
    // datagrams delivered to `cb`. Must not block.
    virtual int poll(const PeerRecvFn& cb) = 0;

    // Allow `peer` to talk to us (Steam requires an explicit accept for an
    // incoming P2P session). No-op for transports without that concept.
    virtual bool accept(std::uint64_t peer) { (void)peer; return true; }

    // Tear down state for a peer that left.
    virtual void close(std::uint64_t peer) { (void)peer; }
};

}  // namespace fom::net
