# Agent Control Portable Package

## Requirements

- Windows PowerShell
- Python 3.11+ in PATH

## Install

Unzip the package, open PowerShell in the extracted folder, then run:

```powershell
.\packaging\install.ps1
```

## Start UI

```powershell
.\packaging\run-ui.ps1 -Port 8767
```

Open:

```text
http://127.0.0.1:8767
```

## Register A Dialog As Worker

```powershell
.\packaging\register-worker.ps1 `
  -WorkerId ui-1 `
  -Project "New project2" `
  -Dialog "UI improvements" `
  -Capabilities "frontend,python,tests"
```

## Data Location

By default, runtime data is stored outside the app folder:

```text
%USERPROFILE%\.agent_control\state.sqlite3
```

This package does not include the original machine's `.agent_control` runtime state.

