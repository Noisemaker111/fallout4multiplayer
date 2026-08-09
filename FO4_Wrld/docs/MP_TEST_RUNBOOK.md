# Multiplayer test runbook (classic 1.10.163 + FW_MINIMAL)

**Protocol:** v25  
**Target exe:** Fallout 4 `1.10.163.0` only  
**Native build:** `fw_native` **minimal** (`build.bat --minimal` / `deploy.bat --minimal`)

---

## Friend / two-PC play (preferred — one click, any network)

Ship: `tools/player_setup/dist/FoM_PlayerPack.zip`

**Steam invite path — no IP, works across different home networks.**

Once, on each PC: unzip and double-click **`FoM.exe`** so it can find
Fallout 4, install the client files and register the background invite
helper. After that:

| Who | Steps |
|-----|--------|
| **Host** | Double-click **`FoM.exe`** → press **1**. The Steam friend picker opens by itself. |
| **Friend** | Click **Accept**. Fallout 4 opens. Done. |

The friend does **not** need FoM open first, does not need to press anything
in FoM, and never types an IP. A friend can also join with no invite at all:
right-click the host in the Steam friends list → **Join Game**.

That works because FoM leaves a small background helper running (Steam only
delivers invites to a process already running as AppID 480 — see
`docs/STEAM_SPACEWAR.md` §2.6). `[5]` in the menu shows and toggles it;
`FoM.exe --steam-check` reports whether it is armed.

If the overlay misbehaves, the host screen also prints a short **session
code**; the joiner pastes that into the Join screen. Same result, still no IP.

Host needs Python 3 on PATH once (or pack `runtime\python.exe`). Join needs
nothing else. **Keep both FoM windows open while playing — they are the
tunnel.** They close themselves a few seconds after Fallout 4 exits.

Steam status will read **Spacewar**, not Fallout 4. That is expected; see
`docs/STEAM_SPACEWAR.md`.

**LAN fallback** (same network, or Steam down): press **3** on both sides —
host shows its IP, joiner types it. Unchanged from before.

> Updating a pack in place fails while the helper is running (it holds
> `FoM.exe`). Run `FoM.exe --quit-agent` first.

Rebuild pack:

```powershell
powershell -File tools\player_setup\Get-SteamworksRuntime.ps1 -SdkPath <sdk>   # once
powershell -File tools\player_setup\Build-PlayerPack.ps1
```

---

## 0-S. Steam preflight (do this before blaming the game)

```powershell
FoM.exe --steam-check
```

Must end in `RESULT: PASS`. It initialises Steam under AppID 480, prints your
derived `peer_id`, confirms Spacewar ownership, creates a throwaway lobby and
checks the lobby data round-trips.

| Symptom | Fix |
|---------|-----|
| `no steam_api64.dll found` | Pack is missing the Steamworks runtime — run `Get-SteamworksRuntime.ps1` and rebuild the pack |
| `every steam_api64.dll found is too old` | Needs SDK 1.47+; Fallout 4's own copy is from 2015 and is deliberately rejected |
| `SteamAPI_Init failed` | Steam client not running / not signed in |
| lobby error, `owns 480: NO` | Steam → Library → search *Spacewar* → install + run once |
| `invites while FoM is closed: OFF` | Background helper not registered — run FoM, press **[5]** |
| `RESULT: PASS` but invite never arrives | Check `background helper right now:` on the **joiner's** PC. Not running → run FoM once, press **[5]** |
| `connect: (empty)` | Rich presence did not stick; friends-list *Join Game* will not appear. Invites still work |
| Warning about two Steam sessions | Run `FoM.exe --quit-agent` before `--steam-check` for a clean read |

Tunnel-only regression (no Steam, no FO4, no second PC):

```powershell
cd fw_launcher
.\build.bat
.\build\fom_bridge_test.exe     # expect "N checks, 0 failures"
```

---

## 0. Preflight (dev / advanced)

```powershell
cd C:\Users\Jk101\Projects\fallout4multiplayer\FO4_Wrld
powershell -File tools\mp_preflight.ps1
```

Must report protocol match (25/25), FO4 1.10.163, and fresh `dxgi.dll` deployed.

If deploy is stale:

```powershell
cd fw_native
.\build.bat --minimal
.\deploy.bat --minimal
```

**Kill FO4 before deploy** or the DLL copy fails (file locked).

---

## 1. Start order (strict)

| Step | Action | Expect |
|------|--------|--------|
| 1 | Double-click **`start_server.bat`** (repo root) | Console: `server listening on ('127.0.0.1', 31337)` |
| 2 | Launch **Side A** FO4 (Steam classic) | `fw_native.log` in game dir: version match, hooks OK, HELLO/WELCOME |
| 3 | Load a save both peers can share narrative on | Prefer same `world_base` / same campaign start |
| 4 | Launch **Side B** | Same DLL + `fw_config.ini` with **different** `client_id` |
| 5 | Load into world | Server shows 2 peers; A sees B pos (`pos_bcast` climbing) |

### Side A config (example)

`C:\Games\Steam\steamapps\common\Fallout 4\fw_config.ini`:

```ini
server = 127.0.0.1:31337
client_id = player_A
ghost_map = player_B=0x1CA7D
log_level = debug
auto_load_save =
```

### Side B config

Same server line; **must** differ:

```ini
client_id = player_B
ghost_map = player_A=0x1CA7D
```

Launcher A/B (`launcher\start_A.bat` / `start_B.bat`) writes these automatically when FO4_b exists.

### Same-PC two clients

- Needs **`FO4_b`** (second install + single-instance patch).  
- This machine currently: **FO4_b may be missing** — then use **two PCs** on LAN (set `server = <host_ip>:31337` on the remote client) or rebuild FO4_b per START_HERE.

### Two PCs / LAN

**Preferred (friends):** both use `FoM.exe` → **[3] LAN** (Host shows IP, Join types it).  
Full write-up for both players: repo root [README.md](../../README.md).

**Dev / manual:**

1. Host runs `start_server_lan.bat` (binds `0.0.0.0:31337`).  
2. Firewall: allow UDP **31337** inbound on host.  
3. Remote `fw_config.ini`: `server = <host_LAN_ip>:31337`  
4. Both must run **same protocol v25** `dxgi.dll` and **1.10.163**.

Equivalent one-liner:

```text
.\.venv\Scripts\python.exe -m net.server.main --host 0.0.0.0 --port 31337
```

### Two PCs / different networks (Steam)

Nothing to configure. Both sides keep `server = 127.0.0.1:31337` — FoM writes
that automatically and never asks for an IP.

| Step | Action | Expect |
|------|--------|--------|
| 1 | Joiner: **nothing**. FoM is closed. Confirm the helper is armed with `FoM.exe --steam-check` beforehand | `invites while FoM is closed: ON` |
| 2 | Host: `FoM.exe` → **1** | `*** YOU ARE HOST ***` + session code; friend picker opens by itself; server console starts |
| 3 | Host picks the friend in the picker | Invite delivered |
| 4 | Joiner clicks **Accept** — and does nothing else | A FoM window appears on its own: `[FoM] invite accepted - joining...` → `*** JOINED <persona> ***` |
| 5 | Both games launch | Server console lists **two** peers with `s…` ids and **different** `127.0.0.1:<port>` addresses |
| 6 | Press **[S]** in either FoM window | `to_server` / `to_peer` counters climbing on both sides |
| 7 | Quit Fallout 4 on the joiner | FoM closes its window and returns to standby; `Get-Process FoM` still shows the helper |

Repeat step 2-4 without restarting anything: the joiner's helper should take
a second invite straight after the first session ended.

Friends-list variant of steps 3-4: joiner right-clicks the host in their
Steam friends list and picks **Join Game** — no invite sent at all. Same
result. This is the `connect` rich-presence path.

Peer ids are Steam-derived (`s` + base36 of SteamID64), so the old
"both friends shipped as `player_A`" collision cannot happen.

The host's server still binds `0.0.0.0:31337`, so a LAN friend can join the
**same** session the classic way while a remote friend is tunnelled in over
Steam.

---

## 2. Smoke checklist (play)

Do in order. Check **server console** and **both** `fw_native.log` files.

| # | Test | Pass criteria |
|---|------|----------------|
| S1 | Connect | Both HELLO → WELCOME; server lists 2 peers |
| S2 | Move | Both `pos_sent` ↑; remote ghost / pos_bcast visible |
| S3 | F8 / F9 | F8 → teleport to peer; F9 → summon peer (PARTY_WARP) |
| S4 | Cell door | Walk through load door → peer auto-follows (debounce ~2.5s) |
| S5 | Container | A takes item → B container empty (anti-dup) |
| S6 | World loot | A picks bobblehead → B world REFR gone (DISABLE) |
| S7 | Door / lock | Open door / unlock → peer state matches |
| S8 | Quest stage | Advance quest → both logs `Quest.SetCurrentStageID` / BCAST |
| S9 | Faction | Join Minutemen-style rank → FACTION_RANK on peer |
| S10 | Wait/Sleep | Wait 1h → peer GameHour advances (`TIME_PASS` / GlobalVar) |
| S11 | Weather | Weather change → peer weather form |
| S12 | Companion | Recruit → COMPANION; F8 pulls companion near PC |
| S13 | Silent reward / key | Quest silent AddItem or KEY → peer inventory grant |
| S14 | Cleared cell | Clear cell → CELL_CLEARED on peer |

**Out of scope for MINIMAL smoke:** full raider combat AI / shared HP (combat tier compiled out). Use a full non-minimal build later for Concord fights.

---

## 3. Log ground truth

| Log | Path |
|-----|------|
| Server | console from `start_server.bat` |
| Side A native | `...\Fallout 4\fw_native.log` |
| Side B native | `FO4_b\fw_native.log` (or second machine game dir) |

Healthy boot lines (examples):

- `version match` / `1.10.163`
- worldstate hooks OK (`GlobalVar`, `Quest`, `SetFactionRank`, `PassTime`, `AddItem`, …)
- `WELCOME` / net connected
- `pos_sent` increasing after load-in

Red flags:

- version mismatch → wrong FO4 build  
- protocol version reject → mixed old/new DLL vs server  
- `peer_id_taken` → both clients using same `client_id` (should be impossible
  on the Steam path — ids are SteamID-derived)  
- hooks FAILED → bad RVA / wrong binary  

Steam-path red flags (FoM console, not `fw_native.log`):

- `peers=0` on the host after the friend accepted → the P2P session was never
  accepted; check both firewalls allow `FoM.exe`
- joiner shows `(waiting for Fallout 4 to connect)` forever → the game is not
  sending to `127.0.0.1:31337`; check the joiner's `fw_config.ini` was
  actually rewritten by FoM
- `to_server` climbing but the server console shows no second peer → the
  campaign server is not on `127.0.0.1:31337` on the **host** machine
- `port 31337 on 127.0.0.1 is already in use` on a joiner → a server from an
  earlier Host run is still alive; kill it

---

## 4. Reset between runs

1. Exit both FO4 processes.  
2. Ctrl+C server (or close window).  
3. Optional: delete / rotate `net/state_snapshot.json` for a clean world.  
4. Redeploy only if you rebuilt: `fw_native\deploy.bat --minimal`.

---

## 5. Hotkeys

In-game:

| Key | Action |
|-----|--------|
| **F8** | Teleport **to** peer |
| **F9** | **Summon** peer to me |

In the FoM window (Steam session):

| Key | Action |
|-----|--------|
| **I** | Open the Steam invite overlay (host only) |
| **S** | Print lobby code, peer count, packet/byte counters |
| **Q** | Close the tunnel and exit |

FoM also exits on its own ~5s after Fallout 4 closes, so a friend is not left
with an orphan tunnel running.

---

## 5-S. Steam acceptance gates

| Gate | How | Pass criteria |
|------|-----|----------------|
| **G1 — unit** | `fw_launcher\build\fom_bridge_test.exe` | Frames survive the tunnel byte-identical; each peer gets a distinct loopback source port; oversize dropped; a non-host peer cannot reach the game. `0 failures` |
| **G2 — Steam bring-up** | `FoM.exe --steam-check` on both PCs | `RESULT: PASS`, `owns 480: yes`, `connect: ... -> friends see a Join Game button`, `invites while FoM is closed: ON` |
| **G2b — agent lifecycle** | `--quit-agent` (nothing running) → start `--agent` → second `--agent` → `--quit-agent` | Refusal, single instance holds, clean stop. No command takes more than ~2s, ever |
| **G3 — two PCs, LAN, Steam invite, joiner's FoM CLOSED** | Both on the same network. Host presses **1**; joiner only clicks Accept | Joiner's FoM appears on its own; joiner never types an IP or opens anything; server console shows 2 peers |
| **G3b — friends-list join** | Joiner uses **Join Game** from the Steam friends list, no invite sent | Same as G3 |
| **G4 — two PCs, different NAT** | Host and joiner on genuinely different networks (e.g. one on mobile hotspot) | Same as G3. This is the gate that proves relay/NAT traversal, not just loopback |
| **G5 — LAN fallback** | Kill Steam, run `FoM.exe` → **1** | Prints the Steam failure honestly, offers LAN, LAN host/join still works |
| **G6 — protocol regression** | `.venv\Scripts\python.exe -m pytest net/tests -q` | All green, including `test_protocol_version_lockstep.py` |

G1, G2, G2b, G5 and G6 are runnable on one machine. G3, G3b and G4 need two
humans and two legal Fallout 4 installs.

---

## 6. What “ready” means today

| Layer | Status |
|-------|--------|
| Protocol v25 Python ↔ C++ | Matched |
| Server authoritative campaign slices | Quest / Global / Faction / Weather / Companion / CellCleared / Relationship / TimePass / ItemGrant |
| Economy under MINIMAL | Containers, put, lock, pickup, doors, equip |
| Ghost + POS | Proven path; multi-peer polish ongoing |
| Same-PC dual client | Requires FO4_b |
| Full combat co-op | Prefer non-MINIMAL build |
| Steam invite (any network) | M1 shipped — lobby + invite + P2P tunnel; overlay shows *Spacewar* |
| LAN (no Steam) | Still supported as the fallback path |

See also: `docs/COOP_1TO1_GAP.md` for honest 1:1 limits, and
`docs/STEAM_SPACEWAR.md` for how the Steam path works and what it does not do.
