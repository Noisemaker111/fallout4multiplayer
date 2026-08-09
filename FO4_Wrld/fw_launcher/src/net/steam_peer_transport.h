// IPeerTransport backed by ISteamNetworkingMessages.
//
// Peer id == SteamID64. Steam handles NAT traversal directly and falls back
// to its own relay network (SDR) when a direct path cannot be punched, which
// is what makes "two friends on two different home networks" work with no
// port forwarding.
//
// Channel 0 carries FalloutWorld game frames verbatim.

#pragma once

#include <cstdint>

#include "../steam/steam_api.h"
#include "peer_transport.h"

namespace fom::net {

constexpr int kFomSteamChannel = 0;

class SteamPeerTransport : public IPeerTransport {
public:
    explicit SteamPeerTransport(fom::steam::SteamApi& api) : api_(api) {}

    bool send(std::uint64_t peer, const void* data, std::size_t len) override;
    int  poll(const PeerRecvFn& cb) override;
    bool accept(std::uint64_t peer) override;
    void close(std::uint64_t peer) override;

private:
    fom::steam::SteamApi& api_;
};

}  // namespace fom::net
