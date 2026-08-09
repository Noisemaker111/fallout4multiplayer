# tools/smoke_single_client.ps1 — single-client FO4 smoke for FO4_Wrld
param(
  [string]$GameDir = "C:\Games\Steam\steamapps\common\Fallout 4",
  [string]$Repo = "C:\Users\Jk101\Projects\fallout4multiplayer\FO4_Wrld",
  [int]$WaitSec = 90
)
$ErrorActionPreference = "Continue"
$py = Join-Path $Repo ".venv\Scripts\python.exe"
$log = Join-Path $GameDir "fw_native.log"
$f4se = Join-Path $GameDir "f4se_loader.exe"

# ensure config
@"
server = 127.0.0.1:31337
client_id = player_A
ghost_map = player_B=0x1CA7D
log_level = debug
auto_load_save =
"@ | Set-Content (Join-Path $GameDir "fw_config.ini") -Encoding utf8

# server
$udp = Get-NetUDPEndpoint -LocalPort 31337 -ErrorAction SilentlyContinue
if (-not $udp) {
  Start-Process $py -ArgumentList "-m","net.server.main" -WorkingDirectory $Repo -WindowStyle Minimized
  Start-Sleep 2
}

Get-Process Fallout4 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1
if (Test-Path $log) { Remove-Item $log -Force }

Start-Process $f4se -WorkingDirectory $GameDir
$deadline = (Get-Date).AddSeconds($WaitSec)
while ((Get-Date) -lt $deadline) {
  $fo = @(Get-Process Fallout4 -ErrorAction SilentlyContinue)
  $len = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
  if ($len -gt 400) { break }
  if ($fo.Count -eq 0 -and ((Get-Date) -gt (Get-Date).AddSeconds(15))) { break }
  Start-Sleep 2
}

if (-not (Test-Path $log)) { Write-Output "FAIL: no fw_native.log"; exit 2 }
$text = Get-Content $log -Raw
$checks = [ordered]@{
  version_ok = $text -match "version: Fallout4.exe = 1\.10\.163\.0.*OK"
  port_ready_inert = $text -match "PORT_READY=false"
  minhook = $text -match "MinHook initialized"
  welcome = $text -match "WELCOME"
  hello = $text -match "HELLO"
  main_menu_fail = $text -match "main_menu.*FAILED"
  crash = $text -match "\[crash-veh\]"
  scene_hook = $text -match "scene_hook.*installed"
}
foreach ($k in $checks.Keys) { Write-Output ("{0}={1}" -f $k, $checks[$k]) }
if ($checks.version_ok -and $checks.minhook -and $checks.welcome -and -not $checks.port_ready_inert) {
  Write-Output "SMOKE_PARTIAL_PASS (version+hooks+net WELCOME)"
  exit 0
}
Write-Output "SMOKE_ISSUES"
exit 1
