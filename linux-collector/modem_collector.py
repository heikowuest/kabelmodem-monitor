#!/usr/bin/env python3
"""Kabelmodem-Collector: liest DOCSIS-Werte aus einem Sagemcom F@st 3893
(Broadcom-Weboberfläche, HTTP Basic Auth) und schreibt sie in InfluxDB 1.x.

Nur Python-Standardbibliothek (ab 3.8). Konfiguration über Umgebungsvariablen:

  MODEM_HOST      http://192.168.100.1
  MODEM_USER      Benutzername am Modem
  MODEM_PASS      Passwort am Modem
  INFLUX_URL      http://localhost:8086
  INFLUX_DB       modem
  INFLUX_USER     optional, falls InfluxDB-Auth aktiv
  INFLUX_PASS     optional
  INTERVAL        Abfrageintervall in Sekunden (Standard 60)
  EVENT_INTERVAL  Eventlog-Intervall in Sekunden (Standard 300)

Measurements:
  docsis_ds      tags: channel, type (scqam|ofdm), modulation
                 fields: freq_mhz, power_dbmv, snr_db, corrected, uncorrectables, locked
  docsis_us      tags: channel, type (scqam|ofdma), modulation
                 fields: freq_mhz, power_dbmv, width_hz, locked
  modem_status   fields: ok, poll_duration_s, uptime_s, temperature_c, network_access,
                         ds_channels, ds_ofdm_channels, us_channels, us_ofdma_channels,
                         ds_locked, us_locked, ds_power_min/max, ds_snr_min, ofdm_mer_min,
                         us_power_min/max, uncorrectables_total, corrected_total_scqam
                 tags: firmware
  modem_events   tags: level, event_id   fields: text, count, level_num
                 (Zeitstempel = "Last Time" aus dem Ereignisprotokoll, Ortszeit des Hosts)
"""
from __future__ import annotations

import base64
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

MODEM_HOST = os.environ.get("MODEM_HOST", "http://192.168.100.1").rstrip("/")
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086").rstrip("/")
INFLUX_DB = os.environ.get("INFLUX_DB", "modem")
INTERVAL = max(15, int(os.environ.get("INTERVAL", "60")))
EVENT_INTERVAL = max(60, int(os.environ.get("EVENT_INTERVAL", "300")))


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# --------------------------------------------------------------------------- HTML-Tabellen

class TableParser(HTMLParser):
    """Sammelt alle (auch verschachtelten) Tabellen als Liste von Zeilen mit Zelltexten."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._stack:
            self._stack[-1][-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "table" and self._stack:
            t = [r for r in self._stack.pop() if r]
            if t:
                self.tables.append(t)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html: str) -> list:
    p = TableParser()
    p.feed(html)
    return p.tables


def num(s):
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", "."))
    return float(m.group()) if m else None


def freq_mhz(s):
    v = num(s)
    if v is None:
        return None
    if "mhz" in (s or "").lower():
        return v
    return v / 1e6 if v > 10000 else v


def hz(s):
    v = num(s)
    if v is None:
        return None
    sl = (s or "").lower()
    if "mhz" in sl:
        return v * 1e6
    if "khz" in sl or "ksym" in sl:
        return v * 1e3
    return v


def map_columns(header: list) -> dict:
    hl = [h.lower() for h in header]

    def find(pred):
        return next((i for i, h in enumerate(hl) if pred(h)), None)

    ch = find(lambda h: "channel id" in h or "chan id" in h)
    if ch is None:
        ch = find(lambda h: "channel" in h and "type" not in h)
    cols = {
        "ch": ch,
        "lock": find(lambda h: "lock" in h),
        "mod": find(lambda h: "modulation" in h or "type" in h or "profile" in h),
        "freq": find(lambda h: "freq" in h),
        "power": find(lambda h: "power" in h or "level" in h),
        "snr": find(lambda h: "snr" in h or "mer" in h),
        "corr": find(lambda h: "correct" in h and "uncorr" not in h),
        "uncorr": find(lambda h: "uncorr" in h),
        "width": find(lambda h: "width" in h or "symbol rate" in h or "bandwidth" in h),
    }
    return {k: v for k, v in cols.items() if v is not None}


def split_title(table):
    title = ""
    rows = list(table)
    while rows and len(rows[0]) == 1:
        title = (title + " " + rows[0][0]).strip()
        rows.pop(0)
    if not rows:
        return title, [], []
    return title, rows[0], rows[1:]


def parse_status(html: str) -> dict:
    res = {"ds": [], "us": []}
    for table in parse_tables(html):
        title, header, rows = split_title(table)
        tl = title.lower()
        cols = map_columns(header)
        if "startup" in tl or "power" not in cols or ("freq" not in cols and "ch" not in cols):
            continue
        if "downstream" in tl or ("uncorr" in cols and "upstream" not in tl):
            kind = "ds"
        elif "upstream" in tl or "width" in cols:
            kind = "us"
        else:
            kind = "ds"
        for r in rows:
            if len(r) < len(header) - 1:
                continue

            def g(k):
                return r[cols[k]] if k in cols and cols[k] < len(r) else None

            mod = g("mod") or ""
            lock = g("lock") or ""
            c = {
                "ch": g("ch"),
                "locked": bool(re.search(r"lock", lock, re.I)) and not re.search(r"not|unlock", lock, re.I),
                "mod": mod,
                "freq": freq_mhz(g("freq")),
                "power": num(g("power")),
                "snr": num(g("snr")),
                "corr": num(g("corr")),
                "uncorr": num(g("uncorr")),
                "width": hz(g("width")),
                "ofdm": "ofdm" in tl or "ofdm" in mod.lower() or mod.lower().strip() == "other",
            }
            if c["power"] is None and c["freq"] is None:
                continue
            if c["freq"] == 0 and c["power"] in (0, None):
                continue  # leere Platzhalterkanäle
            res[kind].append(c)
    return res


def parse_kv(html: str) -> dict:
    kv = {}
    for table in parse_tables(html):
        for r in table:
            if len(r) == 2 and r[0]:
                kv[r[0].rstrip(":").strip()] = r[1]
    return kv


def parse_eventlog(html: str) -> list:
    out = []
    for table in parse_tables(html):
        _, header, rows = split_title(table)
        hl = [h.lower() for h in header]
        if not any("text" in h for h in hl) or not any("level" in h for h in hl):
            continue

        def idx(word, exact=False):
            return next((i for i, h in enumerate(hl) if (h == word if exact else word in h)), None)

        ix = {"id": idx("id", exact=True), "level": idx("level"), "count": idx("count"),
              "last": idx("last"), "text": idx("text")}
        for r in rows:
            def g(k):
                return r[ix[k]] if ix[k] is not None and ix[k] < len(r) else ""
            out.append({
                "id": g("id"), "level": num(g("level")), "count": num(g("count")),
                "last": g("last"), "text": re.sub(r";CM-MAC=.*$", "", g("text")),
            })
    return out


def uptime_seconds(s):
    if not s:
        return None
    total, found = 0, False
    for n, unit in re.findall(r"(\d+)\s*(day|hour|min|sec)", s, re.I):
        found = True
        total += int(n) * {"day": 86400, "hour": 3600, "min": 60, "sec": 1}[unit.lower()]
    return total if found else None


# --------------------------------------------------------------------------- InfluxDB line protocol

def esc_tag(v) -> str:
    return re.sub(r"([,= ])", r"\\\1", str(v)) if str(v) != "" else "unknown"


def fmt_field(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"{v}i"
    if isinstance(v, float):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def line(measurement: str, tags: dict, fields: dict, ts: int):
    f = {k: v for k, v in fields.items() if v is not None}
    if not f:
        return None
    t = "".join(f",{k}={esc_tag(v)}" for k, v in sorted(tags.items()) if v is not None)
    fs = ",".join(f"{k}={fmt_field(v)}" for k, v in f.items())
    return f"{measurement}{t} {fs} {ts}"


def as_int(v):
    return int(v) if v is not None else None


class Influx:
    def __init__(self) -> None:
        self.auth = {}
        if os.environ.get("INFLUX_USER"):
            self.auth = {"u": os.environ["INFLUX_USER"], "p": os.environ.get("INFLUX_PASS", "")}

    def _post(self, path: str, params: dict, body: bytes):
        q = urllib.parse.urlencode({**params, **self.auth})
        req = urllib.request.Request(f"{INFLUX_URL}{path}?{q}", data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"InfluxDB antwortet mit HTTP {e.code}"
                               + (" (Login/Rechte prüfen: INFLUX_USER/INFLUX_PASS)" if e.code in (401, 403) else "")) from None

    def ensure_db(self) -> None:
        try:
            self._post("/query", {"q": f'CREATE DATABASE "{INFLUX_DB}"'}, b"")
        except Exception as e:  # z. B. keine Admin-Rechte: DB muss dann schon existieren
            log(f"Hinweis: CREATE DATABASE nicht möglich ({e})")

    def write(self, lines: list) -> None:
        lines = [x for x in lines if x]
        if lines:
            self._post("/write", {"db": INFLUX_DB, "precision": "s"}, "\n".join(lines).encode())


# --------------------------------------------------------------------------- Modem

class Modem:
    def __init__(self, user: str, pw: str) -> None:
        self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def get(self, path: str, timeout: float = 30) -> str:
        req = urllib.request.Request(MODEM_HOST + path, headers={
            "Authorization": self.auth, "User-Agent": "modem-collector/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Modem antwortet mit HTTP {e.code} auf {path}"
                               + (" (Login falsch? Nach Änderung der env-Datei Dienst neu starten)" if e.code == 401 else "")) from None


def chan(c):
    """Kanalnummer mit Nullen auffüllen, damit Grafana numerisch sortiert."""
    return c["ch"].zfill(3) if (c["ch"] or "").isdigit() else c["ch"]


def poll(modem: Modem, with_events: bool) -> list:
    ts = int(time.time())
    t0 = time.time()
    lines = []
    st = parse_status(modem.get("/cgi-bin/cmconnectionstatus.php", timeout=120))
    sw = parse_kv(modem.get("/cgi-bin/cmswinfo.php"))
    dur = round(time.time() - t0, 2)

    for c in st["ds"]:
        lines.append(line("docsis_ds",
                          {"channel": chan(c), "type": "ofdm" if c["ofdm"] else "scqam", "modulation": c["mod"]},
                          {"freq_mhz": c["freq"], "power_dbmv": c["power"], "snr_db": c["snr"],
                           "corrected": as_int(c["corr"]), "uncorrectables": as_int(c["uncorr"]),
                           "locked": 1 if c["locked"] else 0}, ts))
    for c in st["us"]:
        lines.append(line("docsis_us",
                          {"channel": chan(c), "type": "ofdma" if c["ofdm"] else "scqam", "modulation": c["mod"]},
                          {"freq_mhz": c["freq"], "power_dbmv": c["power"], "width_hz": c["width"],
                           "locked": 1 if c["locked"] else 0}, ts))

    ds, us = st["ds"], st["us"]
    dsq = [c for c in ds if not c["ofdm"]]
    dso = [c for c in ds if c["ofdm"]]
    usq = [c for c in us if not c["ofdm"]]

    def mn(arr, k):
        v = [c[k] for c in arr if c[k] is not None]
        return min(v) if v else None

    def mx(arr, k):
        v = [c[k] for c in arr if c[k] is not None]
        return max(v) if v else None

    access = sw.get("Network Access")
    lines.append(line("modem_status", {"firmware": sw.get("Software Version")}, {
        "ok": 1,
        "poll_duration_s": dur,
        "uptime_s": uptime_seconds(sw.get("System Up Time")),
        "temperature_c": num(sw.get("Board Temperature")),
        "network_access": None if access is None else (1 if re.search(r"allow", access, re.I) else 0),
        "ds_channels": len(dsq), "ds_ofdm_channels": len(dso),
        "us_channels": len(usq), "us_ofdma_channels": len(us) - len(usq),
        "ds_locked": sum(1 for c in ds if c["locked"]), "us_locked": sum(1 for c in us if c["locked"]),
        "ds_power_min": mn(ds, "power"), "ds_power_max": mx(ds, "power"),
        "ds_snr_min": mn(dsq, "snr"), "ofdm_mer_min": mn(dso, "snr"),
        "us_power_min": mn(usq, "power"), "us_power_max": mx(usq, "power"),
        "uncorrectables_total": int(sum(c["uncorr"] or 0 for c in ds)),
        "corrected_total_scqam": int(sum(c["corr"] or 0 for c in dsq)),
    }, ts))

    if with_events:
        for e in parse_eventlog(modem.get("/cgi-bin/CmEventLog.php")):
            try:
                ets = int(datetime.strptime(e["last"].split(".")[0], "%Y-%m-%d %H:%M:%S").timestamp())
            except ValueError:
                continue
            lv = int(e["level"]) if e["level"] is not None else None
            lines.append(line("modem_events", {"level": lv, "event_id": e["id"] or None},
                              {"text": e["text"], "count": as_int(e["count"]), "level_num": lv}, ets))
    return lines


def main() -> None:
    user, pw = os.environ.get("MODEM_USER"), os.environ.get("MODEM_PASS")
    if not user or not pw:
        log("MODEM_USER und MODEM_PASS müssen gesetzt sein.")
        sys.exit(2)
    modem, influx = Modem(user, pw), Influx()
    influx.ensure_db()
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    log(f"Start: Modem {MODEM_HOST} -> {INFLUX_URL}/{INFLUX_DB}, alle {INTERVAL}s")
    last_events, failures = 0.0, 0
    while True:
        start = time.time()
        try:
            with_events = start - last_events >= EVENT_INTERVAL
            influx.write(poll(modem, with_events))
            if with_events:
                last_events = start
            if failures:
                log(f"Wieder ok nach {failures} Fehlversuch(en)")
            failures = 0
        except Exception as e:
            failures += 1
            log(f"Fehler: {e}" if isinstance(e, RuntimeError) else f"Fehler: {type(e).__name__}: {e}")
        if failures:
            try:
                influx.write([line("modem_status", {}, {"ok": 0}, int(time.time()))])
            except Exception:
                pass
        time.sleep(max(1.0, INTERVAL - (time.time() - start)))


if __name__ == "__main__":
    main()
