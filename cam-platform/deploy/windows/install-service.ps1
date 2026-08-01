# Register CAM Studio to start at boot as a Windows Scheduled Task (runs whether or
# not a user is logged on). Uses only built-in Windows tooling — no NSSM/pywin32.
#
# Run from an ELEVATED PowerShell, from the repo root:
#   .\deploy\windows\install-service.ps1
#
# Prerequisite: the venv exists (run start-windows.bat once, or create it manually)
# and the web UI is built (frontend\dist). Production config (CAM_JWT_SECRET,
# CAM_DB_URL, provider keys, …) must be set as MACHINE-level environment variables,
# e.g.  setx CAM_JWT_SECRET "..." /M   — a SYSTEM task does not read user env vars.
#
# For a true Windows Service (SCM-managed), wrap scripts\run_stack.py with NSSM:
#   nssm install CAMStudio "<repo>\.venv\Scripts\python.exe" "scripts\run_stack.py"
#   nssm set CAMStudio AppDirectory "<repo>"

$ErrorActionPreference = "Stop"
$TaskName = "CAMStudio"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Py   = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
  throw "venv python not found at $Py. Run start-windows.bat once (or create the venv) first."
}

$Action    = New-ScheduledTaskAction -Execute $Py -Argument "scripts\run_stack.py" -WorkingDirectory $Root
$Trigger   = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
               -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
               -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
  -Principal $Principal -Settings $Settings -Description "CAM Studio platform (gateway + services)" -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (starts at boot)."
Write-Host "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Then browse:        http://localhost:8080"
