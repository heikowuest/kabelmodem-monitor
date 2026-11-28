"""Erzeugt grafana-dashboard.json (InfluxDB 1.x / InfluxQL) für den Modem-Collector."""
import json, sys

DS = {"type": "influxdb", "uid": "${DS_INFLUXDB}"}

def steps(base, *pairs):
    return {"mode": "absolute", "steps": [{"color": base, "value": None}] + [{"color": c, "value": v} for v, c in pairs]}

# Grenzwerte nach BK-Tabelle (Meister Voda), +0.05 wegen einer Nachkommastelle
TH_DS_POWER = steps("red", (-7.95, "yellow"), (-5.95, "light-green"), (-3.95, "green"), (13.05, "light-green"), (18.05, "yellow"), (20.05, "red"))
TH_SNR = steps("red", (30.05, "yellow"), (32.05, "light-green"), (33.05, "green"))
TH_DS_POWER_1024 = steps("red", (-5.95, "yellow"), (-3.95, "light-green"), (-1.95, "green"), (15.05, "light-green"), (20.05, "yellow"), (22.05, "red"))
TH_MER_1024 = steps("red", (36.05, "yellow"), (38.05, "light-green"), (39.05, "green"))
TH_US = steps("red", (38.05, "yellow"), (40.05, "light-green"), (44.05, "green"), (47.05, "light-green"), (48.05, "yellow"), (50.05, "red"))
# OFDMA-Pegel ist auf 1,6 MHz statt 6,4 MHz bezogen: 10*log10(6,4/1,6) = 6 dB niedriger als SC-QAM
OFDMA_OFFSET = 6.0
TH_US_OFDMA = {"mode": "absolute", "steps": [dict(st, value=None if st["value"] is None else round(st["value"] - OFDMA_OFFSET, 2)) for st in TH_US["steps"]]}

pid = 0
def nid():
    global pid; pid += 1; return pid

def tgt(q, ref="A", fmt="time_series", alias=None):
    t = {"datasource": DS, "refId": ref, "rawQuery": True, "query": q, "resultFormat": fmt}
    if alias: t["alias"] = alias
    return t

def stat(title, q, x, y, w=3, unit="none", th=None, decimals=1, mappings=None, desc=None, graph=True):
    p = {"id": nid(), "type": "stat", "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": 4},
         "targets": [tgt(q)],
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "colorMode": "value", "graphMode": "area" if graph else "none", "textMode": "value", "justifyMode": "center"},
         "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "color": {"mode": "thresholds"},
                                      "thresholds": th or steps("text"), "mappings": mappings or []}, "overrides": []}}
    if desc: p["description"] = desc
    return p

def bars(title, meas, field, x, y, w, th, unit, mn, mx, other_type, other_props, desc=None):
    """Balken je Kanal: A = SC-QAM (Panel-Grenzwerte), B = OFDM/OFDMA (eigene Darstellung per Override)."""
    base = f'SELECT last("{field}") FROM "{meas}" WHERE $timeFilter AND "type" = '
    p = {"id": nid(), "type": "bargauge", "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": 9},
         "targets": [tgt(base + "'scqam' GROUP BY \"channel\"", "A", alias="$tag_channel"),
                     tgt(base + f"'{other_type}' GROUP BY \"channel\"", "B", alias=f"$tag_channel {other_type.upper()}")],
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "orientation": "vertical", "displayMode": "basic", "showUnfilled": True,
                     "valueMode": "color", "namePlacement": "auto", "sizing": "auto", "minVizWidth": 8, "minVizHeight": 10},
         "fieldConfig": {"defaults": {"unit": unit, "decimals": 1, "min": mn, "max": mx,
                                      "color": {"mode": "thresholds"}, "thresholds": th},
                         "overrides": [{"matcher": {"id": "byFrameRefID", "options": "B"}, "properties": other_props}]}}
    if desc: p["description"] = desc
    return p

def ts(title, targets, x, y, w, h=9, unit="none", th=None, th_style="off", draw="line", stack=False, mn=None, mx=None, desc=None, decimals=1):
    custom = {"drawStyle": draw, "lineWidth": 1, "fillOpacity": 80 if draw == "bars" else 0, "pointSize": 3,
              "showPoints": "never", "spanNulls": False, "lineInterpolation": "linear",
              "thresholdsStyle": {"mode": th_style}, "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "axisPlacement": "auto", "barAlignment": 0, "gradientMode": "none"}
    d = {"unit": unit, "decimals": decimals, "custom": custom, "color": {"mode": "palette-classic"},
         "thresholds": th or steps("green")}
    if mn is not None: d["min"] = mn
    if mx is not None: d["max"] = mx
    p = {"id": nid(), "type": "timeseries", "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": targets,
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "fieldConfig": {"defaults": d, "overrides": []}}
    if desc: p["description"] = desc
    return p

def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

S = 'FROM "modem_status" WHERE $timeFilter'
# Pegel min/max nur über SC-QAM (letzter Wert je Kanal), OFDM hat andere Grenzwerte
SCQ = ('SELECT {agg}("p") FROM (SELECT last("power_dbmv") AS "p" FROM "docsis_ds" '
       'WHERE $timeFilter AND "type" = \'scqam\' GROUP BY "channel")')
panels = []
y = 0
panels.append(row("Übersicht", y)); y += 1
panels += [
    stat("Netzzugang", f'SELECT last("network_access") {S}', 0, y, decimals=0,
         th=steps("red", (1, "green")),
         mappings=[{"type": "value", "options": {"1": {"text": "Allowed", "color": "green"}, "0": {"text": "Gesperrt", "color": "red"}}}]),
    stat("Uptime", f'SELECT last("uptime_s") {S}', 3, y, unit="dtdurations", decimals=0),
    stat("DS-Pegel min (SC-QAM)", SCQ.format(agg="min"), 6, y, unit="dBmV", th=TH_DS_POWER, graph=False),
    stat("DS-Pegel max (SC-QAM)", SCQ.format(agg="max"), 9, y, unit="dBmV", th=TH_DS_POWER, graph=False),
    stat("SNR min (SC-QAM)", f'SELECT last("ds_snr_min") {S}', 12, y, unit="dB", th=TH_SNR),
    stat("MER min (OFDM)", f'SELECT last("ofdm_mer_min") {S}', 15, y, unit="dB", th=TH_MER_1024,
         desc="Bewertet als 1024QAM nach BK-Tabelle (das Modem meldet die QAM-Stufe nicht)."),
    stat("US-Pegel max (SC-QAM)", f'SELECT last("us_power_max") {S}', 18, y, unit="dBmV", th=TH_US,
         desc="Spalte 'DOCSIS 3.1 Modem' der BK-Tabelle."),
    stat("Unkorrigierbar (Zeitraum)",
         'SELECT sum("d") FROM (SELECT non_negative_difference(last("uncorrectables_total")) AS "d" FROM "modem_status" WHERE $timeFilter GROUP BY time(1m))',
         21, y, decimals=0, th=steps("green", (1, "yellow"), (1000, "red"))),
]
y += 4

panels.append(row("Aktuelle Kanalwerte", y)); y += 1
panels += [
    bars("Downstream-Pegel je Kanal (dBmV)", "docsis_ds", "power_dbmv", 0, y, 12, TH_DS_POWER, "dBmV", -10, 22,
         "ofdm", [{"id": "thresholds", "value": TH_DS_POWER_1024}],
         desc="Farben nach BK-Tabelle: SC-QAM als 256QAM, OFDM-Kanal als 1024QAM."),
    bars("Downstream SNR/MER je Kanal (dB)", "docsis_ds", "snr_db", 12, y, 12, TH_SNR, "dB", 25, 46,
         "ofdm", [{"id": "thresholds", "value": TH_MER_1024}],
         desc="Farben nach BK-Tabelle: SC-QAM als 256QAM, OFDM-Kanal als 1024QAM."),
]
y += 9
panels += [
    bars("Upstream-Sendepegel je Kanal (dBmV)", "docsis_us", "power_dbmv", 0, y, 8, TH_US, "dBmV", 30, 55,
         "ofdma", [{"id": "thresholds", "value": TH_US_OFDMA}],
         desc="Farben nach BK-Tabelle, Spalte DOCSIS 3.1 Modem. Der OFDMA-Pegel ist auf 1,6 MHz statt 6,4 MHz bezogen und "
              "liegt dadurch 6 dB niedriger; seine Schwellen sind entsprechend um 6 dB verschoben."),
    {"id": nid(), "type": "table", "title": "Ereignisprotokoll", "datasource": DS, "gridPos": {"x": 8, "y": y, "w": 16, "h": 9},
     "targets": [tgt('SELECT "level_num" AS "Level", "count" AS "Anzahl", "text" AS "Meldung" FROM "modem_events" WHERE $timeFilter ORDER BY time DESC LIMIT 300', fmt="table")],
     "options": {"showHeader": True, "cellHeight": "sm"},
     "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "thresholds": steps("red", (4, "yellow"), (6, "transparent"))},
                     "overrides": [{"matcher": {"id": "byName", "options": "Level"},
                                    "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "custom.width", "value": 60}]},
                                   {"matcher": {"id": "byName", "options": "Anzahl"}, "properties": [{"id": "custom.width", "value": 70}]},
                                   {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "custom.width", "value": 160}]}]}},
]
y += 9

panels.append(row("Verlauf", y)); y += 1
panels += [
    ts("Downstream-Pegel (dBmV)", [tgt('SELECT mean("power_dbmv") FROM "docsis_ds" WHERE $timeFilter GROUP BY time($__interval), "channel" fill(none)', alias="$tag_channel")],
       0, y, 12, unit="dBmV", th=TH_DS_POWER, th_style="area"),
    ts("Downstream SNR/MER (dB)", [tgt('SELECT mean("snr_db") FROM "docsis_ds" WHERE $timeFilter GROUP BY time($__interval), "channel" fill(none)', alias="$tag_channel")],
       12, y, 12, unit="dB", th=TH_SNR, th_style="area"),
]
y += 9
panels += [
    ts("Codewort-Fehler je Intervall", [
        tgt('SELECT sum("d") FROM (SELECT non_negative_difference(last("uncorrectables")) AS "d" FROM "docsis_ds" WHERE $timeFilter GROUP BY time($__interval), "channel") GROUP BY time($__interval)', "A", alias="unkorrigierbar (alle)"),
        tgt('SELECT sum("d") FROM (SELECT non_negative_difference(last("corrected")) AS "d" FROM "docsis_ds" WHERE $timeFilter AND "type" = \'scqam\' GROUP BY time($__interval), "channel") GROUP BY time($__interval)', "B", alias="korrigiert (SC-QAM)"),
    ], 0, y, 12, draw="bars", decimals=0,
       desc="Zuwachs der Zähler je Intervall. Korrigierte OFDM-Codewörter sind ausgelassen (LDPC korrigiert dort ständig, das ist normal)."),
    ts("Upstream-Sendepegel (dBmV)", [tgt('SELECT mean("power_dbmv") FROM "docsis_us" WHERE $timeFilter GROUP BY time($__interval), "channel" fill(none)', alias="$tag_channel")],
       12, y, 12, unit="dBmV", th=TH_US, th_style="area",
       desc="Farbflächen gelten für SC-QAM. Der OFDMA-Kanal (1,6-MHz-Bezug) liegt systembedingt rund 6 dB tiefer."),
]
y += 9
panels += [
    ts("Temperatur (°C)", [tgt(f'SELECT mean("temperature_c") {S} GROUP BY time($__interval) fill(none)', alias="Board")], 0, y, 8, h=7, unit="celsius"),
    ts("Abfrage: Dauer (s) und Erfolg", [
        tgt(f'SELECT mean("poll_duration_s") {S} GROUP BY time($__interval) fill(none)', "A", alias="Dauer"),
        tgt(f'SELECT min("ok") {S} GROUP BY time($__interval) fill(none)', "B", alias="ok (1/0)"),
    ], 8, y, 8, h=7),
    ts("Gelockte Kanäle", [
        tgt(f'SELECT last("ds_locked") {S} GROUP BY time($__interval) fill(none)', "A", alias="Downstream"),
        tgt(f'SELECT last("us_locked") {S} GROUP BY time($__interval) fill(none)', "B", alias="Upstream"),
    ], 16, y, 8, h=7, decimals=0),
]

dash = {
    "__inputs": [{"name": "DS_INFLUXDB", "label": "InfluxDB (modem)", "description": "InfluxDB-1.x-Datenquelle mit der Datenbank 'modem'",
                  "type": "datasource", "pluginId": "influxdb", "pluginName": "InfluxDB"}],
    "__requires": [{"type": "datasource", "id": "influxdb", "name": "InfluxDB", "version": "1.0.0"}],
    "title": "Kabelmodem (DOCSIS)", "uid": "kabelmodem-docsis", "tags": ["docsis", "modem", "vodafone"],
    "timezone": "browser", "editable": True, "refresh": "1m", "schemaVersion": 39, "version": 3,
    "time": {"from": "now-24h", "to": "now"}, "graphTooltip": 1,
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                              "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
                             {"name": "Kritische Modem-Ereignisse", "datasource": DS, "enable": True, "iconColor": "red",
                              "query": 'SELECT "text" FROM "modem_events" WHERE $timeFilter AND "level_num" <= 3',
                              "textColumn": "text", "titleColumn": "", "tagsColumn": ""}]},
    "templating": {"list": []},
    "panels": panels,
}
json.dump(dash, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("Panels:", len(panels))
