# Stage the Steamworks redistributable that FoM.exe needs for Steam invites.
#
# FoM hosts the Spacewar (AppID 480) lobby and tunnels co-op traffic over
# ISteamNetworkingMessages. That needs steam_api64.dll from Steamworks SDK
# 1.47 or newer:
#   - ISteamNetworkingMessages  (SDK 1.46+)  - the P2P transport
#   - SteamAPI_ManualDispatch_* (SDK 1.47+)  - callbacks without the C++ SDK
#
# Fallout 4 ships a 2015-vintage steam_api64.dll that has neither, which is
# why we stage our own instead of reusing the game's.
#
# Output: tools/player_setup/steamworks/steam_api64.dll
#         (Build-PlayerPack.ps1 picks it up from there)
#
# Usage:
#   .\Get-SteamworksRuntime.ps1 -SdkPath "C:\steamworks_sdk"
#   .\Get-SteamworksRuntime.ps1 -FromInstalledGame        # dev convenience
#   .\Get-SteamworksRuntime.ps1 -List                     # just show candidates

[CmdletBinding()]
param(
    # Steamworks SDK root, or any folder containing steam_api64.dll
    # (the SDK keeps it at sdk\redistributable_bin\win64\).
    [string] $SdkPath,

    # Borrow the redistributable from an installed Steam game. Fine for local
    # testing; prefer -SdkPath for anything you hand to other people, so you
    # know exactly which SDK build you shipped.
    [switch] $FromInstalledGame,

    [switch] $List
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$OutDir = Join-Path $Root "steamworks"
$OutFile = Join-Path $OutDir "steam_api64.dll"

# Export names FoM resolves by GetProcAddress. They appear verbatim in the
# PE export name table, so a byte scan is enough to tell a usable DLL from a
# too-old one without needing dumpbin on the box.
$RequiredExports = @(
    "SteamAPI_ManualDispatch_Init",
    "SteamAPI_ManualDispatch_GetNextCallback",
    "SteamAPI_ISteamNetworkingMessages_SendMessageToUser",
    "SteamAPI_ISteamNetworkingMessages_ReceiveMessagesOnChannel",
    "SteamAPI_ISteamMatchmaking_CreateLobby"
)

function Test-SteamworksDll {
    param([string] $Path)
    if (-not (Test-Path $Path)) { return $false }
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
    } catch {
        return $false
    }
    $text = [System.Text.Encoding]::ASCII.GetString($bytes)
    foreach ($name in $RequiredExports) {
        if ($text.IndexOf($name, [StringComparison]::Ordinal) -lt 0) {
            return $false
        }
    }
    return $true
}

function Get-CandidateDlls {
    $roots = @(
        "C:\Program Files (x86)\Steam\steamapps\common",
        "C:\Games\Steam\steamapps\common",
        "C:\Steam\steamapps\common",
        "D:\Steam\steamapps\common",
        "D:\SteamLibrary\steamapps\common",
        "E:\Steam\steamapps\common",
        "E:\SteamLibrary\steamapps\common"
    ) | Where-Object { Test-Path $_ }

    $found = @()
    foreach ($r in $roots) {
        Get-ChildItem -Path $r -Filter steam_api64.dll -Recurse -Depth 5 `
            -ErrorAction SilentlyContinue | ForEach-Object {
            $found += $_
        }
    }
    return $found
}

if ($SdkPath) {
    $candidates = @(
        (Join-Path $SdkPath "steam_api64.dll"),
        (Join-Path $SdkPath "redistributable_bin\win64\steam_api64.dll"),
        (Join-Path $SdkPath "sdk\redistributable_bin\win64\steam_api64.dll")
    )
    $src = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $src) {
        throw "no steam_api64.dll under $SdkPath (looked in redistributable_bin\win64\ too)"
    }
    if (-not (Test-SteamworksDll $src)) {
        throw "$src is too old - it lacks ISteamNetworkingMessages / manual dispatch. Use Steamworks SDK 1.47 or newer."
    }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    Copy-Item $src $OutFile -Force
    Write-Host "Staged $OutFile" -ForegroundColor Green
    Write-Host "  from $src"
    return
}

Write-Host "Scanning installed Steam games for a usable steam_api64.dll..." -ForegroundColor Cyan
$all = Get-CandidateDlls
$usable = @()
foreach ($f in $all) {
    $ok = Test-SteamworksDll $f.FullName
    $mark = if ($ok) { "USABLE " } else { "too old" }
    $colour = if ($ok) { "Green" } else { "DarkGray" }
    Write-Host ("  [{0}] {1}" -f $mark, $f.FullName) -ForegroundColor $colour
    if ($ok) { $usable += $f }
}

if ($usable.Count -eq 0) {
    Write-Host ""
    Write-Host "No usable steam_api64.dll found." -ForegroundColor Yellow
    Write-Host "Download the Steamworks SDK (partner.steamgames.com/downloads/list)"
    Write-Host "and re-run:  .\Get-SteamworksRuntime.ps1 -SdkPath <sdk folder>"
    exit 1
}

if ($List) {
    Write-Host ""
    Write-Host ("{0} usable candidate(s). Re-run with -FromInstalledGame to stage the newest." -f $usable.Count)
    return
}

if (-not $FromInstalledGame) {
    Write-Host ""
    Write-Host ("{0} usable candidate(s) found, but nothing was copied." -f $usable.Count)
    Write-Host "Pass -FromInstalledGame to stage the newest one (fine for local testing),"
    Write-Host "or -SdkPath <folder> to use an official Steamworks SDK download."
    return
}

$best = $usable | Sort-Object Length -Descending | Select-Object -First 1
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Copy-Item $best.FullName $OutFile -Force
Write-Host ""
Write-Host "Staged $OutFile" -ForegroundColor Green
Write-Host "  from $($best.FullName)"
Write-Host "  NOTE: borrowed from another game's install. For a release pack," -ForegroundColor Yellow
Write-Host "        re-run with -SdkPath so you know which SDK build shipped." -ForegroundColor Yellow
