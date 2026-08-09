#include "steam_session.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "../fom_util.h"
#include "../../../fw_native/src/net/protocol_version.h"

namespace fom::steam {

namespace {
constexpr const char* kKeyFom   = "fom";
constexpr const char* kKeyProto = "proto";
constexpr const char* kKeyHost  = "host";
constexpr const char* kKeyName  = "name";
}  // namespace

bool SteamSession::start_host(int max_members) {
    if (!api_.ready()) {
        error_ = "Steam not initialised";
        state_ = SessionState::Failed;
        return false;
    }
    // CreateLobby is async; LobbyCreated_t + LobbyEnter_t come back through
    // the manual-dispatch pump.
    const std::uint64_t call = api_.create_lobby(kLobbyTypeFriendsOnly,
                                                 max_members);
    if (call == 0) {
        error_ = "CreateLobby was refused. Open Spacewar once from your Steam "
                 "library (Library -> search 'Spacewar') so your account owns "
                 "it, then retry.";
        state_ = SessionState::Failed;
        return false;
    }
    role_  = SessionRole::Host;
    state_ = SessionState::Creating;
    return true;
}

bool SteamSession::start_join(std::uint64_t lobby_id) {
    if (!api_.ready()) {
        error_ = "Steam not initialised";
        state_ = SessionState::Failed;
        return false;
    }
    if (lobby_id == 0) {
        error_ = "invalid session code";
        state_ = SessionState::Failed;
        return false;
    }
    if (api_.join_lobby(lobby_id) == 0) {
        error_ = "JoinLobby was refused by Steam";
        state_ = SessionState::Failed;
        return false;
    }
    role_  = SessionRole::Join;
    lobby_ = lobby_id;
    state_ = SessionState::Joining;
    return true;
}

void SteamSession::pump() {
    api_.pump(this);
    if (state_ == SessionState::Active) refresh_peers();
}

void SteamSession::leave() {
    // Drop the "Join Game" button before the lobby goes away, so a friend
    // cannot click through to a session that no longer exists.
    api_.clear_rich_presence();
    if (lobby_ != 0) api_.leave_lobby(lobby_);
    lobby_ = 0;
    peers_.clear();
    state_ = SessionState::Idle;
    role_  = SessionRole::None;
}

// ------------------------------------------------------------------ callbacks

void SteamSession::on_lobby_created(const LobbyCreated& cb) {
    if (cb.result != kEResultOK) {
        error_ = "Steam could not create a Spacewar lobby (EResult " +
                 std::to_string(cb.result) +
                 "). Usually this means your account has never run Spacewar: "
                 "open Steam -> Library -> search 'Spacewar' -> install/run "
                 "once, then retry.";
        state_ = SessionState::Failed;
        return;
    }
    lobby_ = cb.lobby;
    publish_host_lobby_data();
    // LobbyEnter_t follows and flips us to Active.
}

void SteamSession::on_lobby_enter(const LobbyEnter& cb) {
    lobby_ = cb.lobby;
    // k_EChatRoomEnterResponseSuccess == 1
    if (cb.chat_room_enter_response != 1) {
        error_ = "could not enter the Steam lobby (response " +
                 std::to_string(cb.chat_room_enter_response) +
                 "). The host may have closed the session.";
        state_ = SessionState::Failed;
        return;
    }
    if (role_ == SessionRole::Host) publish_host_lobby_data();
    state_ = SessionState::Active;
    refresh_peers();
}

void SteamSession::on_lobby_chat_update(const LobbyChatUpdate& cb) {
    if (cb.lobby == lobby_) refresh_peers();
}

void SteamSession::on_join_requested(const GameLobbyJoinRequested& cb) {
    // Friend accepted our invite. Queue it; the caller decides what to do.
    pending_join_ = cb.lobby;
}

void SteamSession::on_rich_presence_join(
    const GameRichPresenceJoinRequested& cb) {
    // Friend used the friends-list "Join Game" button. Same destination,
    // different doorway - the lobby id rides in the connect string.
    char buf[kMaxRichPresenceValueLength + 1];
    std::memcpy(buf, cb.connect, kMaxRichPresenceValueLength);
    buf[kMaxRichPresenceValueLength] = '\0';
    const std::uint64_t lobby = parse_connect_string(buf);
    if (lobby != 0) pending_join_ = lobby;
}

std::uint64_t SteamSession::parse_connect_string(const char* s) {
    if (!s) return 0;
    const char* p = std::strstr(s, "+connect_lobby");
    if (!p) return 0;
    p += std::strlen("+connect_lobby");
    while (*p == ' ' || *p == '\t' || *p == '"') ++p;
    const std::uint64_t v = std::strtoull(p, nullptr, 10);
    return v;
}

std::string SteamSession::connect_string() const {
    if (lobby_ == 0) return {};
    char buf[64];
    std::snprintf(buf, sizeof(buf), "+connect_lobby %llu",
                  static_cast<unsigned long long>(lobby_));
    return buf;
}

void SteamSession::on_session_request(const MessagesSessionRequest& cb) {
    const std::uint64_t sid = cb.identity.steam_id();
    if (cb.identity.type != kIdentityTypeSteamID || sid == 0) return;
    if (on_session_request_) on_session_request_(sid);
}

// --------------------------------------------------------------------- state

std::uint64_t SteamSession::take_pending_join_request() {
    const std::uint64_t v = pending_join_;
    pending_join_ = 0;
    return v;
}

std::uint64_t SteamSession::host_steam_id() const {
    if (lobby_ == 0) return 0;
    if (role_ == SessionRole::Host) return api_.local_steam_id();

    const std::string s = api_.get_lobby_data(lobby_, kKeyHost);
    if (!s.empty()) {
        const std::uint64_t v = std::strtoull(s.c_str(), nullptr, 10);
        if (v != 0) return v;
    }
    // Fall back to whoever Steam says owns the lobby.
    return api_.lobby_owner(lobby_);
}

int SteamSession::lobby_protocol_version() const {
    if (lobby_ == 0) return 0;
    const std::string s = api_.get_lobby_data(lobby_, kKeyProto);
    if (s.empty()) return 0;
    return std::atoi(s.c_str());
}

void SteamSession::request_lobby_data_hint() {
    if (lobby_ != 0) api_.request_lobby_data(lobby_);
}

std::string SteamSession::session_code() const {
    if (lobby_ == 0) return {};
    return fom::to_base36(lobby_);
}

void SteamSession::invite_overlay() {
    if (lobby_ != 0) api_.open_invite_overlay(lobby_);
}

// ------------------------------------------------------------------ internals

void SteamSession::publish_host_lobby_data() {
    if (lobby_ == 0) return;
    char proto[16];
    std::snprintf(proto, sizeof(proto), "%d", FW_PROTOCOL_VERSION);
    char host[32];
    std::snprintf(host, sizeof(host), "%llu",
                  static_cast<unsigned long long>(api_.local_steam_id()));

    api_.set_lobby_data(lobby_, kKeyFom, "1");
    api_.set_lobby_data(lobby_, kKeyProto, proto);
    api_.set_lobby_data(lobby_, kKeyHost, host);

    const std::string name = api_.persona_name(api_.local_steam_id());
    if (!name.empty()) api_.set_lobby_data(lobby_, kKeyName, name.c_str());

    api_.set_lobby_joinable(lobby_, true);
    publish_rich_presence();
}

void SteamSession::publish_rich_presence() {
    if (lobby_ == 0) return;
    // "connect" is the load-bearing key: Steam shows a "Join Game" button on
    // us in every friend's list purely because it is set, so a friend can
    // join without the host inviting anyone.
    const std::string connect = connect_string();
    api_.set_rich_presence("connect", connect.c_str());
    api_.set_rich_presence("status", "FalloutWorld co-op");
}

void SteamSession::refresh_peers() {
    peers_.clear();
    if (lobby_ == 0) return;
    const std::uint64_t me = api_.local_steam_id();
    const int n = api_.lobby_member_count(lobby_);
    peers_.reserve(static_cast<std::size_t>(n > 0 ? n : 0));
    for (int i = 0; i < n; ++i) {
        const std::uint64_t sid = api_.lobby_member(lobby_, i);
        if (sid != 0 && sid != me) peers_.push_back(sid);
    }
}

}  // namespace fom::steam
