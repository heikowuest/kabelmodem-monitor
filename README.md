# kabelmodem-monitor

DOCSIS-Monitoring für das Kabelmodem **Sagemcom F@st 3893** (z. B. am Vodafone-Kabelanschluss
im Bridge-Modus), ähnlich der Kabelansicht einer Fritzbox: Pegel, SNR/MER, Codewort-Fehler
und Ereignisprotokoll je Kanal, bewertet nach der verbreiteten BK-Grenzwerttabelle.

Das Modem liefert seine Werte über eine Broadcom-Weboberfläche unter `http://192.168.100.1`
(HTTP Basic Auth). Es gibt zwei Varianten:

| Ordner | Wofür |
|---|---|
| [`linux-collector/`](linux-collector/) | **Empfohlen.** systemd-Dienst, schreibt in InfluxDB 1.x; Anzeige in Grafana |
| [`grafana/`](grafana/) | Fertiges Grafana-Dashboard (InfluxQL) und das Skript, das es erzeugt |
| [`windows-dashboard/`](windows-dashboard/) | Eigenständiges lokales Web-Dashboard (Python + SQLite) mit Autostart unter Windows |
| [`tools/`](tools/) | `modem_erkunden.py`: lädt die Weboberfläche herunter, um neue Seiten/Werte zu finden |

Alles nutzt nur die Python-Standardbibliothek (ab 3.8).

## Ausgelesene Werte

- **Downstream** je Kanal (SC-QAM und OFDM): Frequenz, Pegel (dBmV), SNR/MER (dB),
  korrigierte und unkorrigierbare Codewörter, Lock-Status
- **Upstream** je Kanal (SC-QAM und OFDMA): Frequenz, Sendepegel (dBmV), Kanalbreite, Lock-Status
- **Modem**: Netzzugang, Uptime, Boardtemperatur, Firmware
- **Ereignisprotokoll**: Level, Anzahl, Zeitpunkt, Meldung (T3/T4-Timeouts usw.)

Seiten des Modems: `/cgi-bin/cmconnectionstatus.php` (Kanäle, braucht ~15 s),
`/cgi-bin/cmswinfo.php`, `/cgi-bin/info.php`, `/cgi-bin/CmEventLog.php`.

## Bewertung (BK-Grenzwerte)

Farbstufen nach der Tabelle „Grenzwerte digital (DVB-C und HSI/Phone) in BK-Netzen“
(Stand 2018-06-17): *Regel-/Vorgabekonform*, *Tolerierte Abweichung*,
*Beseitigung binnen Monatsfrist*, *Sofortige Beseitigung*.

| Wert | Bewertet als | konform |
|---|---|---|
| Downstream-Pegel SC-QAM | 256QAM | −3,9 … 13,0 dBmV |
| Downstream-Pegel OFDM | 1024QAM | −1,9 … 15,0 dBmV |
| SNR SC-QAM | 256QAM | ≥ 33,1 dB |
| MER OFDM | 1024QAM | ≥ 39,1 dB |
| Upstream-Sendepegel SC-QAM | DOCSIS-3.1-Modem | 44,1 … 47,0 dBmV |
| Upstream-Sendepegel OFDMA | DOCSIS-3.1-Modem, 6 dB verschoben | 38,1 … 41,0 dBmV |

Das Modem meldet die QAM-Stufe des OFDM-Kanals nicht; 1024QAM ist eine Annahme
(im Windows-Dashboard über `OFDM_QAM` in `dashboard.html` änderbar).
Der OFDMA-Sendepegel ist auf 1,6 MHz Bandbreite bezogen (SC-QAM: 6,4 MHz) und liegt daher
rund 6 dB niedriger; die Tabelle hat dafür keine eigene Spalte, deshalb werden die Grenzen
der DOCSIS-3.1-Spalte um 6 dB verschoben angewendet.
Korrigierte Codewörter werden nur für SC-QAM ausgewertet: Auf OFDM korrigiert LDPC
ständig Millionen Codewörter, das ist normal.

## Schnellstart

**Linux + InfluxDB + Grafana:** siehe [`linux-collector/INSTALL.md`](linux-collector/INSTALL.md).

**Windows:**

```powershell
cd windows-dashboard
.\zugang-speichern.ps1        # Modem-Login verschlüsselt (DPAPI) in zugang.xml speichern
.\autostart-einrichten.ps1    # Aufgabe "Kabelmodem-Dashboard" anlegen und starten
# -> http://localhost:8089     beenden: .\dashboard-beenden.ps1
```

Oder einmalig ohne Autostart: `py modem_dashboard.py` (fragt den Login ab).

## Lizenz

MIT, siehe [LICENSE](LICENSE).

## Hinweise

- Zugangsdaten gehören nie ins Repository: `zugang.xml`, `*.env`, Datenbanken und
  Modem-Dumps sind per `.gitignore` ausgeschlossen.
- Getestet mit Firmware `FAST3893_SSC_23.1_5.62.0`. Andere Broadcom-basierte Modems mit
  derselben Oberfläche funktionieren vermutlich ebenfalls; der Parser erkennt die Spalten
  anhand der Tabellenüberschriften.
