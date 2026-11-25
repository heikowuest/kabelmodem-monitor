# Beendet das im Hintergrund laufende Modem-Dashboard.
# (Beenden in der Aufgabenplanung stoppt nur die PowerShell-Hülle, nicht Python.)
Stop-ScheduledTask -TaskName 'Kabelmodem-Dashboard' -ErrorAction SilentlyContinue
$p = Get-CimInstance Win32_Process -Filter "Name like 'py%'" |
    Where-Object { $_.CommandLine -match 'modem_dashboard\.py' }
if ($p) {
    $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
    Write-Host "Dashboard beendet ($(@($p).Count) Prozess(e))."
} else {
    Write-Host 'Dashboard lief nicht.'
}
