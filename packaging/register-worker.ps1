param(
    [Parameter(Mandatory = $true)]
    [string]$WorkerId,

    [Parameter(Mandatory = $true)]
    [string]$Project,

    [Parameter(Mandatory = $true)]
    [string]$Dialog,

    [string]$Capabilities = "python,frontend,tests,docs,orchestrator",
    [string]$InstallDir = "$PSScriptRoot\..",
    [string]$DataDir = "$env:USERPROFILE\.agent_control"
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
    & $python.Source -m agent_control.cli --db $db worker-register --id $WorkerId --project $Project --dialog $Dialog --label $Dialog --cap $Capabilities
}
finally {
    Pop-Location
}

