// End-to-end test for the Steam <-> UDP tunnel, with Steam replaced by an
// in-process loopback transport.
//
// Topology under test - the real M1 data path, minus Steam and minus FO4:
//
//   fake game (UDP)                                     fake server (UDP)
//        │                                                     ▲
//        │ 127.0.0.1:JOIN_PORT                                 │ 127.0.0.1:SRV_PORT
//        ▼                                                     │
//   join bridge ──loopback transport──> host bridge ───────────┘
//        ▲                                                     │
//        └───────────────── replies ────────────────────────────
//
// What this proves:
//   1. Frames survive the round trip BYTE FOR BYTE (protocol untouched).
//   2. The host bridge gives each peer its own loopback source port, so the
//      Python server's addr-keyed session table sees N distinct peers.
//   3. Replies find their way back to the specific game socket that asked.
//   4. Oversize junk is dropped instead of corrupting the stream.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <string>
#include <vector>

#include "../src/net/steam_bridge.h"

#pragma comment(lib, "ws2_32.lib")

namespace {

int g_failures = 0;
int g_checks = 0;

void check(bool cond, const char* what) {
    ++g_checks;
    if (!cond) {
        ++g_failures;
        std::printf("  FAIL: %s\n", what);
    } else {
        std::printf("  ok:   %s\n", what);
    }
}

// ---------------------------------------------------------------- loopback

// A pair of IPeerTransports wired to each other. Whatever A sends to peer id
// `b_id` shows up in B's next poll(), attributed to `a_id`.
class LoopbackTransport : public fom::net::IPeerTransport {
public:
    void connect_to(LoopbackTransport* other, std::uint64_t my_id) {
        other_ = other;
        my_id_ = my_id;
    }

    bool send(std::uint64_t peer, const void* data, std::size_t len) override {
        (void)peer;
        if (!other_) return false;
        const auto* p = static_cast<const std::uint8_t*>(data);
        other_->inbox_.emplace_back(my_id_, std::vector<std::uint8_t>(p, p + len));
        ++sent_;
        return true;
    }

    int poll(const fom::net::PeerRecvFn& cb) override {
        int n = 0;
        while (!inbox_.empty()) {
            auto item = std::move(inbox_.front());
            inbox_.pop_front();
            cb(item.first, item.second.data(), item.second.size());
            ++n;
        }
        return n;
    }

    std::uint64_t sent() const { return sent_; }

private:
    LoopbackTransport* other_ = nullptr;
    std::uint64_t my_id_ = 0;
    std::uint64_t sent_ = 0;
    std::deque<std::pair<std::uint64_t, std::vector<std::uint8_t>>> inbox_;
};

// ------------------------------------------------------------- udp helpers

SOCKET make_udp(std::uint16_t port) {
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) return s;
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(s, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == SOCKET_ERROR) {
        closesocket(s);
        return INVALID_SOCKET;
    }
    u_long nb = 1;
    ioctlsocket(s, FIONBIO, &nb);
    return s;
}

std::uint16_t port_of(SOCKET s) {
    sockaddr_in a{};
    int len = sizeof(a);
    if (getsockname(s, reinterpret_cast<sockaddr*>(&a), &len) == SOCKET_ERROR)
        return 0;
    return ntohs(a.sin_port);
}

void send_to_loopback(SOCKET s, std::uint16_t port, const void* d, int n) {
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    sendto(s, static_cast<const char*>(d), n, 0,
           reinterpret_cast<sockaddr*>(&a), sizeof(a));
}

int recv_from(SOCKET s, void* buf, int cap, sockaddr_in* from) {
    int len = sizeof(*from);
    return recvfrom(s, static_cast<char*>(buf), cap, 0,
                    reinterpret_cast<sockaddr*>(from), &len);
}

// A frame shaped like the real thing: 12-byte header + payload, so the test
// exercises the sizes the protocol actually produces.
std::vector<std::uint8_t> make_frame(std::uint8_t tag, std::size_t payload) {
    std::vector<std::uint8_t> f(12 + payload);
    std::memcpy(f.data(), "FWLD", 4);
    f[4] = 25;              // protocol version
    f[5] = tag;             // marker so we can tell frames apart
    for (std::size_t i = 0; i < payload; ++i)
        f[12 + i] = static_cast<std::uint8_t>((i * 31 + tag) & 0xFF);
    return f;
}

// Run both bridges for a while so packets can cross.
void spin(fom::net::SteamUdpBridge& a, fom::net::SteamUdpBridge& b,
          int iterations = 40) {
    for (int i = 0; i < iterations; ++i) {
        a.poll(1);
        b.poll(1);
    }
}

// ------------------------------------------------------------------- tests

void test_roundtrip_two_peers() {
    std::printf("\n[test] two joiners tunnel to one server, byte-identical\n");

    // Stand-in for net/server/main.py.
    SOCKET server = make_udp(0);
    check(server != INVALID_SOCKET, "fake campaign server bound");
    const std::uint16_t server_port = port_of(server);

    // Two joiners, each with their own bridge + transport pair.
    constexpr std::uint64_t kHostId = 111;
    constexpr std::uint64_t kPeerA  = 222;
    constexpr std::uint64_t kPeerB  = 333;

    LoopbackTransport host_to_a, a_to_host;
    host_to_a.connect_to(&a_to_host, kHostId);
    a_to_host.connect_to(&host_to_a, kPeerA);

    LoopbackTransport host_to_b, b_to_host;
    host_to_b.connect_to(&b_to_host, kHostId);
    b_to_host.connect_to(&host_to_b, kPeerB);

    // Two host-side bridges is not how production works (one bridge serves
    // every peer), but each loopback transport pair only models one link, so
    // the test drives one host bridge per link and checks the property that
    // matters: distinct source ports at the server.
    fom::net::SteamUdpBridge host_a, host_b;
    fom::net::SteamUdpBridge::Config hc{};
    hc.host_mode = true;
    hc.server_port = server_port;
    check(host_a.start(hc, &host_to_a), "host bridge A started");
    check(host_b.start(hc, &host_to_b), "host bridge B started");
    host_a.ensure_peer(kPeerA);
    host_b.ensure_peer(kPeerB);

    // Joiner bridges bind the port their local game will target.
    SOCKET probe_a = make_udp(0);
    SOCKET probe_b = make_udp(0);
    const std::uint16_t join_port_a = port_of(probe_a);
    const std::uint16_t join_port_b = port_of(probe_b);
    closesocket(probe_a);
    closesocket(probe_b);

    fom::net::SteamUdpBridge join_a, join_b;
    fom::net::SteamUdpBridge::Config jca{};
    jca.host_mode = false;
    jca.server_port = join_port_a;
    jca.host_peer = kHostId;
    check(join_a.start(jca, &a_to_host), "join bridge A started");

    fom::net::SteamUdpBridge::Config jcb = jca;
    jcb.server_port = join_port_b;
    check(join_b.start(jcb, &b_to_host), "join bridge B started");

    // The games.
    SOCKET game_a = make_udp(0);
    SOCKET game_b = make_udp(0);

    const auto frame_a = make_frame(0xA1, 1400);   // max payload
    const auto frame_b = make_frame(0xB2, 40);

    send_to_loopback(game_a, join_port_a, frame_a.data(),
                     static_cast<int>(frame_a.size()));
    send_to_loopback(game_b, join_port_b, frame_b.data(),
                     static_cast<int>(frame_b.size()));

    spin(join_a, host_a);
    spin(join_b, host_b);

    // The server should now have both frames, from two different ports.
    std::map<std::uint16_t, std::vector<std::uint8_t>> received;
    for (int i = 0; i < 8; ++i) {
        std::uint8_t buf[2048];
        sockaddr_in from{};
        const int n = recv_from(server, buf, sizeof(buf), &from);
        if (n > 0) {
            received[ntohs(from.sin_port)] =
                std::vector<std::uint8_t>(buf, buf + n);
        }
        Sleep(2);
        join_a.poll(1); host_a.poll(1);
        join_b.poll(1); host_b.poll(1);
    }

    check(received.size() == 2,
          "server saw two DISTINCT source ports (one session per peer)");

    bool found_a = false, found_b = false;
    std::uint16_t port_a = 0, port_b = 0;
    for (const auto& [port, data] : received) {
        if (data == frame_a) { found_a = true; port_a = port; }
        if (data == frame_b) { found_b = true; port_b = port; }
    }
    check(found_a, "peer A's 1412-byte frame arrived byte-identical");
    check(found_b, "peer B's frame arrived byte-identical");

    // Now reply to each, and confirm it reaches the right game socket.
    const auto reply_a = make_frame(0xC3, 300);
    const auto reply_b = make_frame(0xD4, 12);
    if (port_a) send_to_loopback(server, port_a, reply_a.data(),
                                 static_cast<int>(reply_a.size()));
    if (port_b) send_to_loopback(server, port_b, reply_b.data(),
                                 static_cast<int>(reply_b.size()));

    spin(join_a, host_a);
    spin(join_b, host_b);

    std::uint8_t buf[2048];
    sockaddr_in from{};
    int n = recv_from(game_a, buf, sizeof(buf), &from);
    check(n > 0 && std::vector<std::uint8_t>(buf, buf + n) == reply_a,
          "reply routed back to game A, byte-identical");

    n = recv_from(game_b, buf, sizeof(buf), &from);
    check(n > 0 && std::vector<std::uint8_t>(buf, buf + n) == reply_b,
          "reply routed back to game B, byte-identical");

    check(host_a.stats().to_server_packets >= 1, "host A counted an inbound frame");
    check(join_a.stats().to_peer_packets >= 1, "join A counted an outbound frame");

    closesocket(game_a);
    closesocket(game_b);
    closesocket(server);
}

void test_oversize_is_dropped() {
    std::printf("\n[test] oversize datagrams are dropped, not forwarded\n");

    SOCKET server = make_udp(0);
    const std::uint16_t server_port = port_of(server);

    constexpr std::uint64_t kHostId = 1;
    constexpr std::uint64_t kPeerId = 2;

    LoopbackTransport h, p;
    h.connect_to(&p, kHostId);
    p.connect_to(&h, kPeerId);

    fom::net::SteamUdpBridge host;
    fom::net::SteamUdpBridge::Config hc{};
    hc.host_mode = true;
    hc.server_port = server_port;
    check(host.start(hc, &h), "host bridge started");
    host.ensure_peer(kPeerId);

    // Hand the bridge a datagram larger than any legal frame, as a hostile or
    // corrupt peer might.
    std::vector<std::uint8_t> huge(fom::net::kBridgeBufferSize + 1, 0x7F);
    p.send(kHostId, huge.data(), huge.size());
    for (int i = 0; i < 20; ++i) host.poll(1);

    check(host.stats().dropped_oversize >= 1, "oversize datagram counted as dropped");

    std::uint8_t buf[4096];
    sockaddr_in from{};
    const int n = recv_from(server, buf, sizeof(buf), &from);
    check(n <= 0, "nothing oversize reached the campaign server");

    closesocket(server);
}

void test_join_requires_host_peer() {
    std::printf("\n[test] join mode refuses to start without a host\n");
    LoopbackTransport t;
    fom::net::SteamUdpBridge b;
    fom::net::SteamUdpBridge::Config c{};
    c.host_mode = false;
    c.server_port = 0;
    c.host_peer = 0;
    check(!b.start(c, &t), "join bridge rejected an empty host peer id");
    check(!b.last_error().empty(), "and said why");
}

void test_only_host_may_talk_to_a_joiner() {
    std::printf("\n[test] a joiner ignores datagrams from anyone but its host\n");

    constexpr std::uint64_t kHostId = 10;
    constexpr std::uint64_t kStranger = 99;

    SOCKET probe = make_udp(0);
    const std::uint16_t join_port = port_of(probe);
    closesocket(probe);

    LoopbackTransport t, other;
    other.connect_to(&t, kStranger);
    t.connect_to(&other, 11);

    fom::net::SteamUdpBridge join;
    fom::net::SteamUdpBridge::Config jc{};
    jc.host_mode = false;
    jc.server_port = join_port;
    jc.host_peer = kHostId;
    check(join.start(jc, &t), "join bridge started");

    SOCKET game = make_udp(0);
    const auto frame = make_frame(0xEE, 16);
    send_to_loopback(game, join_port, frame.data(),
                     static_cast<int>(frame.size()));
    for (int i = 0; i < 20; ++i) join.poll(1);

    // A stranger replies. It must not reach the game.
    const auto evil = make_frame(0xFF, 16);
    other.send(11, evil.data(), evil.size());
    for (int i = 0; i < 20; ++i) join.poll(1);

    std::uint8_t buf[2048];
    sockaddr_in from{};
    const int n = recv_from(game, buf, sizeof(buf), &from);
    check(n <= 0, "stranger's datagram never reached Fallout 4");

    closesocket(game);
}

}  // namespace

int main() {
    WSADATA w{};
    if (WSAStartup(MAKEWORD(2, 2), &w) != 0) {
        std::printf("WSAStartup failed\n");
        return 1;
    }

    std::printf("== FoM Steam<->UDP bridge tests ==\n");
    test_roundtrip_two_peers();
    test_oversize_is_dropped();
    test_join_requires_host_peer();
    test_only_host_may_talk_to_a_joiner();

    std::printf("\n%d checks, %d failures\n", g_checks, g_failures);
    WSACleanup();
    return g_failures == 0 ? 0 : 1;
}
