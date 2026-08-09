// Standalone tests for fw::native::ghosts.
//
// The registry is the one piece of new client code with no engine dependency,
// so it is the one piece that can be verified without Fallout 4 running. Worth
// doing properly: the whole point of the registry is that peers must not share
// state, and "peers share state" is exactly the bug class that is invisible at
// two players and corrupts everything at four.
//
// Build + run:
//     fw_native\tests\run_tests.bat

#include "../src/native/ghost_registry.h"

#include <cstdio>
#include <string>
#include <vector>

namespace g = fw::native::ghosts;

static int g_failures = 0;
static int g_checks   = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        ++g_checks;                                                          \
        if (!(cond)) {                                                       \
            ++g_failures;                                                    \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);    \
        }                                                                    \
    } while (0)

#define CHECK_EQ(a, b)                                                       \
    do {                                                                     \
        ++g_checks;                                                          \
        if (!((a) == (b))) {                                                 \
            ++g_failures;                                                    \
            std::printf("  FAIL %s:%d: %s == %s\n",                          \
                        __FILE__, __LINE__, #a, #b);                         \
        }                                                                    \
    } while (0)

static void test_ensure_and_count() {
    std::printf("test_ensure_and_count\n");
    g::clear_all();

    CHECK(g::ensure("alpha"));           // created
    CHECK(!g::ensure("alpha"));          // already present
    CHECK(g::ensure("bravo"));
    CHECK_EQ(g::count(), 2u);

    const auto peers = g::peer_ids();
    CHECK_EQ(peers.size(), 2u);

    // A slot with no body yet must not report one.
    CHECK_EQ(g::body_for("alpha"), nullptr);
    CHECK(!g::any_body());
}

static void test_body_is_per_peer() {
    std::printf("test_body_is_per_peer\n");
    g::clear_all();

    int body_a = 0, body_b = 0, body_c = 0;
    g::set_body("alpha",   &body_a, 0xFF000001);
    g::set_body("bravo",   &body_b, 0xFF000002);
    g::set_body("charlie", &body_c, 0xFF000003);

    // The core property: three peers, three distinct bodies, no aliasing.
    CHECK_EQ(g::body_for("alpha"),   &body_a);
    CHECK_EQ(g::body_for("bravo"),   &body_b);
    CHECK_EQ(g::body_for("charlie"), &body_c);
    CHECK_EQ(g::count(), 3u);
    CHECK(g::any_body());

    CHECK_EQ(g::body_for("nobody"), nullptr);
}

static void test_primary_election() {
    std::printf("test_primary_election\n");
    g::clear_all();

    // Registered but body-less: primary falls back to the first registration
    // so legacy accessors have something coherent to name.
    g::ensure("alpha");
    CHECK_EQ(g::primary_peer(), std::string("alpha"));
    CHECK_EQ(g::primary_body(), nullptr);

    // First peer to actually receive a body wins the primary slot.
    int body_b = 0;
    g::ensure("bravo");
    g::set_body("bravo", &body_b, 0xFF000002);
    CHECK_EQ(g::primary_peer(), std::string("bravo"));
    CHECK_EQ(g::primary_body(), &body_b);

    // A later body must NOT steal primary — the legacy single-ghost path has
    // to keep pointing at a stable body, not follow the most recent event.
    int body_c = 0;
    g::set_body("charlie", &body_c, 0xFF000003);
    CHECK_EQ(g::primary_peer(), std::string("bravo"));
    CHECK_EQ(g::primary_body(), &body_b);
}

static void test_take_body_clears_bones_and_repicks() {
    std::printf("test_take_body_clears_bones_and_repicks\n");
    g::clear_all();

    int body_a = 0, body_b = 0;
    int j0 = 0, j1 = 0;

    g::set_body("alpha", &body_a, 0xFF000001);
    CHECK(g::set_bones("alpha", {"Pelvis", "Spine"}, {&j0, &j1}));
    CHECK_EQ(g::bone_ptrs_for("alpha").size(), 2u);
    CHECK_EQ(g::primary_peer(), std::string("alpha"));

    g::set_body("bravo", &body_b, 0xFF000002);

    // Releasing the body must also drop the bone table: those pointers point
    // into the subtree being torn down, and a later pose apply would write
    // through dangling NiAVObject pointers.
    void* taken = g::take_body("alpha");
    CHECK_EQ(taken, &body_a);
    CHECK_EQ(g::body_for("alpha"), nullptr);
    CHECK(g::bone_ptrs_for("alpha").empty());
    CHECK(g::bone_names_for("alpha").empty());

    // Primary must move to a peer that still has a body, not go stale.
    CHECK_EQ(g::primary_peer(), std::string("bravo"));
    CHECK_EQ(g::primary_body(), &body_b);

    // Idempotent.
    CHECK_EQ(g::take_body("alpha"), nullptr);
    CHECK_EQ(g::take_body("nobody"), nullptr);
}

static void test_bone_tables_are_isolated() {
    std::printf("test_bone_tables_are_isolated\n");
    g::clear_all();

    int a0 = 0, a1 = 0, b0 = 0, b1 = 0;
    g::set_bones("alpha", {"Pelvis", "Spine"}, {&a0, &a1});
    g::set_bones("bravo", {"Pelvis", "Spine"}, {&b0, &b1});

    // This is the regression the registry exists to prevent: with one shared
    // g_ghost_bone_ptrs, whichever peer's pose arrived last drove every ghost.
    const auto pa = g::bone_ptrs_for("alpha");
    const auto pb = g::bone_ptrs_for("bravo");
    CHECK_EQ(pa.size(), 2u);
    CHECK_EQ(pb.size(), 2u);
    CHECK_EQ(pa[0], &a0);
    CHECK_EQ(pa[1], &a1);
    CHECK_EQ(pb[0], &b0);
    CHECK_EQ(pb[1], &b1);

    // Same joint names on both — names matching must not imply shared storage.
    CHECK_EQ(g::bone_names_for("alpha")[0], std::string("Pelvis"));
    CHECK_EQ(g::bone_names_for("bravo")[0], std::string("Pelvis"));
    CHECK(pa[0] != pb[0]);
}

static void test_set_bones_rejects_mismatch() {
    std::printf("test_set_bones_rejects_mismatch\n");
    g::clear_all();

    int j0 = 0;
    g::set_bones("alpha", {"Pelvis", "Spine"}, {&j0});   // 2 names, 1 ptr

    // Rejected outright rather than half-applied: a desynced name/pointer pair
    // silently drives the wrong joint, which looks like a physics bug rather
    // than a data bug and is miserable to track down.
    CHECK(g::bone_ptrs_for("alpha").empty());
    CHECK(g::bone_names_for("alpha").empty());

    // A well-formed table still applies afterwards.
    CHECK(g::set_bones("alpha", {"Pelvis"}, {&j0}));
    CHECK_EQ(g::bone_ptrs_for("alpha").size(), 1u);
}

static void test_forget() {
    std::printf("test_forget\n");
    g::clear_all();

    int body_a = 0, body_b = 0;
    g::set_body("alpha", &body_a, 0xFF000001);
    g::set_body("bravo", &body_b, 0xFF000002);
    CHECK_EQ(g::primary_peer(), std::string("alpha"));

    CHECK(g::forget("alpha"));
    CHECK(!g::forget("alpha"));          // idempotent
    CHECK_EQ(g::count(), 1u);
    CHECK_EQ(g::body_for("alpha"), nullptr);

    // Primary re-elects onto the surviving peer, so a peer leaving does not
    // strand the legacy path on a departed ghost.
    CHECK_EQ(g::primary_peer(), std::string("bravo"));
    CHECK_EQ(g::primary_body(), &body_b);

    CHECK(g::forget("bravo"));
    CHECK_EQ(g::count(), 0u);
    CHECK_EQ(g::primary_peer(), std::string());
    CHECK_EQ(g::primary_body(), nullptr);
}

static void test_primary_helpers_before_any_peer() {
    std::printf("test_primary_helpers_before_any_peer\n");
    g::clear_all();

    // Bring-up ordering: the body can be injected before any PEER_JOIN has
    // been processed. set_primary_body must still work and park the body
    // under a placeholder key rather than dropping it.
    int body = 0;
    g::set_primary_body(&body, 0xFF000001);
    CHECK_EQ(g::primary_body(), &body);
    CHECK_EQ(g::count(), 1u);

    CHECK_EQ(g::take_primary_body(), &body);
    CHECK_EQ(g::primary_body(), nullptr);
    CHECK_EQ(g::take_primary_body(), nullptr);   // idempotent
}

static void test_scales_to_ten() {
    std::printf("test_scales_to_ten\n");
    g::clear_all();

    std::vector<int> bodies(10);
    for (int i = 0; i < 10; ++i) {
        char peer[16];
        std::snprintf(peer, sizeof(peer), "peer%02d", i);
        g::set_body(peer, &bodies[static_cast<std::size_t>(i)],
                    0xFF000001u + static_cast<unsigned>(i));
        g::set_bones(peer, {"Pelvis"}, {&bodies[static_cast<std::size_t>(i)]});
    }
    CHECK_EQ(g::count(), 10u);

    // Every peer still resolves to its own body after all the churn.
    for (int i = 0; i < 10; ++i) {
        char peer[16];
        std::snprintf(peer, sizeof(peer), "peer%02d", i);
        CHECK_EQ(g::body_for(peer), &bodies[static_cast<std::size_t>(i)]);
    }
}

int main() {
    std::printf("ghost_registry tests\n\n");

    test_ensure_and_count();
    test_body_is_per_peer();
    test_primary_election();
    test_take_body_clears_bones_and_repicks();
    test_bone_tables_are_isolated();
    test_set_bones_rejects_mismatch();
    test_forget();
    test_primary_helpers_before_any_peer();
    test_scales_to_ten();

    std::printf("\n%d checks, %d failure(s)\n", g_checks, g_failures);
    return g_failures == 0 ? 0 : 1;
}
