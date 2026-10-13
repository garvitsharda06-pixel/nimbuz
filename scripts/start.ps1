# Dalal Desk: one-command local start (Windows PowerShell).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1               # http://127.0.0.1:8000
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 8080
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 8000 -Demo   # synthetic only: gate bypass demo
#
# First run installs everything (Python 3.12 venv, UI build), generates the SYNTHETIC demo
# dataset and runs the research once. Binds 127.0.0.1 only.
param([ValidateRange(1, 65535)][int]$Port = 8000, [switch]$Demo)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Die($m) { Write-Host "Error: $m" -ForegroundColor Red; exit 1 }
# native commands don't stop on failure under ErrorActionPreference, so check the exit code
function Run([string]$what, [scriptblock]$cmd) { & $cmd; if ($LASTEXITCODE -ne 0) { Die "$what failed (exit $LASTEXITCODE)" } }

$Root = (Get-Location).Path
$Dalal = Join-Path $Root "backend\.venv\Scripts\dalal.exe"
$VenvPy = Join-Path $Root "backend\.venv\Scripts\python.exe"

# 1) backend
if (-not (Test-Path $Dalal)) {
  Say "Installing the backend (first run only)..."
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    Push-Location backend
    Run "uv venv" { uv venv --python 3.12 .venv }
    Run "backend install" { uv pip install --python .venv\Scripts\python.exe -e . }
    Pop-Location
  } elseif (Get-Command py -ErrorAction SilentlyContinue) {
    Run "python venv" { py -3.12 -m venv backend\.venv }
    Run "pip upgrade" { & $VenvPy -m pip install --upgrade pip }
    Run "backend install" { & $VenvPy -m pip install -e .\backend }
  } else {
    Die "need Python 3.12 or uv. Easiest: install uv (it fetches Python 3.12 for you):`n  powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`"  then re-run."
  }
}

# 2) frontend
if (-not (Test-Path "frontend\dist\index.html")) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Die "need Node.js 18+ to build the UI once: https://nodejs.org" }
  Say "Building the terminal UI (first run only)..."
  Push-Location frontend
  Run "npm ci" { npm ci --no-audit --no-fund }
  Run "UI build" { npx vite build }
  Pop-Location
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

# 4) serve (fail early with a clear message if the port is taken)
$probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
try { $probe.Start(); $probe.Stop() } catch { Die "port $Port is already in use. Pick another one, e.g.  scripts\start.ps1 -Port 8080" }
Say "Dalal Desk is starting -> open  http://127.0.0.1:$Port   (Ctrl+C to stop)"
if ($provider -eq "synthetic") { Say "Data is SYNTHETIC: pick a day in the Replay strip and press Start." }
$serveArgs = @("serve", "--port", "$Port")
if ($Demo) { $serveArgs += "--demo" }
& $Dalal @serveArgs
