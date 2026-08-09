// Spacewar lobby lifecycle for FoM.exe.
//
// One lobby == one FalloutWorld co-op session. The lobby owner is the
// campaign host (the machine running net/server/main.py). Everyone else is a
// joiner whose game traffic is tunnelled to the host over Steam P2P.
//
// Lobby data keys (all strings, Steam's own KV store):
//   fom    = "1"          marks this lobby as ours, not some other Spacewar app
//   proto  = "25"         FW_PROTOCOL_VERSION - joiner refuses a mismatch
//                         BEFORE launching FO4, instead of desyncing later
//   host   = "7656119..." host SteamID64 in decimal
//   name   = "<persona>"  host's Steam persona, for the joiner's console
//
// SteamSession is single-threaded: construct it, call pump() in a loop, read
// state between pumps. It never blocks.

#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "steam_api.h"

namespace fom::steam {

enum class SessionRole {
    None,
    Host,
    Join,
};

enum class SessionState {
    Idle,
    Creating,   // CreateLobby in flight
    Joining,    // JoinLobby in flight
    Active,     // in the lobby, transport may run
    Failed,
};

class SteamSession : public ICallbackSink {
public:
    explicit SteamSession(SteamApi& api) : api_(api) {}

    // --- lifecycle ---------------------------------------------------------

    // Asks Steam to create a friends-only lobby. Returns false only if the
    // request could not be submitted at all; success is reported later via
    // state() == Active.
    bool start_host(int max_members = 8);

    // Joins an existing lobby (from an invite callback or a pasted code).
    bool start_join(std::uint64_t lobby_id);

    // Drain Steam callbacks. Call at bridge-loop frequency.
    void pump();

    void leave();

    // --- state -------------------------------------------------------------

    SessionState  state()      const { return state_; }
    SessionRole   role()       const { return role_; }
    std::uint64_t lobby_id()   const { return lobby_; }
    const std::string& error() const { return error_; }

    // Host's SteamID64. For a host this is us; for a joiner it is read from
    // lobby data (falling back to the Steam-reported lobby owner).
    std::uint64_t host_steam_id() const;

    bool is_host() const { return role_ == SessionRole::Host; }

    // Lobby members other than us, refreshed on every pump().
    std::vector<std::uint64_t> peers() const { return peers_; }

    // Protocol version advertised by the lobby ("proto" key). 0 if unknown.
    int lobby_protocol_version() const;

    // Nudge Steam to fetch this lobby's metadata. Lobby data can arrive a
    // beat after LobbyEnter_t, so a joiner asks before reading `host`/`proto`.
    void request_lobby_data_hint();

    // A friend accepted our invite, or clicked "Join Game" in their friends
    // list. Returns the lobby to join, or 0. Consumed by the caller.
    std::uint64_t take_pending_join_request();

    // The command line Steam would use to launch us for this session. We
    // publish it as the "connect" rich-presence key, which is what makes the
    // friends-list "Join Game" button appear next to the host - no invite
    // required. Also what the agent parses when that button is clicked.
    std::string connect_string() const;

    // Open the Steam overlay's friend-invite dialog for this lobby.
    void invite_overlay();

    // Human-readable session code (base36 lobby id) for the paste-a-code
    // fallback when the overlay is not cooperating.
    std::string session_code() const;

    // Called for every SteamNetworkingMessages session request. The bridge
    // installs this so it can decide whether to accept the peer.
    void set_session_request_handler(std::function<void(std::uint64_t)> h) {
        on_session_request_ = std::move(h);
    }

    // --- ICallbackSink -----------------------------------------------------
    void on_lobby_created(const LobbyCreated&) override;
    void on_lobby_enter(const LobbyEnter&) override;
    void on_lobby_chat_update(const LobbyChatUpdate&) override;
    void on_join_requested(const GameLobbyJoinRequested&) override;
    void on_rich_presence_join(const GameRichPresenceJoinRequested&) override;
    void on_session_request(const MessagesSessionRequest&) override;

    // Parse "+connect_lobby <id>" out of a Steam connect string / command
    // line. Returns 0 if there is no lobby in it. Public + static so the
    // agent and argv parsing share one implementation.
    static std::uint64_t parse_connect_string(const char* s);

private:
    void publish_host_lobby_data();
    void publish_rich_presence();
    void refresh_peers();

    SteamApi&     api_;
    SessionState  state_ = SessionState::Idle;
    SessionRole   role_  = SessionRole::None;
    std::uint64_t lobby_ = 0;
    std::uint64_t pending_join_ = 0;
    std::string   error_;
    std::vector<std::uint64_t> peers_;

    std::function<void(std::uint64_t)> on_session_request_;
};

}  // namespace fom::steam
