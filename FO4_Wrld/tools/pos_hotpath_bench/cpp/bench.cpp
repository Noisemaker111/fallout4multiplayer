// POS hot-path microbench — same algorithm as python/bench.py
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static constexpr double MAX_SPEED = 2500.0;
static constexpr double MAX_VSPEED = 5000.0;
static constexpr double MIN_DT_MS = 20.0;
static constexpr double COORD_BOUND = 1e7;
static constexpr int PEER_ID_LEN = 16;
static constexpr int BODY_LEN = 36;
static constexpr uint8_t OK = 0, TOO_FAST = 5, SPEED = 2, VERTICAL = 4, NON_FINITE = 10, TS_INV = 3;

struct Peer {
    uint8_t peer_id[PEER_ID_LEN]{};
    float last_x = 0, last_y = 0, last_z = 0;
    uint64_t last_ts = 0;
    uint32_t last_cell = 0;
    double last_at_ms = 0;
    bool has = false;
};

static void pack_pos(uint8_t* buf, float x, float y, float z, float rx, float ry, float rz,
                     uint64_t ts, uint32_t cell) {
    std::memcpy(buf + 0, &x, 4);
    std::memcpy(buf + 4, &y, 4);
    std::memcpy(buf + 8, &z, 4);
    std::memcpy(buf + 12, &rx, 4);
    std::memcpy(buf + 16, &ry, 4);
    std::memcpy(buf + 20, &rz, 4);
    std::memcpy(buf + 24, &ts, 8);
    std::memcpy(buf + 32, &cell, 4);
}

static void unpack_pos(const uint8_t* buf, float& x, float& y, float& z, float& rx, float& ry,
                       float& rz, uint64_t& ts, uint32_t& cell) {
    std::memcpy(&x, buf + 0, 4);
    std::memcpy(&y, buf + 4, 4);
    std::memcpy(&z, buf + 8, 4);
    std::memcpy(&rx, buf + 12, 4);
    std::memcpy(&ry, buf + 16, 4);
    std::memcpy(&rz, buf + 20, 4);
    std::memcpy(&ts, buf + 24, 8);
    std::memcpy(&cell, buf + 32, 4);
}

static uint8_t validate(const Peer& peer, float x, float y, float z, float rx, float ry, float rz,
                        uint64_t ts, uint32_t cell, double now_ms) {
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) || !std::isfinite(rx) ||
        !std::isfinite(ry) || !std::isfinite(rz))
        return NON_FINITE;
    if (std::fabs(x) > COORD_BOUND || std::fabs(y) > COORD_BOUND || std::fabs(z) > COORD_BOUND)
        return NON_FINITE;
    if (!peer.has) return OK;
    double dt_ms = now_ms - peer.last_at_ms;
    if (dt_ms < MIN_DT_MS) return TOO_FAST;
    if (ts < peer.last_ts) return TS_INV;
    if (cell != 0 && peer.last_cell != 0 && cell != peer.last_cell) return OK;
    double dt_s = dt_ms / 1000.0;
    double dx = x - peer.last_x, dy = y - peer.last_y, dz = z - peer.last_z;
    double dist = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (dist / dt_s > MAX_SPEED) return SPEED;
    if (std::fabs(dz) / dt_s > MAX_VSPEED) return VERTICAL;
    return OK;
}

static void run(size_t iters, size_t n_peers) {
    std::vector<Peer> peers(n_peers);
    std::vector<std::array<uint8_t, BODY_LEN>> bodies(n_peers);
    for (size_t i = 0; i < n_peers; ++i) {
        std::string s = "player_" + std::to_string(i);
        std::memset(peers[i].peer_id, 0, PEER_ID_LEN);
        std::memcpy(peers[i].peer_id, s.data(), s.size());
        pack_pos(bodies[i].data(), -76000.f + float(i) * 100.f, 93000.f, 7700.f, 0, 0, 0.1f,
                 1000000ull + i, 0x00024A02u);
    }
    std::vector<uint8_t> out((PEER_ID_LEN + BODY_LEN) * (n_peers - 1));
    uint64_t accepts = 0, rejects = 0;
    double now = 1000000.0;

    auto t0 = std::chrono::steady_clock::now();
    for (size_t k = 0; k < iters; ++k) {
        for (size_t i = 0; i < n_peers; ++i) {
            float x, y, z, rx, ry, rz;
            uint64_t ts;
            uint32_t cell;
            unpack_pos(bodies[i].data(), x, y, z, rx, ry, rz, ts, cell);
            x += 6.f;
            ts += 20;
            pack_pos(bodies[i].data(), x, y, z, rx, ry, rz, ts, cell);
            now += 20.0;
            uint8_t reason = validate(peers[i], x, y, z, rx, ry, rz, ts, cell, now);
            if (reason != OK) {
                ++rejects;
                continue;
            }
            ++accepts;
            peers[i].last_x = x;
            peers[i].last_y = y;
            peers[i].last_z = z;
            peers[i].last_ts = ts;
            peers[i].last_cell = cell;
            peers[i].last_at_ms = now;
            peers[i].has = true;
            size_t o = 0;
            for (size_t j = 0; j < n_peers; ++j) {
                if (j == i) continue;
                std::memcpy(out.data() + o, peers[i].peer_id, PEER_ID_LEN);
                std::memcpy(out.data() + o + PEER_ID_LEN, bodies[i].data(), BODY_LEN);
                o += PEER_ID_LEN + BODY_LEN;
            }
        }
    }
    auto t1 = std::chrono::steady_clock::now();
    double elapsed = std::chrono::duration<double>(t1 - t0).count();
    double ops = double(iters * n_peers);
    std::printf("lang=cpp peers=%zu iters=%zu ops=%.0f\n", n_peers, iters, ops);
    std::printf("elapsed_s=%.6f ns_per_op=%.1f ops_per_s=%.0f\n", elapsed, elapsed * 1e9 / ops,
                ops / elapsed);
    std::printf("accepts=%llu rejects=%llu\n", (unsigned long long)accepts,
                (unsigned long long)rejects);
}

int main(int argc, char** argv) {
    size_t iters = 500000;
    if (argc > 1) iters = std::stoull(argv[1]);
    run(iters, 4);
    return 0;
}
