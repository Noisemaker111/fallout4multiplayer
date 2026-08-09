# FalloutWorld — friend install (one click)

## What you send friends

```
FoM_PlayerPack.zip   (from tools/player_setup/dist/)
```

## What they do

**Once:** unzip, double-click **`FoM.exe`**, let it find Fallout 4.

**Then, forever after:**

| Host | Friend |
|------|--------|
| Double-click `FoM.exe`, press **1**. Steam's friend picker opens by itself — pick them. | Click **Accept**. Their game opens. They're in. |

That’s it. **No IP. No port forwarding. Nothing for the friend to open
first.** They can be on a completely different network.

They can also join with no invite at all: right-click the host in the Steam
friends list → **Join Game**.

| Role | What FoM does |
|------|----------------|
| **Host** | Installs multiplayer files, creates a Steam lobby, opens the invite picker, starts the server, runs the Steam↔UDP tunnel, launches FO4 |
| **Join** | Everything above in reverse, unattended, triggered by the invite |
| **LAN (3)** | The old behaviour — host shows an IP, joiner types it. Fallback when Steam is unavailable |

No Nexus. No MO2. No deploy.bat. No editing `fw_config.ini`.

**Keep the FoM window open while playing** — it is the tunnel. It closes
itself a few seconds after Fallout 4 exits.

## How accepting an invite opens the game

Steam only delivers an invite to a process already running as the app in
question, and for AppID 480 it would otherwise launch Valve's Spacewar rather
than us. So FoM leaves a small resident helper running: idle and windowless
until an invite lands, at which point it does the entire join by itself.

It is one `HKCU\...\Run` entry named `FalloutWorld (FoM)`, registered after
the first successful Steam session and announced in the console. `[5]` in the
menu shows and toggles it; `FoM.exe --quit-agent` stops it (do that before
overwriting `FoM.exe`, which it holds open).

## Why does Steam say "Spacewar"?

Because it does. FoM carries the lobby, the invite and the peer-to-peer
connection over **Spacewar (AppID 480)**, Valve's free public test app that
every Steam account can use. Fallout 4 itself is untouched classic 1.10.163.

If hosting fails with a lobby error, the fix is: Steam → Library → search
*Spacewar* → install and run it once.

Full detail and the honest limitations: [`docs/STEAM_SPACEWAR.md`](../../docs/STEAM_SPACEWAR.md).

## Steamworks runtime (developer, once)

The pack must ship a modern `steam_api64.dll` (Steamworks SDK 1.47+).
Fallout 4's own copy is from 2015 and is deliberately rejected.

```powershell
# preferred
powershell -File tools\player_setup\Get-SteamworksRuntime.ps1 -SdkPath C:\steamworks_sdk
# local testing only
powershell -File tools\player_setup\Get-SteamworksRuntime.ps1 -FromInstalledGame
```

Without it the pack still builds — Steam mode just fails over to LAN with a
clear message.

## Host requirement

The Host PC needs **Python 3** once (https://python.org — check “Add to PATH”),  
**or** a pack that includes `runtime\python.exe` (optional embed).

Join does **not** need Python.

## Classic FO4 (1.10.163)

Both players need **Fallout 4 1.10.163**. If Steam updated to next-gen, use the depot steps in the old setup notes or a pre-downgraded install. FoM installs the multiplayer client either way once the exe is classic.

## Build the pack (you, developer)

```powershell
cd FO4_Wrld\fw_native
.\build.bat --minimal
cd ..\fw_launcher
.\build.bat
cd ..\tools\player_setup
powershell -File .\Build-PlayerPack.ps1
```

Zip: `tools\player_setup\dist\FoM_PlayerPack.zip`

## Drop-in mode

You can also copy just:

- `FoM.exe`
- `dxgi.dll` (or `payload\dxgi.dll`)
- `runtime\` (Host)

into the Fallout 4 folder and double-click `FoM.exe` there.
