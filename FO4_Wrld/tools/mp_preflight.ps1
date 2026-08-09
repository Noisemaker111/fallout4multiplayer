# Multiplayer preflight for FO4_Wrld (classic 1.10.163 + FW_MINIMAL).
# Run from repo:  powershell -File tools\mp_preflight.ps1
# Exit 0 = ready enough to start server + FO4; non-zero = fix listed issues.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "net\protocol.py"))) {
    $Root = (Get-Location).Path
}
Set-Location $Root

$script:fail = 0
function Ok([string]$m)  { Write-Host "[OK]  $m" -ForegroundColor Green }
function Warn([string]$m){ Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Bad([string]$m) {
    Write-Host "[FAIL] $m" -ForegroundColor Red
    $script:fail++
}

Write-Host "=== FO4_Wrld multiplayer preflight ===" -ForegroundColor Cyan
Write-Host "repo: $Root"
Write-Host ""

# --- Python / protocol ---
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Bad "venv python missing: $py"
    $py = "python"
} else {
    Ok "venv python: $py"
}

$protoPy = & $py -c "import sys; sys.path.insert(0,'net'); from protocol import PROTOCOL_VERSION; print(PROTOCOL_VERSION)" 2>$null
$protoH = $null
$hdr = Join-Path $Root "fw_native\src\net\protocol.h"
if (Test-Path $hdr) {
    $line = Select-String -Path $hdr -Pattern "PROTOCOL_VERSION\s*=\s*\d+" | Select-Object -First 1
    if ($line -and ($line.Line -match "=\s*(\d+)")) {
        $protoH = $Matches[1]
    }
}
if (("$protoPy" -eq "$protoH") -and $protoPy) {
    Ok "protocol version match: Python=$protoPy C++=$protoH"
} else {
    Bad "protocol mismatch: Python=$protoPy C++=$protoH"
}

# --- DLL build ---
$minDll = Join-Path $Root "fw_native\build-minimal\dxgi.dll"
$fullDll = Join-Path $Root "fw_native\build\dxgi.dll"
if (Test-Path $minDll) {
    $i = Get-Item $minDll
    Ok ("minimal DLL: {0} bytes, {1}" -f $i.Length, $i.LastWriteTime)
} else {
    Bad "missing fw_native\build-minimal\dxgi.dll - run: cd fw_native; .\build.bat --minimal"
}
if (Test-Path $fullDll) {
    $i = Get-Item $fullDll
    Warn ("full build DLL also present ({0}) - for MINIMAL co-op use deploy --minimal" -f $i.LastWriteTime)
}

# --- Game install Side A ---
$steam = "C:\Games\Steam\steamapps\common\Fallout 4"
if (-not (Test-Path $steam)) {
    $steam = "C:\Program Files (x86)\Steam\steamapps\common\Fallout 4"
}
if (Test-Path $steam) {
    Ok "Side A game dir: $steam"
    $exe = Join-Path $steam "Fallout4.exe"
    if (Test-Path $exe) {
        $ver = (Get-Item $exe).VersionInfo.FileVersion
        if ($ver -like "1.10.163*") {
            Ok "Fallout4.exe version: $ver"
        } else {
            Bad "Fallout4.exe version is '$ver' - need 1.10.163.0 (classic)"
        }
    } else {
        Bad "Fallout4.exe missing in $steam"
    }
    $dep = Join-Path $steam "dxgi.dll"
    if (Test-Path $dep) {
        $d = Get-Item $dep
        $match = $false
        if (Test-Path $minDll) {
            $m = Get-Item $minDll
            if ($d.Length -eq $m.Length) { $match = $true }
        }
        if ($match) {
            Ok ("deployed dxgi.dll size matches minimal build ({0} bytes)" -f $d.Length)
        } else {
            Warn ("deployed dxgi.dll may be stale ({0} bytes, {1}) - run: cd fw_native; .\deploy.bat --minimal" -f $d.Length, $d.LastWriteTime)
        }
    } else {
        Bad "dxgi.dll not in game dir - run deploy.bat --minimal"
    }
    $cfg = Join-Path $steam "fw_config.ini"
    if (Test-Path $cfg) {
        Ok "fw_config.ini present"
        Get-Content $cfg | Select-Object -First 8 | ForEach-Object { Write-Host "       $_" }
    } else {
        Warn "fw_config.ini missing - launcher will write it, or create manually"
    }
    if (Test-Path (Join-Path $steam "f4se_loader.exe")) {
        Ok "f4se_loader.exe present"
    } else {
        Warn "f4se_loader.exe missing - Steam can still launch FO4; dxgi proxy loads via Fallout4.exe"
    }
} else {
    Bad "Steam FO4 directory not found"
}

# --- Side B ---
$fo4b = Join-Path $Root "FO4_b"
if (Test-Path $fo4b) {
    Ok "Side B FO4_b present: $fo4b"
    if (Test-Path (Join-Path $fo4b "dxgi.dll")) {
        Ok "Side B dxgi.dll present"
    } else {
        Warn "Side B missing dxgi.dll - redeploy"
    }
} else {
    Warn "FO4_b not present - same-PC two-client needs FO4_b (or use two machines)"
}

# --- Processes / port ---
$fo4 = Get-Process -Name "Fallout4" -ErrorAction SilentlyContinue
if ($fo4) {
    Warn ("Fallout4 already running (PID {0}) - exit before deploy or DLL copy fails" -f ($fo4.Id -join ","))
} else {
    Ok "no Fallout4.exe running"
}

$portBusy = $false
try {
    $c = Get-NetUDPEndpoint -LocalPort 31337 -ErrorAction SilentlyContinue
    if ($c) { $portBusy = $true }
} catch {}
if ($portBusy) {
    Warn "UDP 31337 already bound - server may already be running (OK if intentional)"
} else {
    Ok "UDP 31337 free (server not running yet)"
}

# --- Quick protocol unit tests (subset) ---
Write-Host ""
Write-Host "=== pytest narrative subset ===" -ForegroundColor Cyan
& $py -m pytest net/tests/test_item_grant.py net/tests/test_relationship_timepass.py net/tests/test_quest_sync.py -q --tb=line
if ($LASTEXITCODE -ne 0) {
    Bad "pytest failed"
} else {
    Ok "narrative wire tests passed"
}

Write-Host ""
if ($script:fail -eq 0) {
    Write-Host "PREFLIGHT PASSED - ready to multiplayer-test." -ForegroundColor Green
    Write-Host "1. start_server.bat"
    Write-Host "2. launch FO4 1.10.163 (deployed dxgi) - load world"
    Write-Host "3. second client (FO4_b or other machine) with matching DLL + fw_config client_id"
    Write-Host "4. see docs/MP_TEST_RUNBOOK.md for smoke checklist"
    exit 0
} else {
    Write-Host ("PREFLIGHT FAILED ({0} issue(s)) - fix above before testing." -f $script:fail) -ForegroundColor Red
    exit 1
}
