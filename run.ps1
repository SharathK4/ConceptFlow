# ConceptFlow AI - one-command launcher for Windows (PowerShell).
#
#   .\run.ps1            build the page if needed, then serve everything on http://127.0.0.1:8010
#   .\run.ps1 -Dev       API on :8010 plus the Vite dev server on :5179 (hot reload)
#   .\run.ps1 -Setup     create .venv, install Python and npm dependencies, create .env
param(
    [switch]$Dev,
    [switch]$Setup
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if ($Setup -or -not (Test-Path $python)) {
    Write-Host 'Creating the Python environment (.venv)...'
    python -m venv (Join-Path $root '.venv')
    & $python -m pip install --upgrade pip
    & $python -m pip install -r (Join-Path $root 'requirements.txt')
    Push-Location (Join-Path $root 'frontend'); npm install; Pop-Location
    if (-not (Test-Path (Join-Path $root '.env'))) {
        Copy-Item (Join-Path $root '.env.example') (Join-Path $root '.env')
        Write-Host 'Created .env - add your API keys there (all optional; without them the studio runs in replay mode).'
    }
    if ($Setup) { return }
}

if ($Dev) {
    Write-Host 'API    -> http://127.0.0.1:8010'
    Write-Host 'Studio -> http://127.0.0.1:5179'
    $api = Start-Process -PassThru -NoNewWindow $python -ArgumentList '-m', 'uvicorn', 'conceptflow.server:app', '--app-dir', (Join-Path $root 'backend'), '--host', '127.0.0.1', '--port', '8010', '--reload', '--reload-dir', (Join-Path $root 'backend')
    try {
        Push-Location (Join-Path $root 'frontend'); npm run dev -- --host 127.0.0.1 --port 5179 --strictPort
    }
    finally {
        Pop-Location
        Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
    }
    return
}

if (-not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) {
    Write-Host 'Building the page...'
    Push-Location (Join-Path $root 'frontend'); npm run build; Pop-Location
}
Write-Host 'ConceptFlow -> http://127.0.0.1:8010'
& $python -m uvicorn conceptflow.server:app --app-dir (Join-Path $root 'backend') --host 127.0.0.1 --port 8010
