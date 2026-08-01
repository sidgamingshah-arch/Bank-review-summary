# Remove the CAM Studio boot task registered by install-service.ps1.
# Run from an ELEVATED PowerShell:  .\deploy\windows\uninstall-service.ps1
$ErrorActionPreference = "Stop"
$TaskName = "CAMStudio"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'."
