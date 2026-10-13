# Dalal Desk: one-command local start (Windows PowerShell 5.1+).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1                # first free port from 8000
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 8080     # exactly this port
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Demo          # synthetic only: gate bypass demo
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Open          # also open the browser
#
# First run installs everything (Python 3.12 venv, UI build if not prebuilt), generates the
# SYNTHETIC demo dataset and runs the research once. Binds 127.0.0.1 only.
param([ValidateRange(0, 65535)][int]$Port = 0, [switch]$Demo, [switch]$Open)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "Note: $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "Error: $m" -ForegroundColor Red; exit 1 }
# native commands don't stop on failure under ErrorActionPreference, so check the exit code
function Run([string]$what, [scriptblock]$cmd) { & $cmd; if ($LASTEXITCODE -ne 0) { Die "$what failed (exit $LASTEXITCODE)" } }
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

$Root = (Get-Location).Path
$Dalal = Join-Path $Root "backend\.venv\Scripts\dalal.exe"
$VenvPy = Join-Path $Root "backend\.venv\Scripts\python.exe"
$Stamp = Join-Path $Root "backend\.venv\.dalal-installed"
$Pyproject = Join-Path $Root "backend\pyproject.toml"
$env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"   # where the uv installer puts it

# 0) uv (fetches Python 3.12 for us) unless Python 3.12 is already installed
$hasPy312 = $false
if (Has py) {   # PowerShell 5.1 turns redirected stderr into a terminating error under "Stop"
  try { & py -3.12 --version *> $null; $hasPy312 = ($LASTEXITCODE -eq 0) } catch { $hasPy312 = $false }
}
if (-not (Test-Path $Dalal) -and -not (Has uv) -and -not $hasPy312) {
  Write-Host "Dalal Desk needs Python 3.12. The easiest way is 'uv', a small installer from astral.sh"
  Write-Host "that downloads Python 3.12 into your user folder (nothing system-wide)."
  $ans = Read-Host "Install uv now? [Y/n]"
  if ($ans -eq "" -or $ans -match '^[Yy]') {
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if (-not (Has uv)) { Die "uv was installed but is not on PATH yet: close this window and run again" }
  } else {
    Die "install uv (https://docs.astral.sh/uv/) or Python 3.12, then run this again"
  }
}

# 1) backend; reinstalls when backend\pyproject.toml changes
$needInstall = (-not (Test-Path $Dalal)) -or (-not (Test-Path $Stamp))
if (-not $needInstall) { $needInstall = (Get-Item $Pyproject).LastWriteTime -gt (Get-Item $Stamp).LastWriteTime }
if ($needInstall) {
  Say "Installing the backend (first run, or its dependencies changed; a few minutes)..."
  if (Has uv) {
    Push-Location backend
    if (-not (Test-Path $VenvPy)) { Run "uv venv" { uv venv --python 3.12 .venv } }
    Run "backend install" { uv pip install --python $VenvPy -e . }
    Pop-Location
  } else {
    if (-not (Test-Path $VenvPy)) { Run "python venv" { py -3.12 -m venv backend\.venv } }
    Run "pip upgrade" { & $VenvPy -m pip install --upgrade pip }
    Run "backend install" { & $VenvPy -m pip install -e .\backend }
  }
  Set-Content -Path $Stamp -Value (Get-Date -Format o)
}

# 2) frontend: served from frontend\dist (prebuilt in the desktop package, else built once with Node 18+)
$dist = Join-Path $Root "frontend\dist\index.html"
if (-not (Test-Path $dist)) {
  if (-not (Has npm)) { Die "the UI is not built and Node.js is missing. Install Node.js 18+ (https://nodejs.org) and run this again" }
  Say "Building the terminal UI (first run only)..."
  Push-Location frontend
  Run "npm ci" { npm ci --no-audit --no-fund }
  Run "UI build" { npx vite build }
  Pop-Location
} else {
  $built = (Get-Item $dist).LastWriteTime
  $newer = @(Get-ChildItem -Recurse -File "frontend\src", "frontend\index.html" | Where-Object { $_.LastWriteTime -gt $built }).Count
  if ($newer -gt 0) {
    if (Has npm) {
      Say "UI sources changed: rebuilding the terminal UI..."
      Push-Location frontend
      if (-not (Test-Path "node_modules")) { Run "npm ci" { npm ci --no-audit --no-fund } }
      Run "UI build" { npx vite build }
      Pop-Location
    } else {
      Warn "UI sources are newer than frontend\dist but Node.js is missing, so the existing build is used."
    }
  }
}

# 3) data + research
$provider = "synthetic"
$inData = $false
foreach ($line in Get-Content "config\settings.yaml") {
  if ($line -match '^data:') { $inData = $true; continue }
  if ($inData -and $line -match '^\s+provider:\s*(\S+)') { $provider = $Matches[1]; break }
}
if ($provider -eq "synthetic" -and -not (Test-Path "data\synthetic\instruments.json")) {
  Say "Generating the SYNTHETIC demo dataset (plumbing only, never market data)..."
  Run "dalal synth" { & $Dalal synth }
}
if ($provider -eq "upstox" -and -not (Test-Path "data\upstox\instruments.json")) {
  Die "provider is upstox but no data yet: put UPSTOX_ANALYTICS_TOKEN in .env, then run  $Dalal verify ; $Dalal download-data"
}
if (-not (Test-Path "data\$provider\research\statsbook.json")) {
  Say "Running the walk-forward research once (a few minutes)..."
  Run "dalal backtest" { & $Dalal backtest }
}

# 4) serve: an explicit -Port must be free; the default moves to the next free one
function PortFree([int]$p) {
  $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $p)
  try { $l.Start(); $l.Stop(); return $true } catch { return $false }
}
if ($Port -ne 0) {
  if (-not (PortFree $Port)) { Die "port $Port is already in use. Pick another one, e.g.  scripts\start.ps1 -Port 8080" }
} else {
  $Port = 8000
  while (-not (PortFree $Port)) {
    $Port++
    if ($Port -gt 8050) { Die "ports 8000-8050 are all in use. Pick one, e.g.  scripts\start.ps1 -Port 9000" }
  }
  if ($Port -ne 8000) { Warn "port 8000 is busy, using $Port instead." }
}
Say "Dalal Desk is starting -> open  http://127.0.0.1:$Port   (Ctrl+C to stop)"
if ($provider -eq "synthetic") { Say "Data is SYNTHETIC: pick a day in the Replay strip and press Start." }
$serveArgs = @("serve", "--port", "$Port")
if ($Demo) { $serveArgs += "--demo" }
if ($Open) { $serveArgs += "--open" }
& $Dalal @serveArgs
