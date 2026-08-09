#include "steam_peer_transport.h"

namespace fom::net {

bool SteamPeerTransport::send(std::uint64_t peer, const void* data,
                              std::size_t len) {
    if (len == 0 || len > 0xFFFFFFFFull) return false;
    return api_.send_to_user(peer, data, static_cast<std::uint32_t>(len),
                             kFomSteamChannel);
}

int SteamPeerTransport::poll(const PeerRecvFn& cb) {
    // Drain in batches until Steam has nothing left, so a burst cannot build
    // a backlog across bridge iterations.
    constexpr int kBatch = 64;
    fom::steam::NetMessage* msgs[kBatch] = {};
    int total = 0;

    for (;;) {
        const int n = api_.receive_on_channel(kFomSteamChannel, msgs, kBatch);
        if (n <= 0) break;
        for (int i = 0; i < n; ++i) {
            auto* m = msgs[i];
            if (!m) continue;
            if (m->data && m->size > 0) {
                cb(m->peer.steam_id(),
                   static_cast<const std::uint8_t*>(m->data),
                   static_cast<std::size_t>(m->size));
                ++total;
            }
            api_.release_message(m);
            msgs[i] = nullptr;
        }
        if (n < kBatch) break;
    }
    return total;
}

bool SteamPeerTransport::accept(std::uint64_t peer) {
    return api_.accept_session(peer);
}

void SteamPeerTransport::close(std::uint64_t peer) {
    api_.close_session(peer);
}

}  // namespace fom::net
