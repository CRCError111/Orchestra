param(
    [string]$InstallDir = "$PSScriptRoot\..",
    [string]$DataDir = "$env:USERPROFILE\.agent_control",
    [int]$Port = 8767
)

$ErrorActionPreference = "Stop"

function Find-Python {
    $candidates = @("python", "py")
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            return $candidate
        }
    }
    throw "Python was not found in PATH. Install Python 3.11+ first."
}

$python = Find-Python
$db = Join-Path $DataDir "state.sqlite3"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Push-Location $InstallDir
try {
    & $python -m agent_control.cli --db $db init
    Write-Host "Agent Control installed."
    Write-Host "InstallDir: $InstallDir"
    Write-Host "DataDir: $DataDir"
    Write-Host "Run UI: .\packaging\run-ui.ps1 -DataDir `"$DataDir`" -Port $Port"
}
finally {
    Pop-Location
}

