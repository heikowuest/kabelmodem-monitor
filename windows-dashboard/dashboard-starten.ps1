# Startet das Modem-Dashboard ohne Rückfrage (für Autostart über die Aufgabenplanung).
# Liest die mit zugang-speichern.ps1 gespeicherten Zugangsdaten.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot 'dashboard.log'
$zugang = Join-Path $PSScriptRoot 'zugang.xml'
if (-not (Test-Path $zugang)) {
    "$(Get-Date -Format s) zugang.xml fehlt - bitte zugang-speichern.ps1 ausführen." | Out-File $log -Encoding utf8
    exit 1
}
$cred = Import-Clixml $zugang
$env:MODEM_USER = $cred.UserName
$env:MODEM_PASS = $cred.GetNetworkCredential().Password
$env:PYTHONIOENCODING = 'utf-8'
"$(Get-Date -Format s) Start" | Out-File $log -Encoding utf8
# Umleitung über cmd, damit das Log einheitlich UTF-8 bleibt
cmd /c "py -u modem_dashboard.py >> dashboard.log 2>&1"
