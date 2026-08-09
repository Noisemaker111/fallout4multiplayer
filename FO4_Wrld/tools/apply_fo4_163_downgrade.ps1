# FO4 1.10.163 downgrade apply script
# Run AFTER steam console depot downloads finish.
# Depots land in: <Steam>\steamapps\content\app_377160\depot_XXXXXX\

$ErrorActionPreference = "Stop"
$steamLibs = @(
  "C:\Program Files (x86)\Steam",
  "C:\Games\Steam"
)
$fo4 = "C:\Games\Steam\steamapps\common\Fallout 4"
$depots = @{
  "377162" = "Fallout4.exe (critical)"
  "377161" = "content_a"
  "377163" = "content_b"
  "377164" = "english"
}

function Find-DepotDir($id) {
  foreach ($lib in $steamLibs) {
    $p = Join-Path $lib "steamapps\content\app_377160\depot_$id"
    if (Test-Path $p) { return $p }
  }
  return $null
}

Write-Host "Looking for downloaded depots..."
$found = @{}
foreach ($id in $depots.Keys) {
  $d = Find-DepotDir $id
  if ($d) {
    Write-Host "  OK depot_$id ($($depots[$id])) -> $d"
    $found[$id] = $d
  } else {
    Write-Host "  MISSING depot_$id ($($depots[$id]))"
  }
}

if (-not $found.ContainsKey("377162")) {
  Write-Host "ERROR: exe depot 377162 not found. Finish download_depot in Steam console first."
  exit 1
}

# Backup current next-gen exe
$bak = Join-Path $fo4 "Fallout4.exe.1.11.bak"
if (-not (Test-Path $bak)) {
  Copy-Item (Join-Path $fo4 "Fallout4.exe") $bak -Force
  Write-Host "Backed up current exe to Fallout4.exe.1.11.bak"
}

# Copy each found depot over FO4 (robocopy for content)
foreach ($id in $found.Keys) {
  $src = $found[$id]
  Write-Host "Copying depot_$id from $src ..."
  & robocopy $src $fo4 /E /XO /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
  Write-Host "  done (robocopy exit $LASTEXITCODE)"
}

# Classic steam_api64 (flat SteamUser/SteamApps/SteamFriends). Next-gen
# steam_api only exports SteamAPI_* — 1.10.163 exe dies at entry with
# "SteamUser could not be located". FO4 Downgrader ships an xdelta3
# patch; apply it if present and current DLL lacks SteamUser.
$py = "C:\Users\Jk101\Projects\fallout4multiplayer\FO4_Wrld\.venv\Scripts\python.exe"
$api = Join-Path $fo4 "steam_api64.dll"
$apiBak = Join-Path $fo4 "steam_api64_downgradeBackup.dll"
$apiPatch = Join-Path $fo4 "patch_steam_api_diff.vcdiff"
$xdelta = Join-Path $fo4 "xdelta3.exe"
$hasSteamUser = $false
if (Test-Path $api) {
  $hasSteamUser = (& $py -c "import pefile; pe=pefile.PE(r'$api'); print(any(e.name and e.name.decode()=='SteamUser' for e in pe.DIRECTORY_ENTRY_EXPORT.symbols))" 2>$null) -eq "True"
}
if (-not $hasSteamUser -and (Test-Path $xdelta) -and (Test-Path $apiPatch)) {
  if (-not (Test-Path $apiBak) -and (Test-Path $api)) {
    Copy-Item $api $apiBak -Force
  }
  if (Test-Path $apiBak) {
    Write-Host "Patching steam_api64.dll to classic (SteamUser) via xdelta3..."
    if (Test-Path $api) { Remove-Item $api -Force }
    & $xdelta -d -vfs $apiBak $apiPatch $api
    if ($LASTEXITCODE -ne 0) {
      Write-Host "WARNING: xdelta3 steam_api patch failed (exit $LASTEXITCODE)"
    } else {
      Write-Host "  steam_api64.dll patched ($((Get-Item $api).Length) bytes)"
    }
  }
} elseif ($hasSteamUser) {
  Write-Host "steam_api64.dll already has SteamUser export"
} else {
  Write-Host "WARNING: no classic steam_api64 and no xdelta patch present"
}

# Verify version
$ver = & $py -c "import pefile; pe=pefile.PE(r'$fo4\Fallout4.exe'); fi=pe.VS_FIXEDFILEINFO[0]; print(f'{(fi.FileVersionMS>>16)&0xFFFF}.{fi.FileVersionMS&0xFFFF}.{(fi.FileVersionLS>>16)&0xFFFF}.{fi.FileVersionLS&0xFFFF}')"
Write-Host "Fallout4.exe version now: $ver"
if ($ver -ne "1.10.163.0") {
  Write-Host "WARNING: expected 1.10.163.0 — exe depot may not have been applied correctly"
  exit 2
}
Write-Host "SUCCESS: FO4 is 1.10.163.0"
Write-Host "Set Steam update to 'Only update when I launch' to avoid re-upgrade."
