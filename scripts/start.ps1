param([int]$Port = 5088)
$ErrorActionPreference = 'Stop'
$canvasRoot = [System.IO.Directory]::GetParent($PSScriptRoot).FullName
Set-Location -LiteralPath $canvasRoot
$localPython = Join-Path $canvasRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) {
    $canvasPython = $localPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        Write-Host 'Python 3.10+ is required. Install Python and add it to PATH.'
        exit 1
    }
    $canvasPython = $pythonCommand.Source
}
try {
    $existing = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($existing.app -eq 'standalone-creative-canvas') {
        Start-Process "http://127.0.0.1:$Port/"
        exit 0
    }
    Write-Host "Port $Port is occupied. Try: .\start.ps1 -Port 5089"
    exit 1
} catch {
    # No canvas server is running; start it below.
}
& $canvasPython -X utf8 -c 'import flask, dotenv, requests, PIL' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Install dependencies first:'
    Write-Host 'python -m venv .venv'
    Write-Host '.\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}
Write-Host "Huaihua MV: http://127.0.0.1:$Port/"
Write-Host 'Close this window or press Ctrl+C to stop.'
& $canvasPython -X utf8 (Join-Path $canvasRoot 'backend\app.py') --port $Port --open
exit $LASTEXITCODE
