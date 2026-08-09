#Requires -Version 5.1
<#
.SYNOPSIS
  FalloutWorld multiplayer one-click setup (no Nexus).

.DESCRIPTION
  Point at your Fallout 4 folder. This script:
    - Detects FO4 / lets you pick the folder
    - Applies classic depots if Steam already downloaded them
    - Patches steam_api64 to classic (SteamUser) via bundled xdelta
    - Installs F4SE 0.6.23 + dxgi.dll (multiplayer client) from payload\
    - Disables DLC/Creations load lists (stable classic boot)
    - Writes fw_config.ini (host or join)
    - Optional: pin Steam AutoUpdateBehavior=1

  ONE manual step if the game is still Next-Gen (1.11.x):
    Steam → console → download classic depots (script prints the commands).

.PARAMETER Fo4Dir
  Path to the Fallout 4 install (folder that contains Fallout4.exe).

.PARAMETER Mode
  host | join | setup-only  (default: setup-only)

.PARAMETER Server
  host:ip:port for join, e.g. 192.168.1.20:31337

.PARAMETER ClientId
  player_A / player_B / custom
#>
param(
  [string]$Fo4Dir = "",
  [ValidateSet("setup-only", "host", "join")]
  [string]$Mode = "setup-only",
  [string]$Server = "127.0.0.1:31337",
  [string]$ClientId = "player_A",
  [switch]$SkipDepotPrompt,
  [switch]$Launch
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Payload = Join-Path $ScriptDir "payload"
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..") -ErrorAction SilentlyContinue

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "  XX  $msg" -ForegroundColor Red }

function Find-Fo4Default {
  $candidates = @(
    "C:\Games\Steam\steamapps\common\Fallout 4",
    "C:\Program Files (x86)\Steam\steamapps\common\Fallout 4",
    "D:\SteamLibrary\steamapps\common\Fallout 4",
    "E:\SteamLibrary\steamapps\common\Fallout 4"
  )
  # libraryfolders.vdf
  $vdf = "C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
  if (Test-Path $vdf) {
    $text = Get-Content $vdf -Raw
    [regex]::Matches($text, '"path"\s+"([^"]+)"') | ForEach-Object {
      $lib = $_.Groups[1].Value -replace '\\\\', '\'
      $candidates += (Join-Path $lib "steamapps\common\Fallout 4")
    }
  }
  foreach ($c in $candidates) {
    if (Test-Path (Join-Path $c "Fallout4.exe")) { return $c }
  }
  return $null
}

function Pick-Folder($title) {
  Add-Type -AssemblyName System.Windows.Forms | Out-Null
  $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
  $dlg.Description = $title
  $dlg.ShowNewFolderButton = $false
  if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    return $dlg.SelectedPath
  }
  return $null
}

function Get-ExeVersion([string]$exe) {
  $py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = "python" }
  try {
    $v = & $py -c "import pefile; pe=pefile.PE(r'$exe'); fi=pe.VS_FIXEDFILEINFO[0]; print(f'{(fi.FileVersionMS>>16)&0xFFFF}.{fi.FileVersionMS&0xFFFF}.{(fi.FileVersionLS>>16)&0xFFFF}.{fi.FileVersionLS&0xFFFF}')" 2>$null
    if ($v) { return $v.Trim() }
  } catch {}
  # fallback FileVersionInfo
  try {
    return [Diagnostics.FileVersionInfo]::GetVersionInfo($exe).FileVersion
  } catch { return "unknown" }
}

function Test-SteamUserExport([string]$dll) {
  if (-not (Test-Path $dll)) { return $false }
  $py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = "python" }
  try {
    $r = & $py -c "import pefile; pe=pefile.PE(r'$dll'); print(any(e.name and e.name.decode()=='SteamUser' for e in pe.DIRECTORY_ENTRY_EXPORT.symbols))" 2>$null
    return ($r -eq "True")
  } catch { return $false }
}

function Find-Depot([string]$id) {
  $libs = @(
    "C:\Program Files (x86)\Steam",
    "C:\Games\Steam"
  )
  # also scan libraryfolders
  $vdf = "C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
  if (Test-Path $vdf) {
    [regex]::Matches((Get-Content $vdf -Raw), '"path"\s+"([^"]+)"') | ForEach-Object {
      $libs += ($_.Groups[1].Value -replace '\\\\', '\')
    }
  }
  foreach ($lib in ($libs | Select-Object -Unique)) {
    $p = Join-Path $lib "steamapps\content\app_377160\depot_$id"
    if (Test-Path $p) { return $p }
  }
  return $null
}

function Apply-ClassicDepots([string]$fo4) {
  $map = @{
    "377162" = "exe"
    "377161" = "content_a"
    "377163" = "content_b"
    "377164" = "english"
  }
  $found = @{}
  foreach ($id in $map.Keys) {
    $d = Find-Depot $id
    if ($d) { $found[$id] = $d; Write-Ok "depot $id ($($map[$id]))" }
    else { Write-Warn "depot $id missing ($($map[$id]))" }
  }
  if (-not $found.ContainsKey("377162")) { return $false }

  $bak = Join-Path $fo4 "Fallout4.exe.1.11.bak"
  $exe = Join-Path $fo4 "Fallout4.exe"
  if ((Test-Path $exe) -and -not (Test-Path $bak)) {
    Copy-Item $exe $bak -Force
    Write-Ok "backed up next-gen exe → Fallout4.exe.1.11.bak"
  }

  foreach ($id in $found.Keys) {
    Write-Step "Copying depot $id …"
    & robocopy $found[$id] $fo4 /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Ok "depot $id applied (robocopy $LASTEXITCODE)"
  }
  return $true
}

function Patch-SteamApi([string]$fo4) {
  $api = Join-Path $fo4 "steam_api64.dll"
  $xdelta = Join-Path $Payload "xdelta3.exe"
  $patch = Join-Path $Payload "patch_steam_api_diff.vcdiff"
  $bak = Join-Path $fo4 "steam_api64_downgradeBackup.dll"

  if (Test-SteamUserExport $api) {
    Write-Ok "steam_api64 already has SteamUser"
    return $true
  }
  if (-not (Test-Path $xdelta) -or -not (Test-Path $patch)) {
    Write-Warn "payload missing xdelta/patch — cannot fix steam_api"
    return $false
  }
  if (-not (Test-Path $bak)) {
    if (Test-Path $api) {
      Copy-Item $api $bak -Force
      Write-Ok "saved next-gen steam_api as downgradeBackup"
    } else {
      Write-Fail "no steam_api64.dll to patch"
      return $false
    }
  }
  if (Test-Path $api) { Remove-Item $api -Force }
  & $xdelta -d -vfs $bak $patch $api
  if ($LASTEXITCODE -ne 0 -or -not (Test-SteamUserExport $api)) {
    Write-Fail "xdelta steam_api patch failed"
    return $false
  }
  Write-Ok "steam_api64 patched (classic SteamUser)"
  return $true
}

function Install-Payload([string]$fo4) {
  $files = @(
    "dxgi.dll",
    "f4se_loader.exe",
    "f4se_1_10_163.dll",
    "f4se_steam_loader.dll"
  )
  foreach ($f in $files) {
    $src = Join-Path $Payload $f
    if (-not (Test-Path $src)) {
      Write-Warn "payload missing $f"
      continue
    }
    Copy-Item $src (Join-Path $fo4 $f) -Force
    Write-Ok "installed $f"
  }
  $appid = Join-Path $fo4 "steam_appid.txt"
  if (-not (Test-Path $appid)) {
    Set-Content $appid "377160" -Encoding ascii
    Write-Ok "steam_appid.txt"
  }
}

function Disable-DlcAndCreations([string]$fo4) {
  # Empty Creations list
  $ccc = Join-Path $fo4 "Fallout4.ccc"
  if (Test-Path $ccc) {
    if (-not (Test-Path "$ccc.fom_bak")) { Copy-Item $ccc "$ccc.fom_bak" -Force }
    Set-Content $ccc "" -Encoding ascii
    Write-Ok "Fallout4.ccc emptied (Creations off)"
  }
  $ccc2 = Join-Path $fo4 "Fallout4IDs.ccc"
  if (Test-Path $ccc2) {
    if (-not (Test-Path "$ccc2.fom_bak")) { Copy-Item $ccc2 "$ccc2.fom_bak" -Force }
    Set-Content $ccc2 "" -Encoding ascii
  }

  # Disable DLC archives/plugins that crash classic+NG mix
  $data = Join-Path $fo4 "Data"
  $prefixes = @("DLCCoast", "DLCNukaWorld", "DLCRobot", "DLCworkshop01", "DLCworkshop02", "DLCworkshop03")
  $n = 0
  foreach ($pre in $prefixes) {
    Get-ChildItem $data -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like "$pre*" -and $_.Name -notlike "*.disabled" } |
      ForEach-Object {
        Rename-Item $_.FullName ($_.FullName + ".disabled") -Force
        $n++
      }
  }
  # Creations BA2/ESL
  Get-ChildItem $data -File -ErrorAction SilentlyContinue |
    Where-Object { ($_.Name -like "cc*" -or $_.Name -like "cc*") -and $_.Name -notlike "*.disabled" } |
    ForEach-Object {
      Rename-Item $_.FullName ($_.FullName + ".disabled") -Force
      $n++
    }

  $local = Join-Path $env:LOCALAPPDATA "Fallout4"
  if (-not (Test-Path $local)) { New-Item -ItemType Directory -Path $local -Force | Out-Null }
  $dlcList = Join-Path $local "DLCList.txt"
  if (Test-Path $dlcList) {
    if (-not (Test-Path "$dlcList.fom_bak")) { Copy-Item $dlcList "$dlcList.fom_bak" -Force }
  }
  Set-Content $dlcList "" -Encoding ascii
  Write-Ok "DLCList emptied; disabled $n DLC/CC files (reversible *.disabled / *.fom_bak)"
}

function Write-FwConfig([string]$fo4, [string]$server, [string]$clientId) {
  $cfg = @"
server = $server
client_id = $clientId
ghost_map = player_B=0x1CA7D
log_level = debug
auto_load_save =
boot_proxy_only = 0
"@
  Set-Content (Join-Path $fo4 "fw_config.ini") $cfg -Encoding utf8
  Write-Ok "fw_config.ini → server=$server client_id=$clientId"
}

function Set-SteamNoAutoUpdate([string]$fo4) {
  # Find appmanifest near install
  $steamapps = Split-Path (Split-Path $fo4 -Parent) -Parent
  $acf = Join-Path $steamapps "appmanifest_377160.acf"
  if (-not (Test-Path $acf)) {
    Write-Warn "appmanifest_377160.acf not found — set FO4 update to 'Only when I launch' in Steam UI"
    return
  }
  $raw = Get-Content $acf -Raw
  if ($raw -match '"AutoUpdateBehavior"\s+"1"') {
    Write-Ok "Steam AutoUpdateBehavior already 1"
    return
  }
  if ($raw -match '"AutoUpdateBehavior"\s+"\d+"') {
    $raw = $raw -replace '"AutoUpdateBehavior"\s+"\d+"', '"AutoUpdateBehavior""1"'
    # vdf uses "key""value" sometimes without space
    $raw = $raw -replace '"AutoUpdateBehavior"\s+"\d+"', '"AutoUpdateBehavior"		"1"'
  }
  # simpler line-based
  $lines = Get-Content $acf
  $lines = $lines | ForEach-Object {
    if ($_ -match 'AutoUpdateBehavior') { "`t`"AutoUpdateBehavior`"`t`t`"1`"" } else { $_ }
  }
  Set-Content $acf $lines -Encoding ascii
  Write-Ok "Steam AutoUpdateBehavior=1 (won't auto-reupgrade)"
}

function Show-DepotHelp {
  Write-Host ""
  Write-Host "--------------------------------------------------------------" -ForegroundColor Yellow
  Write-Host " GAME IS NOT 1.10.163 — one manual Steam step (no Nexus):" -ForegroundColor Yellow
  Write-Host "--------------------------------------------------------------" -ForegroundColor Yellow
  Write-Host " 1. Steam → open console:  Win+R → steam://open/console"
  Write-Host " 2. Paste these lines one at a time:"
  Write-Host ""
  Write-Host "    download_depot 377160 377162 5847529232406005096" -ForegroundColor White
  Write-Host "    download_depot 377160 377161 7497069378349273908" -ForegroundColor White
  Write-Host "    download_depot 377160 377163 5819088023757897745" -ForegroundColor White
  Write-Host "    download_depot 377160 377164 2178106366609958945" -ForegroundColor White
  Write-Host ""
  Write-Host " 3. Wait for 'Depot download complete' for each."
  Write-Host " 4. Run this setup again (double-click FoM_Setup.bat)."
  Write-Host "--------------------------------------------------------------" -ForegroundColor Yellow
  $cmds = @"
download_depot 377160 377162 5847529232406005096
download_depot 377160 377161 7497069378349273908
download_depot 377160 377163 5819088023757897745
download_depot 377160 377164 2178106366609958945
"@
  try { Set-Clipboard -Value $cmds; Write-Ok "depot commands copied to clipboard" } catch {}
  try { Start-Process "steam://open/console" } catch {}
}

function Start-HostServer {
  $py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = "python" }
  if (-not (Test-Path (Join-Path $RepoRoot "net\server\main.py"))) {
    Write-Warn "server sources not next to this pack — start server manually from the FO4_Wrld repo"
    return
  }
  if (Get-NetUDPEndpoint -LocalPort 31337 -ErrorAction SilentlyContinue) {
    Write-Ok "UDP 31337 already listening"
    return
  }
  Start-Process $py -ArgumentList "-u","-m","net.server.main" -WorkingDirectory $RepoRoot -WindowStyle Minimized
  Start-Sleep 2
  Write-Ok "Python server started on 127.0.0.1:31337"
}

# --------------------- main ---------------------
Write-Host ""
Write-Host "  FalloutWorld multiplayer setup" -ForegroundColor Cyan
Write-Host "  No Nexus. Targets your FO4 folder only." -ForegroundColor DarkGray
Write-Host ""

if (-not $Fo4Dir) {
  $Fo4Dir = Find-Fo4Default
  if ($Fo4Dir) {
    Write-Host "Found FO4: $Fo4Dir"
    $ans = Read-Host "Use this folder? [Y/n]"
    if ($ans -match '^[nN]') { $Fo4Dir = "" }
  }
  if (-not $Fo4Dir) {
    Write-Host "Pick the folder that contains Fallout4.exe …"
    $Fo4Dir = Pick-Folder "Select Fallout 4 install folder (contains Fallout4.exe)"
  }
}

if (-not $Fo4Dir -or -not (Test-Path (Join-Path $Fo4Dir "Fallout4.exe"))) {
  Write-Fail "Need a valid FO4 folder with Fallout4.exe"
  exit 1
}
$Fo4Dir = (Resolve-Path $Fo4Dir).Path
Write-Ok "FO4 = $Fo4Dir"

if (-not (Test-Path $Payload)) {
  Write-Fail "payload\ folder missing next to FoM_Setup.ps1 (dxgi, f4se, xdelta)"
  exit 1
}

Write-Step "Game version"
$ver = Get-ExeVersion (Join-Path $Fo4Dir "Fallout4.exe")
Write-Host "  Fallout4.exe = $ver"
$needClassic = ($ver -notmatch '^1\.10\.163')

if ($needClassic) {
  Write-Warn "Need classic 1.10.163 (you have $ver)"
  Write-Step "Looking for already-downloaded classic depots"
  $applied = Apply-ClassicDepots $Fo4Dir
  if ($applied) {
    $ver = Get-ExeVersion (Join-Path $Fo4Dir "Fallout4.exe")
    Write-Host "  version now = $ver"
    $needClassic = ($ver -notmatch '^1\.10\.163')
  }
  if ($needClassic -and -not $SkipDepotPrompt) {
    Show-DepotHelp
    Write-Host ""
    Write-Host "Setup paused until classic depots are downloaded."
    Write-Host "Press Enter to exit…"
    [void][Console]::ReadLine()
    exit 2
  }
} else {
  Write-Ok "already 1.10.163"
  # still apply any missing content depots if present
  Apply-ClassicDepots $Fo4Dir | Out-Null
}

Write-Step "Classic steam_api64"
Patch-SteamApi $Fo4Dir | Out-Null

Write-Step "Install multiplayer payload (F4SE + dxgi)"
Install-Payload $Fo4Dir

Write-Step "Disable DLC / Creations (classic stability)"
Disable-DlcAndCreations $Fo4Dir

Write-Step "Steam update pin"
Set-SteamNoAutoUpdate $Fo4Dir

# Mode prompts
if ($Mode -eq "setup-only") {
  Write-Host ""
  Write-Host "Role for this PC?"
  Write-Host "  [1] Host (starts server, player_A)"
  Write-Host "  [2] Join (enter host IP, player_B)"
  Write-Host "  [3] Setup only (no launch)"
  $c = Read-Host "Choice [1/2/3]"
  switch ($c) {
    "1" { $Mode = "host"; $ClientId = "player_A"; $Server = "127.0.0.1:31337" }
    "2" {
      $Mode = "join"
      $ClientId = "player_B"
      $ip = Read-Host "Host LAN IP (e.g. 192.168.1.20)"
      if (-not $ip) { $ip = "127.0.0.1" }
      $Server = "${ip}:31337"
    }
    default { $Mode = "setup-only" }
  }
}

Write-Step "Write fw_config.ini"
Write-FwConfig $Fo4Dir $Server $ClientId

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " SETUP COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host " FO4:     $Fo4Dir"
Write-Host " Version: $(Get-ExeVersion (Join-Path $Fo4Dir 'Fallout4.exe'))"
Write-Host " SteamUser export: $(Test-SteamUserExport (Join-Path $Fo4Dir 'steam_api64.dll'))"
Write-Host " Mode:    $Mode  server=$Server  id=$ClientId"
Write-Host ""
Write-Host " Play: always launch with f4se_loader.exe (not Steam Play button)."
Write-Host " Host: start server first (or choose Host above)."
Write-Host ""

if ($Mode -eq "host" -or $Launch) {
  if ($Mode -eq "host") { Start-HostServer }
  $loader = Join-Path $Fo4Dir "f4se_loader.exe"
  if (Test-Path $loader) {
    Write-Step "Launching f4se_loader.exe"
    Start-Process $loader -WorkingDirectory $Fo4Dir
    Write-Ok "game started"
  }
} elseif ($Mode -eq "join") {
  $loader = Join-Path $Fo4Dir "f4se_loader.exe"
  if (Test-Path $loader) {
    Write-Step "Launching f4se_loader.exe (join $Server)"
    Start-Process $loader -WorkingDirectory $Fo4Dir
  }
}

Write-Host "Press Enter to close…"
[void][Console]::ReadLine()
