// Steamworks binding for FoM.exe - dynamic, header-free, SDK-free.
//
// WHY NOT THE SDK: vendoring the Steamworks SDK into this repo would add a
// third-party tree, a license question, and a build dependency for a shell
// process that needs maybe 25 functions. Real `steam_api64.dll` exports the
// *flat* C API (`SteamAPI_ISteamXxx_Method`), so we can bind every call we
// need with GetProcAddress and hand-declared POD structs. This is the same
// technique fw_native/src/steam/steam_id.cpp already uses to read the local
// SteamID, extended to matchmaking + P2P networking.
//
// CALLBACKS: the C++ SDK delivers callbacks through CCallback<> template
// registration, which we cannot do without the headers. Valve ships
// `SteamAPI_ManualDispatch_*` precisely for non-C++ bindings - we use that.
// Consequence: SteamAPI_RunCallbacks() must never be called on this process.
//
// TRANSPORT: we use ISteamNetworkingMessages, not ISteamNetworkingSockets.
// Messages is the connectionless, datagram-preserving, send-to-a-SteamID
// interface - semantically identical to the UDP socket our protocol already
// speaks, with NAT punch + Steam relay fallback handled inside Steam. Sockets
// would additionally require binding SteamNetConnectionStatusChangedCallback_t
// and its large nested SteamNetConnectionInfo_t, for no gain here.
//
// ABI NOTE: Steamworks callback structs are declared under `#pragma pack(8)`
// on x64, which is natural alignment, so the plain struct declarations below
// match the SDK layout byte for byte. Struct-by-value parameters
// (SteamNetworkingIdentity) are declared by value so MSVC applies the same
// x64 calling convention Valve's build did.

#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace fom::steam {

// ---------------------------------------------------------------- constants

constexpr std::uint32_t kSpacewarAppId = 480;

// Callback ids (steam_api_common.h base + offset).
constexpr int kCB_SteamAPICallCompleted   = 703;   // k_iSteamUtilsCallbacks + 3
constexpr int kCB_LobbyEnter              = 504;   // k_iSteamMatchmakingCallbacks + 4
constexpr int kCB_LobbyDataUpdate         = 505;
constexpr int kCB_LobbyChatUpdate         = 506;
constexpr int kCB_LobbyCreated            = 513;
constexpr int kCB_GameLobbyJoinRequested  = 333;   // k_iSteamFriendsCallbacks + 33
constexpr int kCB_RichPresenceJoinRequested = 337; // k_iSteamFriendsCallbacks + 37
constexpr int kCB_MessagesSessionRequest  = 1251;  // k_iSteamNetworkingMessagesCallbacks + 1
constexpr int kCB_MessagesSessionFailed   = 1252;
constexpr int kCB_RelayNetworkStatus      = 1281;  // k_iSteamNetworkingUtilsCallbacks + 1

// ELobbyType
constexpr int kLobbyTypePrivate     = 0;
constexpr int kLobbyTypeFriendsOnly = 1;
constexpr int kLobbyTypePublic      = 2;
constexpr int kLobbyTypeInvisible   = 3;

// EResult
constexpr int kEResultOK = 1;

// nSendFlags for SendMessageToUser. Our protocol carries its own
// sequence/ACK/retransmit layer (fw_native/src/net/reliable.*), so the tunnel
// stays a dumb unreliable datagram pipe and never inspects frame bytes.
constexpr int kSendUnreliable = 0;
constexpr int kSendNoNagle    = 1;

// ESteamNetworkingIdentityType
constexpr int kIdentityTypeSteamID = 16;

// ESteamNetworkingAvailability
constexpr int kAvailabilityCurrent = 100;

// ------------------------------------------------------------- POD structs

#pragma pack(push, 8)

struct CallbackMsg {
    std::int32_t   steam_user;
    std::int32_t   callback;
    std::uint8_t*  param;
    std::int32_t   param_size;
};

struct APICallCompleted {
    std::uint64_t  async_call;
    std::int32_t   callback;
    std::uint32_t  param_size;
};

struct LobbyCreated {
    std::int32_t   result;        // EResult
    std::uint64_t  lobby;
};

struct LobbyEnter {
    std::uint64_t  lobby;
    std::uint32_t  chat_permissions;
    bool           locked;
    std::uint32_t  chat_room_enter_response;
};

struct LobbyChatUpdate {
    std::uint64_t  lobby;
    std::uint64_t  user_changed;
    std::uint64_t  making_change;
    std::uint32_t  member_state_change;
};

struct GameLobbyJoinRequested {
    std::uint64_t  lobby;
    std::uint64_t  friend_id;
};

// Fired when a friend uses the friends-list "Join Game" button, which Steam
// shows for any user whose rich presence has a "connect" key. m_rgchConnect
// carries that string verbatim - we put "+connect_lobby <id>" in it, matching
// the command line Steam would have used on a cold start.
constexpr int kMaxRichPresenceValueLength = 256;
struct GameRichPresenceJoinRequested {
    std::uint64_t  friend_id;
    char           connect[kMaxRichPresenceValueLength];
};

// SteamNetworkingIdentity: 8-byte header + 128-byte union = 136 bytes.
// The union's first member is m_steamID64, so a SteamID identity keeps its
// 64-bit id at offset 8.
struct NetIdentity {
    std::int32_t   type;
    std::int32_t   size;
    std::uint8_t   data[128];

    std::uint64_t steam_id() const {
        std::uint64_t v = 0;
        std::memcpy(&v, data, sizeof(v));
        return v;
    }

    static NetIdentity from_steam_id(std::uint64_t id) {
        NetIdentity n{};
        n.type = kIdentityTypeSteamID;
        n.size = static_cast<std::int32_t>(sizeof(std::uint64_t));
        std::memcpy(n.data, &id, sizeof(id));
        return n;
    }
};
static_assert(sizeof(NetIdentity) == 136, "SteamNetworkingIdentity layout");

struct MessagesSessionRequest {
    NetIdentity    identity;
};

struct MessagesSessionFailed {
    // Real layout is SteamNetConnectionInfo_t, which we deliberately do not
    // bind. We only ever read the callback id, never the body.
    std::uint8_t   opaque[1];
};

// SteamNetworkingMessage_t. We only touch fields at offsets <= 192, which
// have been stable across every SDK that has ISteamNetworkingMessages, so
// tail additions (m_idxLane in 1.54+) cannot shift anything we read.
struct NetMessage {
    void*          data;                 // 0
    std::int32_t   size;                 // 8
    std::uint32_t  conn;                 // 12
    NetIdentity    peer;                 // 16 .. 151
    std::int64_t   conn_user_data;       // 152
    std::int64_t   usec_time_received;   // 160
    std::int64_t   message_number;       // 168
    void*          pfn_free_data;        // 176
    void*          pfn_release;          // 184
    std::int32_t   channel;              // 192
    std::int32_t   flags;                // 196
    std::int64_t   user_data;            // 200
};
static_assert(offsetof(NetMessage, peer) == 16, "NetMessage.peer offset");
static_assert(offsetof(NetMessage, channel) == 192, "NetMessage.channel offset");

#pragma pack(pop)

// ------------------------------------------------------------------- loader

// Where a usable steam_api64.dll came from, for honest logging.
enum class DllSource {
    None,
    NextToExe,       // shipped in the player pack (intended)
    EnvOverride,     // FOM_STEAM_API_DLL
    Discovered,      // scavenged from an installed Steam game (dev fallback)
};

struct InitResult {
    bool         ok = false;
    std::wstring dll_path;
    DllSource    source = DllSource::None;
    std::string  error;        // human-readable, actionable
};

// Callback sink. One virtual per callback we actually consume; the pump
// ignores everything else Steam hands us.
class ICallbackSink {
public:
    virtual ~ICallbackSink() = default;
    virtual void on_lobby_created(const LobbyCreated&) {}
    virtual void on_lobby_enter(const LobbyEnter&) {}
    virtual void on_lobby_chat_update(const LobbyChatUpdate&) {}
    virtual void on_join_requested(const GameLobbyJoinRequested&) {}
    virtual void on_rich_presence_join(const GameRichPresenceJoinRequested&) {}
    virtual void on_session_request(const MessagesSessionRequest&) {}
};

// Process-wide Steamworks facade. Construct once, call init(), then pump()
// on a single thread for the lifetime of the session.
class SteamApi {
public:
    SteamApi() = default;
    ~SteamApi();

    SteamApi(const SteamApi&) = delete;
    SteamApi& operator=(const SteamApi&) = delete;

    // Loads steam_api64.dll, forces AppID 480, runs SteamAPI_Init, resolves
    // interfaces, switches to manual callback dispatch, and kicks off relay
    // network access. `exe_dir` is where we look for the shipped DLL and
    // where steam_appid.txt is written.
    InitResult init(const std::wstring& exe_dir);

    bool ready() const { return ready_; }

    // Drain Steam's callback queue into `sink`. Call every frame of the
    // bridge loop. Cheap when idle.
    void pump(ICallbackSink* sink);

    void shutdown();

    // --- identity
    std::uint64_t local_steam_id() const { return local_steam_id_; }
    std::string   persona_name(std::uint64_t steam_id) const;
    bool          owns_spacewar() const;

    // --- matchmaking
    std::uint64_t create_lobby(int lobby_type, int max_members);  // returns API call handle
    std::uint64_t join_lobby(std::uint64_t lobby);                // returns API call handle
    void          leave_lobby(std::uint64_t lobby);
    bool          set_lobby_data(std::uint64_t lobby, const char* k, const char* v);
    std::string   get_lobby_data(std::uint64_t lobby, const char* k) const;
    bool          set_lobby_joinable(std::uint64_t lobby, bool joinable);
    bool          set_lobby_type(std::uint64_t lobby, int lobby_type);
    int           lobby_member_count(std::uint64_t lobby) const;
    std::uint64_t lobby_member(std::uint64_t lobby, int index) const;
    std::uint64_t lobby_owner(std::uint64_t lobby) const;
    bool          request_lobby_data(std::uint64_t lobby);

    // --- overlay
    void          open_invite_overlay(std::uint64_t lobby);

    // --- rich presence
    // Setting the "connect" key is what puts a "Join Game" button next to us
    // in every friend's list, with no invite needed.
    bool          set_rich_presence(const char* key, const char* value);
    void          clear_rich_presence();
    // Reads back what Steam actually stored. Works on yourself, which is how
    // the diagnostic proves the "Join Game" button will show up.
    std::string   get_rich_presence(std::uint64_t steam_id,
                                    const char* key) const;

    // --- p2p transport (ISteamNetworkingMessages)
    bool          send_to_user(std::uint64_t steam_id, const void* data,
                               std::uint32_t len, int channel);
    // Fills `out` with up to `max` received messages on `channel`. Caller
    // MUST call release_message on each.
    int           receive_on_channel(int channel, NetMessage** out, int max);
    void          release_message(NetMessage* m);
    bool          accept_session(std::uint64_t steam_id);
    void          close_session(std::uint64_t steam_id);

    // Relay network readiness (k_ESteamNetworkingAvailability_Current == 100).
    int           relay_status() const;

private:
    void* proc(const char* name) const;
    void* resolve_interface(const char* flat_accessor_prefix,
                            const char* version_string,
                            int version_lo, int version_hi,
                            bool steamapi_suffix) const;

    void* dll_ = nullptr;              // HMODULE
    bool  owns_dll_ = false;
    bool  ready_ = false;
    std::int32_t pipe_ = 0;

    void* i_user_ = nullptr;
    void* i_friends_ = nullptr;
    void* i_matchmaking_ = nullptr;
    void* i_messages_ = nullptr;
    void* i_utils_net_ = nullptr;
    void* i_apps_ = nullptr;

    std::uint64_t local_steam_id_ = 0;
};

// Best-effort search for a steam_api64.dll that is new enough to have
// ISteamNetworkingMessages + manual dispatch. Exposed for the packaging
// helper and for diagnostics.
std::vector<std::wstring> find_candidate_steam_dlls(const std::wstring& exe_dir);

}  // namespace fom::steam
