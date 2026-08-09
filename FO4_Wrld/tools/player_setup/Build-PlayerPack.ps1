# Build FoM one-click player pack.
# Output: tools/player_setup/dist/FoM_PlayerPack/  and  FoM_PlayerPack.zip
#
# Friend UX: unzip, double-click FoM.exe -> Host or Join. Done.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Repo = Resolve-Path (Join-Path $Root "..\..")
$Payload = Join-Path $Root "payload"
$Out = Join-Path $Root "dist"
$Stage = Join-Path $Out "FoM_PlayerPack"
$Fo4 = "C:\Games\Steam\steamapps\common\Fallout 4"

New-Item -ItemType Directory -Force -Path $Payload, $Out | Out-Null

Write-Host "== payload ==" -ForegroundColor Cyan
$dll = Join-Path $Repo "fw_native\build-minimal\dxgi.dll"
if (-not (Test-Path $dll)) {
    Write-Host "Building minimal dxgi.dll..." -ForegroundColor Yellow
    Push-Location (Join-Path $Repo "fw_native")
    cmd /c "build.bat --minimal"
    Pop-Location
}
if (Test-Path $dll) {
    Copy-Item $dll $Payload -Force
    Write-Host "  dxgi.dll"
} else {
    throw "missing $dll - build fw_native --minimal first"
}

foreach ($f in @(
    "xdelta3.exe", "patch_steam_api_diff.vcdiff",
    "f4se_loader.exe", "f4se_1_10_163.dll", "f4se_steam_loader.dll"
)) {
    $src = Join-Path $Fo4 $f
    if (Test-Path $src) {
        Copy-Item $src $Payload -Force
        Write-Host "  $f"
    } else {
        Write-Host "  SKIP $f" -ForegroundColor DarkYellow
    }
}

Write-Host "== Steamworks runtime (Steam invites) ==" -ForegroundColor Cyan
# FoM.exe hosts the Spacewar (AppID 480) lobby and the Steam P2P tunnel. That
# needs a MODERN steam_api64.dll: ISteamNetworkingMessages landed in SDK 1.46
# and the manual callback dispatch FoM uses landed in 1.47. Fallout 4's own
# 2015-vintage steam_api64.dll is far too old, so we ship our own next to
# FoM.exe rather than reusing the game's.
#
# Put the redistributable at tools/player_setup/steamworks/steam_api64.dll
# (from the Steamworks SDK: sdk/redistributable_bin/win64/steam_api64.dll).
# Run Get-SteamworksRuntime.ps1 to stage one.
$SteamworksDll = Join-Path $Root "steamworks\steam_api64.dll"
$HaveSteamworks = Test-Path $SteamworksDll
if ($HaveSteamworks) {
    Write-Host "  steam_api64.dll"
} else {
    Write-Host "  SKIP steam_api64.dll - Steam invites will fall back to LAN." -ForegroundColor Yellow
    Write-Host "       Run tools\player_setup\Get-SteamworksRuntime.ps1 first." -ForegroundColor Yellow
}

Write-Host "== FoM.exe ==" -ForegroundColor Cyan
$fomSrc = Join-Path $Repo "fw_launcher\build\FoM.exe"
if (-not (Test-Path $fomSrc)) {
    $fomSrc = Join-Path $Repo "fw_launcher\build\Release\FoM.exe"
}
if (-not (Test-Path $fomSrc)) {
    Write-Host "Building FoM.exe..." -ForegroundColor Yellow
    Push-Location (Join-Path $Repo "fw_launcher")
    cmd /c "build.bat"
    Pop-Location
    $fomSrc = Join-Path $Repo "fw_launcher\build\FoM.exe"
    if (-not (Test-Path $fomSrc)) {
        $fomSrc = Join-Path $Repo "fw_launcher\build\Release\FoM.exe"
    }
}
if (-not (Test-Path $fomSrc)) {
    throw "FoM.exe not built - check fw_launcher\build.bat"
}

Write-Host "== runtime (server for Host) ==" -ForegroundColor Cyan
$Runtime = Join-Path $Stage "runtime"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage, $Runtime | Out-Null

# Copy net package (server)
$NetSrc = Join-Path $Repo "net"
$NetDst = Join-Path $Runtime "net"
New-Item -ItemType Directory -Force -Path $NetDst | Out-Null
robocopy $NetSrc $NetDst /E /XD __pycache__ tests snapshot-old .pytest_cache /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
# channel lives at net/../channel? Actually channel.py is in net/
# protocol is net/protocol.py - good

# Prefer system Python for host runtime note; if embeddable exists use it
$PyEmbed = Join-Path $Root "python_embed"
if (Test-Path (Join-Path $PyEmbed "python.exe")) {
    robocopy $PyEmbed $Runtime /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    Write-Host "  embedded python"
} else {
    # Stub: create run_server.bat that finds python
    @"
@echo off
REM Host server helper if FoM could not start it.
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Install Python 3 from python.org and re-run FoM as Host.
  pause
  exit /b 1
)
python -u -m net.server.main --host 0.0.0.0 --port 31337
"@ | Set-Content (Join-Path $Runtime "run_server.bat") -Encoding ascii
    Write-Host "  runtime\net + run_server.bat (uses system Python on Host)"
}

Copy-Item $fomSrc (Join-Path $Stage "FoM.exe") -Force
Copy-Item $Payload (Join-Path $Stage "payload") -Recurse -Force

# FoM.exe forces AppID 480 via the SteamAppId env var and this file; Steam
# reads steam_appid.txt relative to the working directory, which FoM pins to
# its own folder before calling SteamAPI_Init.
"480" | Set-Content (Join-Path $Stage "steam_appid.txt") -Encoding ascii
Write-Host "  steam_appid.txt = 480"

if ($HaveSteamworks) {
    Copy-Item $SteamworksDll (Join-Path $Stage "steam_api64.dll") -Force
}

# One-page friend instructions
@"
FalloutWorld multiplayer
========================

SETUP (once)
  1. Unzip this folder anywhere (or into your Fallout 4 folder).
  2. Double-click FoM.exe and let it find your Fallout 4.

That's it. From now on:

TO PLAY WITH A FRIEND
  You:          double-click FoM.exe, press 1. Steam's friend picker opens.
                Pick your friend.
  Your friend:  clicks Accept. Their game opens. They're in your world.

Your friend does NOT need to open anything first. No IP. No port
forwarding. Nothing to type.

They can also join you with no invite at all: right-click you in their
Steam friends list and pick "Join Game".

Keep the FoM window open while you play - it is what carries the
connection. It closes itself a few seconds after you quit Fallout 4.


HOW DOES ACCEPTING AN INVITE OPEN THE GAME?
FoM leaves a small helper running in the background so Steam has something
to deliver the invite to. It uses no meaningful CPU and shows no window
until you're actually invited. Run FoM and press 5 to see it or turn it
off - it's a single Windows startup entry called "FalloutWorld (FoM)".

WHY DOES STEAM SAY I'M PLAYING "SPACEWAR"?
Because we are. Spacewar (AppID 480) is Valve's free public test app that
every Steam account can use. FoM borrows it to carry the lobby, the invite
and the peer-to-peer connection. Fallout 4 itself is untouched - still
plain 1.10.163. Your Steam status will say Spacewar while a session is up.
Expected and harmless.

If hosting fails with a lobby error: open Steam > Library > search
"Spacewar", install and run it once, then try again.

SAME NETWORK (LAN), OR STEAM DOWN?
  Host:   FoM.exe -> press 3 -> Host. Read the LAN IP it prints.
  Friend: FoM.exe -> press 3 -> Join. Type that IP.
  Both: keep FoM open. Host firewall must allow UDP 31337 if join fails.

TROUBLE?
  FoM.exe --steam-check    tells you exactly what is and isn't working
  FoM.exe --quit-agent     stops the background helper (do this before
                           replacing FoM.exe with a newer version)

Both players need Fallout 4 version 1.10.163 (classic).
Do not use Steam "Play" if it upgrades the game.

Host needs Python 3 installed once (python.org) unless runtime\python.exe is included.
"@ | Set-Content (Join-Path $Stage "HOW_TO_PLAY.txt") -Encoding ascii

$zip = Join-Path $Out "FoM_PlayerPack.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $zip -Force

Write-Host ""
Write-Host "Packed: $zip" -ForegroundColor Green
Write-Host "Folder: $Stage" -ForegroundColor Green
Get-ChildItem $Stage | Format-Table Name, Length
Get-ChildItem (Join-Path $Stage "payload") | Format-Table Name, Length

if (-not $HaveSteamworks) {
    Write-Host "REMINDER: no steam_api64.dll in this pack - Steam invites will" -ForegroundColor Yellow
    Write-Host "          fall back to LAN. Run Get-SteamworksRuntime.ps1." -ForegroundColor Yellow
}

# robocopy exits 1 on "files were copied", which is success. Do not let that
# leak out as a failed pack build.
exit 0
