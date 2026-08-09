# Steam invites over Spacewar (AppID 480)

**Status:** M1 shipped — lobby + invite + P2P tunnel, protocol v25 unchanged.

Two friends on two different home networks play co-op with a Steam invite and
nothing else. No LAN IP, no `fw_config.ini` editing, no port forwarding, no
"what's your IPv4", **and nothing to start or coordinate first**.

The whole player-facing flow:

| Host | Friend |
|------|--------|
| Run FoM, press **1**. The Steam friend picker opens by itself. | Click **Accept**. Fallout 4 opens. You are in their world. |

Or, with no invite at all: the friend right-clicks the host in their Steam
friends list and picks **Join Game**.

---

## 1. What actually happens

```
                 Steam client (AppID 480 "Spacewar")
                 lobby + invite + ISteamNetworkingMessages
                          │                    │
     ┌────────────────────┘                    └────────────────────┐
     │  HOST machine                                JOIN machine    │
     ▼                                                              ▼
  FoM.exe  ── SteamNetworkingMessages (NAT punch / SDR relay) ── FoM.exe
     │                                                              │
     │ one loopback UDP socket per remote peer                      │ binds
     ▼                                                              ▼ 127.0.0.1:31337
  net/server/main.py  ◄── UDP 127.0.0.1:31337 ──  Fallout4.exe + dxgi.dll
     ▲                                                (thinks it is talking
     │ UDP 127.0.0.1:31337                             to a local server)
  Fallout4.exe + dxgi.dll  (host's own game)
```

The important property: **Fallout 4 and the campaign server are unchanged.**
`dxgi.dll` still opens a plain UDP socket to whatever `fw_config.ini` says,
and that value is now always `127.0.0.1:31337` on both sides. FoM is what
moves those datagrams across the internet.

Frames are forwarded **opaquely**. The bridge never parses a header, so a
protocol bump needs no change in `fw_launcher`.

---

## 2. Decisions, and why

### 2.1 Option A (transport under the existing protocol), not Option B

The session prompt offered two shapes: put Steam *under* the existing wire
protocol, or use Steam only for matchmaking and then fall back to UDP over a
discovered public endpoint.

We took **Option A**. Option B still leaves you at the mercy of NAT: two
symmetric-NAT home routers will not hole-punch, and "exchange an endpoint"
does not fix that — you would end up writing a relay anyway. Steam already
runs one (SDR). Option A gets NAT traversal *and* relay fallback for free,
and the endpoint the game targets becomes a constant, which is what actually
kills the "type an IP" step.

### 2.2 The tunnel terminates in FoM, not inside `dxgi.dll`

The prompt flagged this and it is the right call: **Fallout 4 already runs
its own SteamAPI session** under its own AppID. Calling `SteamAPI_Init` a
second time inside our injected DLL, under a *different* AppID, is a fight
with no upside. It would also mean the Steam overlay, the invite handler and
the game's own Steam integration all live in one process and can deadlock
each other.

So FoM owns Steam. The game owns UDP. The two meet on loopback.

The cost is one extra localhost hop on the joining machine — measured in
microseconds, invisible next to a WAN round trip.

The seam to remove that hop later already exists:
`fw_native/src/net/transport.h` defines `ITransport`, `client.cpp` no longer
touches a socket directly, and `make_transport()` is the single place a
`SteamTransport` would be selected. That was done as part of this work so the
future change is a new file plus one line, not a client rewrite.

### 2.3 `ISteamNetworkingMessages`, not `ISteamNetworkingSockets`

Messages is the connectionless, send-to-a-SteamID, datagram-preserving
interface. That is *exactly* the shape of the socket it replaces, which is
why the bridge is as small as it is.

Sockets would have required binding `SteamNetConnectionStatusChangedCallback_t`
and its large nested `SteamNetConnectionInfo_t` by hand (see 2.4) purely to
call `AcceptConnection`, for no behavioural gain — we do not want an ordered
reliable stream, we already have our own reliability layer in
`fw_native/src/net/reliable.*`.

Datagrams are sent `Unreliable | NoNagle`. Frames above the Steam MTU are
fragmented and delivered all-or-nothing, which is the same failure mode UDP
already has and which `ReliableChannel` already handles.

### 2.4 No vendored Steamworks SDK

`steam_api64.dll` exports the **flat C API** (`SteamAPI_ISteamXxx_Method`),
so everything FoM needs is reachable by `GetProcAddress`. This repo already
used that technique in `fw_native/src/steam/steam_id.cpp`; `fw_launcher/src/steam/steam_api.cpp`
extends it to matchmaking and networking.

Callbacks are the one thing the flat API cannot do on its own — the C++ SDK
delivers them through `CCallback<>` template registration. Valve ships
`SteamAPI_ManualDispatch_*` for exactly this case (non-C++ bindings), and
that is what FoM uses.

Consequence, and it is a hard rule: **`SteamAPI_RunCallbacks()` must never be
called in the FoM process.** Manual dispatch and automatic dispatch are
mutually exclusive.

Callback struct layouts are hand-declared in `steam_api.h`. This is safe
because Steamworks callback structs are declared under `#pragma pack(8)` on
x64, which is natural alignment, so a plain struct declaration matches the
SDK byte for byte. The declarations are `static_assert`ed on size/offset
where it matters.

### 2.5 Identity from SteamID64

The server rejects duplicate `client_id`s (`net/server/state.py`
`accept_peer`), so two friends who both shipped with `player_A` could never
both connect. FoM now derives `client_id` from the local SteamID64 as
`"s" + base36`, e.g. `76561198090749127` → `skxuogunoclj`.

`MAX_CLIENT_ID_LEN` is 15 and base36 of any `uint64` is at most 13
characters, so the prefixed form fits with room to spare. That headroom is
pinned by `net/tests/test_protocol_version_lockstep.py` so a future identity
change cannot silently start getting peers rejected.

`HELLO` already carried `steam_id` (B6.6w5), and `dxgi.dll` resolves it from
Fallout 4's own Steam session, so the two agree without extra plumbing.

### 2.6 The resident agent, and why it is not optional

This is the piece that decides whether the feature is co-op or homework.

**Steam only delivers "you were invited" / "Join Game" to a process that is
already running as that AppID.** If nothing is running, Steam launches
whatever executable is registered for the AppID — and for 480 that is Valve's
Spacewar, not us. We cannot change that; Valve owns the app config.

The naive consequence is a flow where the joiner has to start FoM, pick a
"wait for invite" option, and *then* tell their friend to invite them. That
is not what accepting an invite feels like in any real game, and it is not
something to hand the player as a caveat to work around.

So FoM keeps a resident agent (`FoM.exe --agent`): windowless, parked in
Steam's callback queue, doing nothing measurable. When an invite arrives it
runs the whole join by itself — enter the lobby, check the protocol version,
stand up the tunnel, write `fw_config.ini`, launch Fallout 4 — shows its
window so the player can see what happened, and returns to standby when the
game exits.

From the player's side: click Accept, the game opens. That is the target.

Supporting decisions:

- **Autostart.** One `HKCU\...\Run` entry named `FalloutWorld (FoM)`, written
  after the first successful Steam session, announced in the console rather
  than done silently, and removable from the menu (`[5]`) or
  `FoM.exe --quit-agent`. An agent that only exists until the next reboot
  would fail exactly when the player is not thinking about it.
- **Single instance.** Two processes both claiming AppID 480 is a coin flip
  over which one Steam hands the invite to. A named mutex makes the agent the
  only Steam session; double-clicking `FoM.exe` hands its command to the agent
  over a named pipe instead of racing it.
- **The pipe server runs on its own thread and can refuse.** The agent spends
  most of a session blocked — on a menu prompt, or inside the tunnel loop — so
  a polled pipe would stop answering precisely when someone pokes it. It
  answers `Busy` rather than queueing a command behind a session that may last
  hours. All client-side pipe I/O is overlapped with an explicit timeout:
  `CallNamedPipe` looks right but its timeout covers only *waiting for a free
  instance*, and the reply read after that blocks forever, so a wedged agent
  would hang every `FoM.exe` the player double-clicks.

### 2.7 Rich presence `connect`

Setting the `connect` rich-presence key is what makes Steam show a **Join
Game** button next to the host in every friend's list — no invite needed at
all. We publish `+connect_lobby <id>`, matching the command line Steam would
have used on a cold start, so the agent parses one format either way.

`GameRichPresenceJoinRequested_t` (337) and `GameLobbyJoinRequested_t` (333)
both land in the same place. `--steam-check` reads the key back from Steam to
prove the button will appear.

The host also gets the invite picker opened for them automatically the moment
the lobby is live, because that is the moment they want it.

### 2.8 One loopback socket per peer

Not an optimisation — a correctness requirement. The Python server keys
sessions on the source `(ip, port)` tuple. Funnelling every remote peer
through a single loopback socket would collapse them into one session and
break multi-peer fan-out. The host bridge therefore allocates a dedicated
`127.0.0.1:<ephemeral>` socket per Steam peer, and the server sees N distinct
peers exactly as it does on a LAN.

`fw_launcher/tests/bridge_test.cpp` asserts this directly.

---

## 3. Honest caveats

These are real. Do not paper over them in UI copy.

| Thing | Reality |
|-------|---------|
| **The overlay says "Spacewar"** | Because it is. Spacewar is Valve's free public test app; every Steam account can use it. Your Steam status reads *Spacewar* while a FoM session is up, not *Fallout 4*. There is no way around this short of owning a real AppID. |
| **Ownership** | Players must "own" Spacewar. It is free and automatic for most accounts, but if `CreateLobby` fails, the fix is: Steam → Library → search *Spacewar* → install/run once. FoM prints exactly that message. |
| **A background helper has to exist** | Steam cannot launch us for AppID 480 (§2.6), so the "click Accept and the game opens" flow is carried by a resident agent that starts with Windows. It is one startup entry and an idle process; it is not optional if invites are to work while FoM is closed. `[5]` in the menu turns it off, and `--steam-check` reports its state. |
| **First run still has to happen** | The very first time, a player must run FoM once so it can find Fallout 4, install the client files and register the helper. After that, invites work cold. |
| **FoM.exe is held open** | While the agent runs it holds `FoM.exe`, so overwriting the pack in place fails. `FoM.exe --quit-agent` first. |
| **Session code fallback** | The host screen also prints a short base36 **session code**. Pasting it into the Join screen is equivalent to accepting an invite and needs no overlay. Still no IP. |
| **Not a permanent AppID** | 480 is a *development* carrier. A shipping product needs its own AppID. Nothing in the design assumes 480 beyond one constant (`kSpacewarAppId`). |
| **Family Sharing** | Unchanged and still bad: two concurrent Fallout 4 instances need two legal installs on two machines. |
| **Firewall** | Steam handles NAT, but Windows Firewall must still let `FoM.exe` through. Allow it when prompted. |
| **Anti-cheat / auth tickets** | Out of scope. Lobby membership is the only gate: the host bridge refuses P2P sessions from SteamIDs that are not in the lobby. |

---

## 4. Files

| Path | Role |
|------|------|
| `fw_launcher/src/agent.{h,cpp}` | Resident agent plumbing: single instance, autostart, pipe server, remembered FO4 path |
| `fw_launcher/src/steam/steam_api.{h,cpp}` | Flat-API binding, manual dispatch pump, interface resolution |
| `fw_launcher/src/steam/steam_session.{h,cpp}` | Lobby lifecycle, invite handling, lobby data, peer roster |
| `fw_launcher/src/net/peer_transport.h` | Abstract peer datagram transport (what makes the bridge testable) |
| `fw_launcher/src/net/steam_peer_transport.{h,cpp}` | `IPeerTransport` over `ISteamNetworkingMessages` |
| `fw_launcher/src/net/steam_bridge.{h,cpp}` | The tunnel: peer datagrams ⇄ loopback UDP |
| `fw_launcher/tests/bridge_test.cpp` | End-to-end tunnel test with Steam replaced by loopback |
| `fw_native/src/net/transport.{h,cpp}` | `ITransport` seam in the game client (UDP today) |
| `fw_native/src/net/protocol_version.h` | Single source of truth for the wire version |
| `tools/player_setup/Get-SteamworksRuntime.ps1` | Stages a modern `steam_api64.dll` for the pack |

### Lobby data keys

| Key | Value |
|-----|-------|
| `fom` | `"1"` — marks the lobby as ours, not another Spacewar app's |
| `proto` | `"25"` — `FW_PROTOCOL_VERSION`; a joiner refuses a mismatch **before** launching FO4 |
| `host` | host SteamID64, decimal |
| `name` | host persona name, for the joiner's console |

---

## 5. The `steam_api64.dll` requirement

FoM needs SDK **1.47+**:

- `ISteamNetworkingMessages` — SDK 1.46
- `SteamAPI_ManualDispatch_*` — SDK 1.47

Fallout 4 ships a 2015-vintage `steam_api64.dll` with neither, so FoM does
**not** reuse the game's copy. Search order:

1. `steam_api64.dll` next to `FoM.exe` ← what the player pack ships
2. `payload\steam_api64.dll`
3. `%FOM_STEAM_API_DLL%`
4. A depth-limited scan of installed Steam games (developer fallback; FoM
   logs loudly when it uses this)

Every candidate is rejected unless it exports the full set FoM calls, so a
too-old DLL fails fast with a clear message instead of crashing.

Stage one for the pack:

```powershell
# preferred - official SDK download
powershell -File tools\player_setup\Get-SteamworksRuntime.ps1 -SdkPath C:\steamworks_sdk

# local testing only
powershell -File tools\player_setup\Get-SteamworksRuntime.ps1 -FromInstalledGame
```

---

## 6. Command line

| Flag | Effect |
|------|--------|
| *(none)* | Menu |
| `--host` / `--join` | Skip the menu |
| `+connect_lobby <id>` | Join that lobby directly (what Steam passes on a cold start) |
| `--lan`, `--side A\|B` | LAN path, unchanged |
| `--agent` | Resident invite helper. Windowless until it has something to do |
| `--quit-agent` | Retire the resident helper (needed before overwriting `FoM.exe`) |
| `--no-agent` | Run interactively without registering autostart |
| `--steam-check` | Diagnostic, below |

## 7. Diagnostics

```powershell
FoM.exe --steam-check
```

Initialises Steam under AppID 480, prints identity / derived `peer_id` /
Spacewar ownership / relay status, creates a throwaway lobby, verifies both
the lobby data **and** the `connect` rich-presence key round-trip, reports
whether cold invites are actually wired up, and exits. Gate 1 whenever an
invite misbehaves.

Expected on a healthy machine:

```
  app id:      480 (Spacewar)
  steam id:    765611980xxxxxxxx
  peer_id:     skxuogunoclj  (what the campaign server will see)
  owns 480:    yes
  protocol:    v25
  relay:       100 (ready - NAT traversal available)
  lobby proto: 25
  connect:     +connect_lobby 1097752410xxxxxxx -> friends see a Join Game button
  invites while FoM is closed: ON (background helper starts with Windows)
  background helper right now:  running
  RESULT: PASS - Steam lobby + invite path is live
```

If `invites while FoM is closed` says OFF, a friend's invite will go nowhere
unless FoM happens to be open. Run FoM and press `[5]`.

Note: run `--steam-check` with the agent stopped (`--quit-agent`) for a clean
read — two Steam sessions on one machine make invite routing ambiguous, and
the check warns when it detects that.
