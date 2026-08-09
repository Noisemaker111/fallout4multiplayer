/* POS hot-path microbench — same algorithm as python/bench.py */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_SPEED 2500.0
#define MAX_VSPEED 5000.0
#define MIN_DT_MS 20.0
#define COORD_BOUND 1e7
#define PEER_ID_LEN 16
#define BODY_LEN 36
#define OK 0
#define TOO_FAST 5
#define SPEED 2
#define VERTICAL 4
#define NON_FINITE 10
#define TS_INV 3
#define N_PEERS 4

typedef struct {
    uint8_t peer_id[PEER_ID_LEN];
    float last_x, last_y, last_z;
    uint64_t last_ts;
    uint32_t last_cell;
    double last_at_ms;
    int has;
} Peer;

static void pack_pos(uint8_t *buf, float x, float y, float z, float rx, float ry, float rz,
                     uint64_t ts, uint32_t cell) {
    memcpy(buf + 0, &x, 4);
    memcpy(buf + 4, &y, 4);
    memcpy(buf + 8, &z, 4);
    memcpy(buf + 12, &rx, 4);
    memcpy(buf + 16, &ry, 4);
    memcpy(buf + 20, &rz, 4);
    memcpy(buf + 24, &ts, 8);
    memcpy(buf + 32, &cell, 4);
}

static void unpack_pos(const uint8_t *buf, float *x, float *y, float *z, float *rx, float *ry,
                       float *rz, uint64_t *ts, uint32_t *cell) {
    memcpy(x, buf + 0, 4);
    memcpy(y, buf + 4, 4);
    memcpy(z, buf + 8, 4);
    memcpy(rx, buf + 12, 4);
    memcpy(ry, buf + 16, 4);
    memcpy(rz, buf + 20, 4);
    memcpy(ts, buf + 24, 8);
    memcpy(cell, buf + 32, 4);
}

static uint8_t validate(const Peer *peer, float x, float y, float z, float rx, float ry, float rz,
                        uint64_t ts, uint32_t cell, double now_ms) {
    if (!isfinite(x) || !isfinite(y) || !isfinite(z) || !isfinite(rx) || !isfinite(ry) ||
        !isfinite(rz))
        return NON_FINITE;
    if (fabsf(x) > COORD_BOUND || fabsf(y) > COORD_BOUND || fabsf(z) > COORD_BOUND)
        return NON_FINITE;
    if (!peer->has) return OK;
    double dt_ms = now_ms - peer->last_at_ms;
    if (dt_ms < MIN_DT_MS) return TOO_FAST;
    if (ts < peer->last_ts) return TS_INV;
    if (cell != 0 && peer->last_cell != 0 && cell != peer->last_cell) return OK;
    double dt_s = dt_ms / 1000.0;
    double dx = x - peer->last_x, dy = y - peer->last_y, dz = z - peer->last_z;
    double dist = sqrt(dx * dx + dy * dy + dz * dz);
    if (dist / dt_s > MAX_SPEED) return SPEED;
    if (fabs(dz) / dt_s > MAX_VSPEED) return VERTICAL;
    return OK;
}

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    size_t iters = 500000;
    if (argc > 1) iters = (size_t)strtoull(argv[1], NULL, 10);
    Peer peers[N_PEERS];
    uint8_t bodies[N_PEERS][BODY_LEN];
    memset(peers, 0, sizeof(peers));
    for (int i = 0; i < N_PEERS; i++) {
        char s[16];
        snprintf(s, sizeof(s), "player_%d", i);
        memcpy(peers[i].peer_id, s, strlen(s));
        pack_pos(bodies[i], -76000.f + i * 100.f, 93000.f, 7700.f, 0, 0, 0.1f, 1000000ull + i,
                 0x00024A02u);
    }
    uint8_t out[(PEER_ID_LEN + BODY_LEN) * (N_PEERS - 1)];
    uint64_t accepts = 0, rejects = 0;
    double now = 1000000.0;
    double t0 = now_sec();
    for (size_t k = 0; k < iters; k++) {
        for (int i = 0; i < N_PEERS; i++) {
            float x, y, z, rx, ry, rz;
            uint64_t ts;
            uint32_t cell;
            unpack_pos(bodies[i], &x, &y, &z, &rx, &ry, &rz, &ts, &cell);
            x += 6.f;
            ts += 20;
            pack_pos(bodies[i], x, y, z, rx, ry, rz, ts, cell);
            now += 20.0;
            uint8_t reason = validate(&peers[i], x, y, z, rx, ry, rz, ts, cell, now);
            if (reason != OK) {
                rejects++;
                continue;
            }
            accepts++;
            peers[i].last_x = x;
            peers[i].last_y = y;
            peers[i].last_z = z;
            peers[i].last_ts = ts;
            peers[i].last_cell = cell;
            peers[i].last_at_ms = now;
            peers[i].has = 1;
            size_t o = 0;
            for (int j = 0; j < N_PEERS; j++) {
                if (j == i) continue;
                memcpy(out + o, peers[i].peer_id, PEER_ID_LEN);
                memcpy(out + o + PEER_ID_LEN, bodies[i], BODY_LEN);
                o += PEER_ID_LEN + BODY_LEN;
            }
        }
    }
    double elapsed = now_sec() - t0;
    double ops = (double)(iters * N_PEERS);
    printf("lang=c peers=%d iters=%zu ops=%.0f\n", N_PEERS, iters, ops);
    printf("elapsed_s=%.6f ns_per_op=%.1f ops_per_s=%.0f\n", elapsed, elapsed * 1e9 / ops,
           ops / elapsed);
    printf("accepts=%llu rejects=%llu\n", (unsigned long long)accepts,
           (unsigned long long)rejects);
    return 0;
}
