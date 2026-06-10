param(
    [string]$InstallDir = "$PSScriptRoot\..",
    [string]$DataDir = "$env:USERPROFILE\.agent_control",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8767
)

$ErrorActionPreference = "Stop"

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    $python = (Get-Command py -ErrorAction SilentlyContinue)
}
if (-not $python) {
    throw "Python was not found in PATH. Install Python 3.11+ first."
}

$db = Join-Path $DataDir "state.sqlite3"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Push-Location $InstallDir
try {
    & $python.Source -m agent_control.cli --db $db ui --host $HostName --port $Port
}
finally {
    Pop-Location
}

