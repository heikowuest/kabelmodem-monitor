# Speichert die Modem-Zugangsdaten verschlüsselt (Windows DPAPI).
# Die Datei zugang.xml ist nur mit deinem Windows-Konto auf diesem PC lesbar.
# Erneut ausführen, wenn sich das Modem-Passwort ändert.
$ziel = Join-Path $PSScriptRoot 'zugang.xml'
$cred = Get-Credential -Message 'Zugangsdaten für das Kabelmodem (192.168.100.1)'
if (-not $cred) { Write-Host 'Abgebrochen.'; exit 1 }
$cred | Export-Clixml -Path $ziel
Write-Host "Gespeichert: $ziel"
