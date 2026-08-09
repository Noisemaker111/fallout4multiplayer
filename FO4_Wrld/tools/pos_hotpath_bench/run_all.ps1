# Run POS hot-path microbench across available languages.
# Usage: powershell -File run_all.ps1 [iters]
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Iters = if ($args[0]) { $args[0] } else { "500000" }
$OutDir = Join-Path $Root "results"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ResultFile = Join-Path $OutDir "bench_$Stamp.txt"

function Run-One($name, $scriptBlock) {
  Write-Host "`n==== $name ====" -ForegroundColor Cyan
  try {
    $out = & $scriptBlock 2>&1 | ForEach-Object { "$_" } | Out-String
    Write-Host $out
    Add-Content $ResultFile "`n==== $name ====`n$out"
  } catch {
    Write-Host "FAILED: $_" -ForegroundColor Red
    Add-Content $ResultFile "`n==== $name ====`nFAILED: $_"
  }
}

"POS hot-path bench  iters=$Iters  $(Get-Date -Format o)" | Set-Content $ResultFile
"Workload: 4 peers, decode POS(36B) + validate + encode bcast to 3 peers" | Add-Content $ResultFile

# --- Python ---
$py = "C:\Users\Jk101\Projects\fallout4multiplayer\FO4_Wrld\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Run-One "python" { & $py (Join-Path $Root "python\bench.py") $Iters }

# --- Rust ---
if (Get-Command rustc -EA SilentlyContinue) {
  $rustDir = Join-Path $Root "rust"
  Push-Location $rustDir
  cargo build --release 2>&1 | Out-Null
  Pop-Location
  $exe = Join-Path $rustDir "target\release\pos_hotpath_bench.exe"
  Run-One "rust" { & $exe $Iters }
}

# --- C++ ---
$gpp = Get-Command g++ -EA SilentlyContinue
if ($gpp) {
  $cppExe = Join-Path $Root "cpp\bench.exe"
  & g++ -O3 -std=c++17 -o $cppExe (Join-Path $Root "cpp\bench.cpp")
  Run-One "cpp" { & $cppExe $Iters }
}

# --- C ---
$gcc = Get-Command gcc -EA SilentlyContinue
if ($gcc) {
  $cExe = Join-Path $Root "c\bench.exe"
  & gcc -O3 -std=c11 -o $cExe (Join-Path $Root "c\bench.c") -lm
  Run-One "c" { & $cExe $Iters }
}

# --- Node ---
if (Get-Command node -EA SilentlyContinue) {
  Run-One "node" { & node (Join-Path $Root "node\bench.js") $Iters }
}

# --- Go ---
$go = Get-Command go -EA SilentlyContinue
if (-not $go) {
  # winget often installs but PATH not refreshed
  $cand = @(
    "$env:ProgramFiles\Go\bin\go.exe",
    "$env:LOCALAPPDATA\Programs\Go\bin\go.exe"
  )
  foreach ($c in $cand) { if (Test-Path $c) { $go = @{ Source = $c }; break } }
}
if ($go) {
  $goBin = if ($go.Source) { $go.Source } else { "go" }
  $goDir = Join-Path $Root "go"
  Push-Location $goDir
  & $goBin build -o bench.exe bench.go
  Pop-Location
  Run-One "go" { & (Join-Path $goDir "bench.exe") $Iters }
} else {
  Add-Content $ResultFile "`n==== go ====`nSKIPPED (go not installed)"
  Write-Host "go SKIPPED" -ForegroundColor Yellow
}

Write-Host "`nResults written to $ResultFile" -ForegroundColor Green
Get-Content $ResultFile
