# Linux-Collector: Kabelmodem → InfluxDB 1.x → Grafana

Der Collector fragt das Modem jede Minute ab und schreibt die DOCSIS-Werte in die
InfluxDB-Datenbank `modem`. Nur Python-Standardbibliothek (ab 3.8), kein pip nötig.

| Datei | Ziel auf dem Server |
|---|---|
| `modem_collector.py` | `/opt/modem-collector/modem_collector.py` |
| `modem-collector.service` | `/etc/systemd/system/modem-collector.service` |
| `modem-collector.env.example` | `/etc/modem-collector.env` (mit echten Zugangsdaten, `chmod 600`) |
| `../grafana/grafana-dashboard.json` | in Grafana importieren |

## 1. Voraussetzungen prüfen

```bash
python3 --version                                                    # 3.8 oder neuer
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.100.1/        # erwartet: 401
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8086/ping   # erwartet: 204
```

Kommt beim Modem `000` statt `401`, erreicht der Server das Modem nicht (Routing/Firewall prüfen).

## 2. Datenbank anlegen (empfohlen: eigene DB mit Aufbewahrungsdauer)

```bash
influx -execute 'CREATE DATABASE modem'
influx -execute 'ALTER RETENTION POLICY "autogen" ON "modem" DURATION 400d SHARD DURATION 7d DEFAULT'
```

Bei aktiver InfluxDB-Authentifizierung zusätzlich einen Schreib-Benutzer anlegen
(`CREATE USER modem_writer WITH PASSWORD '...'`, `GRANT WRITE ON modem TO modem_writer`)
und ihn als `INFLUX_USER`/`INFLUX_PASS` eintragen.

## 3. Installieren

```bash
git clone https://github.com/heikowuest/kabelmodem-monitor.git /tmp/kabelmodem-monitor
cd /tmp/kabelmodem-monitor/linux-collector
install -d /opt/modem-collector
install -m 644 modem_collector.py /opt/modem-collector/
install -m 644 modem-collector.service /etc/systemd/system/
install -m 600 modem-collector.env.example /etc/modem-collector.env
nano /etc/modem-collector.env        # MODEM_USER / MODEM_PASS eintragen
```

Einmal von Hand testen (Strg+C nach der ersten Minute):

```bash
set -a; . /etc/modem-collector.env; set +a
python3 /opt/modem-collector/modem_collector.py
influx -database modem -execute 'SELECT last("ok"), last("ds_channels") FROM modem_status'
```

Dann als Dienst starten:

```bash
systemctl daemon-reload
systemctl enable --now modem-collector
journalctl -u modem-collector -f
```

Der Dienst liest `/etc/modem-collector.env` nur beim Start. Nach jeder Änderung:
`systemctl restart modem-collector`.

## 4. Grafana

1. **Connections → Data sources → Add → InfluxDB**, Query Language *InfluxQL*,
   URL `http://localhost:8086`, Database `modem`.
2. **Dashboards → New → Import** → `grafana/grafana-dashboard.json` hochladen und
   bei „InfluxDB (modem)“ die Datenquelle wählen.

## Fehlersuche

| Meldung im Journal | Ursache |
|---|---|
| `Modem antwortet mit HTTP 401` | Modem-Login falsch oder Dienst nach Änderung der env-Datei nicht neu gestartet |
| `InfluxDB antwortet mit HTTP 401/403` | InfluxDB verlangt Login oder der Benutzer hat kein Schreibrecht auf `modem` |
| `URLError ... timed out` | Modem nicht erreichbar oder Statusseite hängt; die Seite braucht normal ~15 s |

Windows-Zeilenenden in der env-Datei (nach Kopieren von Windows) entfernen:
`sed -i 's/\r$//' /etc/modem-collector.env`
