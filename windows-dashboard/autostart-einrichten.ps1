# Legt die Aufgabe "Kabelmodem-Dashboard" in der Windows-Aufgabenplanung an:
# Start bei Anmeldung, unsichtbar, Neustart nach Absturz. Vorher zugang-speichern.ps1 ausführen.
$starter = Join-Path $PSScriptRoot 'dashboard-starten.ps1'
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$starter`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Kabelmodem-Dashboard' -Force `
    -Description 'Startet das lokale Kabelmodem-Dashboard (http://localhost:8089)' `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName 'Kabelmodem-Dashboard'
Write-Host 'Aufgabe angelegt und gestartet: http://localhost:8089'
