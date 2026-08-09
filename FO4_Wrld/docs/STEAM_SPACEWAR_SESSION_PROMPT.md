# Session prompt — Steam Spacewar invites + online multiplayer

Paste **everything below the line** into a new engineering session.

---

You are the sole engineer on **FO4_Wrld** (Fallout 4 multiplayer: `dxgi.dll` + Python UDP campaign server + **FoM.exe** one-click launcher).

## Mission

Make co-op **inviteable over Steam** so two players can sit on different networks and join **without typing LAN IPs**.

Use **Steam Spacewar** (`AppID 480`) as the Steamworks “carrier” for:

- lobbies / party
- Steam invites (“Invite to game” / overlay join)
- NAT traversal via **Steam Networking Sockets** (or Steam Datagram Relay if available under Spacewar)

**Do not** try to make Fallout 4 itself a Steam multiplayer title. FO4 stays classic **1.10.163** + our `dxgi.dll`. Spacewar is only the **Steam session / transport shell**.

## Product UX (non-negotiable)

After this lands, a friend should:

1. Run **FoM.exe** (or “Play with Steam”).
2. Press **Host** or accept an **invite**.
3. Fallout 4 launches and they are in the same campaign session.

No manual `fw_config.ini` server IP. No “what’s your IPv4”. No port-forwarding tutorial for the happy path.

Keep the existing one-click Host/Join LAN path as **fallback** if Steam is offline.

## Locked constraints (do not reopen)

| Item | Value |
|------|--------|
| FO4 build | **1.10.163 only** (`EXPECTED` in `fw_native/src/version.h`) |
| Gameplay client | `dxgi.dll` proxy, protocol **v25+** (bump together if wire changes) |
| Campaign model | Server-authoritative overlay; each peer still has own `.fos` |
| Prefer | Minimal new surface; FoM one-click stays dumb-simple |
| Not in scope | True single shared `.fos`, workshop/PA epics, full dialogue trees |

Existing Steam-ish code (do not reinvent blindly):

- `fw_native/src/steam/steam_id.cpp` — resolves SteamID from real Steam / Goldberg
- FoM launcher: `fw_launcher/src/main.cpp` — Host/Join LAN UX
- Wire: `net/protocol.py` + `fw_native/src/net/protocol.h` + `net/server/main.py`
- Player pack: `tools/player_setup/`

## Architecture (implement this design unless you find a hard blocker)

### Recommended: **Host-listen + Steam P2P/relay for the UDP path**

Today:

```
Peer A FO4+dxgi  ←UDP:31337→  Python server (often on Host)  ←UDP→  Peer B FO4+dxgi
```

Target:

```
                    Steam (Spacewar 480)
                    lobby + invite + networking sockets
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
   FoM / thin steam host   (optional)          FoM join
         │                                         │
         ▼                                         ▼
   FO4 + dxgi  ─── SteamNetworkingSockets ───  FO4 + dxgi
         │              (or UDP over SNS)           │
         └────────── campaign server ──────────────┘
              (still authoritative Python OR later C++ port)
```

**Two acceptable implementations (pick one, document why):**

### Option A — **Steam Networking Sockets as transport under existing protocol** (preferred)

1. Keep **binary frames** identical (`protocol.py` / `protocol.h`).
2. Replace raw `sendto`/`recvfrom` on clients (and optionally server) with:
   - `ISteamNetworkingSockets` connection (host listen + client connect via SteamID / lobby host), **or**
   - Steam datagram / P2P messages carrying the same UDP payload bytes.
3. Host still runs campaign authority (Python server process **or** in-process later).
4. FoM / steam shell:
   - creates **Spacewar** lobby as Host
   - sets lobby data: `fom=1`, protocol version, host SteamID, maybe session token
   - Join via invite callback → connect sockets → write local `fw_config` if still needed → launch FO4

### Option B — **Steam only for matchmaking; still UDP over IP after Steam relays an endpoint**

Use Spacewar lobby to exchange a **public endpoint** (STUN-like or Steam’s own) then fall back to UDP.  
Only if SNS proves too painful on classic FO4 process isolation. Prefer A.

### Why Spacewar (480)

- Free Steamworks test app every Steam user already “owns.”
- Lobbies, invites, overlay, Networking Sockets work for prototypes.
- Caveats to handle honestly:
  - All players must **own Spacewar** (they get it free by running it once / library).
  - Overlay / invite is for **Spacewar**, not FO4 — UX copy must say “invite via FalloutWorld / Spacewar session.”
  - Do **not** ship as a permanent commercial AppID substitute forever; note “dev Spacewar; real AppID later.”

## Concrete work items (order)

### 1. Steamworks bring-up (shell process first)

Do **not** force full SteamAPI init only inside FO4 if that fights the game’s own SteamAPI.

Preferred: a small **FoM Steam host** process (extend `FoM.exe` or `FoM_Steam.exe`) that:

1. Sets env / `steam_appid.txt` → **`480`** next to that exe.
2. `SteamAPI_Init()` against **real** `steam_api64.dll` from Steamworks redistributable (not Goldberg for online invites).
3. Creates lobby (`ISteamMatchmaking`).
4. Host: `SetLobbyData`, `SetLobbyJoinable`, enable invite.
5. Handles `GameLobbyJoinRequested_t` / `LobbyEnter_t` for invite accept.
6. Opens **Networking Sockets** listen/connect between host and joiners.
7. Launches FO4 only after session transport is ready (or launches FO4 and pipes traffic).

Deliverable: two Steam accounts can invite; console log shows lobby ID + peer SteamIDs.

### 2. Tunnel FO4_Wrld traffic

Map existing UDP client/server path onto Steam transport:

| Piece | Today | Target |
|-------|--------|--------|
| Client net | UDP to `server` in `fw_config.ini` | Connect via Steam to host (or local loopback if host embeds server) |
| Server | `net/server/main.py` UDP `:31337` | Bind local + bridge from SNS, **or** host FoM proxies SNS ↔ UDP localhost |

**Simplest first ship that works over internet:**

- Host FoM starts Python server on `127.0.0.1:31337`.
- Host FO4 `fw_config` stays `127.0.0.1:31337`.
- Host FoM runs a **bridge**: SteamNetworkingSockets ↔ UDP localhost (and multi-peer fan-out).
- Join FoM: no public IP; SNS to host; **local** UDP shim to a tiny local proxy **or** change C++ client to speak SNS directly.

Prefer changing **C++ client** (`fw_native/src/net/client.cpp`) to pluggable transport (`UdpTransport` / `SteamTransport`) so Join doesn’t need a second localhost hop long-term.

### 3. FoM UX rewrite

Replace “type IP” primary path:

```
[1] Host session (Steam)
[2] Join / waiting for invite
[3] LAN only (advanced)
```

Host screen shows: “Invite friends from Steam overlay (Shift+Tab) while FoM/Spacewar session is active.”

On invite accept (cold start FoM with `+connect_lobby`):

- Init Steam → enter lobby → connect transport → install dxgi → launch FO4.

### 4. Identity

- Prefer SteamID64 as peer identity (map to `client_id` string for existing FixedClientId if needed: e.g. base36 truncated, or raise limit carefully).
- Reuse `fw_native/src/steam/steam_id.cpp` patterns; init may live in FoM not FO4.
- Server HELLO should not reject two friends because both used `player_A`.

### 5. Packaging

Update `tools/player_setup/Build-PlayerPack.ps1`:

- Ship Steamworks `steam_api64.dll` + `steam_appid.txt` = `480` for FoM steam shell.
- Document: “Install / run Spacewar once if Steam asks.”
- Keep LAN fallback offline.

### 6. Tests / proof

| Gate | Pass criteria |
|------|----------------|
| Unit | Transport mock: encode frame → “SNS send” buffer → decode |
| Two-PC LAN Steam | Invite works without typing IP |
| Two-PC different NAT | Join works via Steam (internet) |
| Fallback | LAN Host/Join still works if SteamAPI_Init fails |
| Regression | Protocol v25+ narrative pytest still green |

## Explicit non-goals for this epic

- Replacing Python server with Rust (unless transport forces it — do not boil ocean).
- Steam auth tickets for anti-cheat.
- Steam Workshop.
- Making FO4 show as “playing Fallout 4 multiplayer” in rich presence beyond best-effort (Spacewar will show Spacewar — honest UI).

## Implementation notes / pitfalls

1. **FO4 already loads Steam** — dual `SteamAPI_Init` in `dxgi.dll` can conflict. Prefer Steam matchmaking in **FoM.exe**; FO4 only needs game traffic.
2. **Goldberg / cold client** breaks real invites — online path requires **real Steam**.
3. **Family Share** still bad for two concurrent FO4s — two legal installs / two machines.
4. **Protocol version** must stay in lockstep client/server; add STEAM transport flag if needed, don’t silently desync.
5. **Firewall**: Steam handles most NAT; still allow FoM through Windows Firewall.
6. Spacewar lobby visibility / app ownership: handle “failed to create lobby” with a clear error (“Open Spacewar once from library”).

## Suggested first milestone (shippable)

**M1 — Steam lobby + invite + localhost bridge**

- FoM Host creates Spacewar lobby, prints invite-ready.
- Friend accepts invite → FoM Join starts → bridge to host.
- Both run FO4 classic with existing protocol; campaign co-op works over internet.
- LAN IP mode remains as advanced.

Do not stop at lobby without traffic.

## Repo paths to touch (expected)

- `fw_launcher/` — FoM steam host/join UX  
- `fw_native/src/net/` — transport abstraction  
- `fw_native/src/steam/` — expand if FO4-side SteamID needed  
- `net/server/` — only if multi-peer fan-out or bind changes  
- `tools/player_setup/` — pack Spacewar redistributables  
- `docs/MP_TEST_RUNBOOK.md` + `HOW_TO_PLAY` — Steam invite steps  

## Definition of done

1. Two humans on two PCs, different networks, **Steam invite only**, both load FO4, see each other move (`pos_*`), F8/F9 works.  
2. One-click FoM still works offline via LAN fallback.  
3. Docs explain Spacewar honestly (why 480, what overlay shows).  
4. No multi-step IP configuration in the happy path.

## Start command for the agent

Implement **M1** top-down: FoM Spacewar lobby + invite → Networking Sockets bridge to existing UDP server → wire Join so FO4 never needs a typed IP. Keep protocol frames byte-compatible. Ship player-pack notes.

---

End of prompt.
