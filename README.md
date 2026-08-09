# FalloutWorld (FO4_Wrld)

**Unofficial Fallout 4 multiplayer** — walk around together, see each other’s character (movement + full-body anims), share world interactions, and fight the same raiders.

> **Status:** Active development. Expect bugs and crashes. Back up your game and saves.  
> **Game version:** Fallout 4 **classic `1.10.163` only** (not next-gen).  
> **License:** PolyForm Noncommercial 1.0.0 — see [FO4_Wrld/LICENSE](FO4_Wrld/LICENSE).

**Demo:** [90s YouTube clip](https://www.youtube.com/watch?v=Qs3dNzXnnko)

This repo is the public home of the project. The playable code lives under **`FO4_Wrld/`**.  
`F4MP-Archive/` is an older third-party archive kept for reference only — do not use it to play.

---

## Play together on your LAN (two PCs)

You and a second PC on the **same home network**. Easiest path is the **player pack** (`FoM.exe`). No port forwarding, no typing public IPs.

### What both of you need

| Requirement | Notes |
|-------------|--------|
| **Fallout 4** | Own a legit Steam copy |
| **Game build `1.10.163`** | Classic / pre-next-gen. Next-gen will refuse to load the multiplayer client |
| **Steam client** | Signed in on both machines (game still launches through FoM, not Steam “Play”) |
| **Same multiplayer build** | Same `FoM_PlayerPack` (or same built `dxgi.dll` + protocol) on both PCs |
| **Windows 10/11** | 64-bit |

**Host PC only (the one that starts the session):**

| Requirement | Notes |
|-------------|--------|
| **Python 3** | [python.org](https://www.python.org/downloads/) — check **“Add python.exe to PATH”** when installing. Needed to run the small game server. Join-only PCs do **not** need Python if you use FoM’s built-in flow with a pack that already includes `runtime\`. |

Optional: Windows Firewall will prompt the first time — **allow** access on **private** networks for Python / FoM (UDP **31337**).

### Step 1 — Get the player pack

Until GitHub Releases ship a zip, the host builds or shares:

```text
FO4_Wrld\tools\player_setup\dist\FoM_PlayerPack.zip
```

**Host (developer tree):**

```powershell
cd FO4_Wrld\fw_native
.\build.bat --minimal
cd ..\fw_launcher
.\build.bat
cd ..\tools\player_setup
# once: steamworks SDK or -FromInstalledGame (see tools/player_setup/README.md)
powershell -File .\Build-PlayerPack.ps1
```

Copy `FoM_PlayerPack.zip` to the other PC (USB stick, network share, Discord, etc.).  
Unzip **anywhere** on each machine (or into the Fallout 4 folder).

### Step 2 — One-time install on each PC

1. Make sure Fallout 4 is **1.10.163** (see [Classic FO4](#classic-fo4-110163) below).
2. Double-click **`FoM.exe`**.
3. Let it find your Fallout 4 install and install multiplayer files (`dxgi.dll`, config, etc.).
4. Close when it says install is done if it asks you to re-run.

Do this on **both** PCs before the first session.

### Step 3 — Host starts a LAN session

On the **host** PC:

1. Open **`FoM.exe`**.
2. Press **`3`** → **LAN only**.
3. Press **`1`** → **Host**.
4. FoM prints your **LAN IP** (something like `192.168.1.42`). **Tell that IP to the joiner.**
5. Leave the **FoM window open** for the whole session (it runs the server + keeps the game wired). Fallout 4 should start after setup.

Firewall: if Windows asks, allow FoM/Python on private networks. If the joiner cannot connect, on the host allow inbound **UDP port 31337** (Windows Defender Firewall → Advanced → Inbound rule).

### Step 4 — Joiner connects on LAN

On the **second** PC:

1. Open **`FoM.exe`**.
2. Press **`3`** → **LAN only**.
3. Press **`2`** → **Join**.
4. Type the host’s IP (with or without `:31337` — FoM uses port **31337**).
5. Leave FoM open; Fallout 4 should launch.

### Step 5 — In game

1. Both load into a **compatible save** (same broad campaign progress helps; for first tests, fresh or early Sanctuary saves are fine).
2. You should see each other as a remote “ghost” body and move around together.
3. When finished: quit Fallout 4; FoM usually closes a few seconds later. Host can Ctrl+C the server if a separate console is open.

### LAN checklist (quick)

| # | Host | Joiner |
|---|------|--------|
| 1 | FO4 1.10.163 + FoM installed once | Same |
| 2 | `FoM.exe` → **3** → Host | `FoM.exe` → **3** → Join |
| 3 | Read off LAN IP | Type that IP |
| 4 | Firewall allows UDP 31337 | Same Wi‑Fi/LAN subnet |
| 5 | Keep FoM open | Keep FoM open |
| 6 | Load save / enter world | Load save / enter world |

### If LAN does not connect

| Symptom | What to try |
|---------|-------------|
| Joiner cannot reach host | Both on same subnet? (`ipconfig` → IPv4). Host IP not a VPN adapter. |
| Timeout / no peers | Host firewall: allow UDP **31337**. Disable VPN temporarily. |
| Wrong game version | Next-gen exe rejected — downgrade to **1.10.163**. |
| Desync / missing features | Same player pack version on both PCs. |
| Host has no Python | Install Python 3 with PATH, or use a pack that embeds `runtime\python.exe`. |

**Find host IP yourself (PowerShell on host):**

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object IPAddress, InterfaceAlias
```

Use the address on your LAN/Wi‑Fi adapter (usually `192.168.x.x` or `10.x.x.x`).

---

## Play over the internet (Steam invite)

Same player pack. Works across different home networks — **no port forwarding**.

| Who | What |
|-----|------|
| **Host** | `FoM.exe` → press **`1`**. Steam friend picker opens; invite your friend. |
| **Friend** | Click **Accept**. Game opens. They do **not** need FoM open first. |

Or: friend right-clicks the host in Steam → **Join Game**.

Steam may show you as playing **Spacewar** (AppID 480). That is expected — FoM uses Valve’s free test app for lobbies/P2P. Fallout 4 stays classic 1.10.163.

Details: [FO4_Wrld/docs/STEAM_SPACEWAR.md](FO4_Wrld/docs/STEAM_SPACEWAR.md) and [FO4_Wrld/tools/player_setup/README.md](FO4_Wrld/tools/player_setup/README.md).

---

## Classic FO4 (1.10.163)

The multiplayer client is pinned to **`Fallout4.exe` 1.10.163.0**.

If Steam updated you to next-gen:

1. Use a known-good classic install / depot downgrade (community guides for “Fallout 4 downgrade 1.10.163”).
2. Prefer launching through **FoM** so Steam’s “Play” does not silently re-update mid-session.
3. Confirm version: file properties on `Fallout4.exe`, or the in-game / FoM logs after load.

Dev notes: [FO4_Wrld/docs/GAME_VERSION.md](FO4_Wrld/docs/GAME_VERSION.md), [FO4_Wrld/docs/MP_TEST_RUNBOOK.md](FO4_Wrld/docs/MP_TEST_RUNBOOK.md).

---

## Dev / from-source (advanced)

Repo layout:

| Path | Role |
|------|------|
| `FO4_Wrld/fw_native/` | Game client (`dxgi.dll` proxy) |
| `FO4_Wrld/fw_launcher/` | `FoM.exe` (Steam + LAN UI) |
| `FO4_Wrld/net/` | Authoritative Python UDP server |
| `FO4_Wrld/launcher/` | Older Python A/B orchestrator |
| `FO4_Wrld/docs/` | Runbooks, roadmap, Steam notes |
| `F4MP-Archive/` | Historical F4MP sources — not the active stack |

**Server only (LAN bind on all interfaces):**

```powershell
cd FO4_Wrld
# optional: python -m venv .venv ; .\.venv\Scripts\pip install -e .   # if you use a venv
.\start_server_lan.bat
```

Listens on `0.0.0.0:31337`. Point remote `fw_config.ini` at `server = <host_lan_ip>:31337`.

**Build client + pack:** see [FO4_Wrld/README.md](FO4_Wrld/README.md) and [tools/player_setup](FO4_Wrld/tools/player_setup/README.md).

**Same-PC two clients:** needs a second game install + single-instance patch — see [FO4_Wrld/docs/START_HERE.md](FO4_Wrld/docs/START_HERE.md). For real co-op, **two PCs** is simpler.

---

## Safety & legal

- Not affiliated with Bethesda / Microsoft / Zenimax.
- Do not redistribute Fallout 4 binaries, BA2s, or saves.
- Noncommercial license on our code — read `FO4_Wrld/LICENSE`.
- Backup `Fallout4.exe` and saves before installing.

---

## Contributing

The native client uses aggressive scene-graph injection and binary hooks. Casual drive-by refactors can hard-crash the game. Prefer issues and small, tested PRs; read `FO4_Wrld/docs/START_HERE.md` before large changes.

---

## Links

- In-depth project README: [FO4_Wrld/README.md](FO4_Wrld/README.md)
- Friend install (Steam path): [FO4_Wrld/tools/player_setup/README.md](FO4_Wrld/tools/player_setup/README.md)
- Multiplayer runbook: [FO4_Wrld/docs/MP_TEST_RUNBOOK.md](FO4_Wrld/docs/MP_TEST_RUNBOOK.md)
