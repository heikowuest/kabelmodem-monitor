"""Kabelmodem-Dashboard für Sagemcom F@st 3893 (Broadcom-Weboberfläche).

Aufruf:   py modem_dashboard.py [--interval 60] [--port 8089]
Danach im Browser: http://localhost:8089

Zugangsdaten werden beim Start abgefragt (oder aus den Umgebungsvariablen
MODEM_USER / MODEM_PASS gelesen) und nur im Arbeitsspeicher gehalten.
Verlauf wird in modem_history.db (SQLite) neben dem Skript gespeichert.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = os.environ.get("MODEM_HOST", "http://192.168.100.1")
HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MODEM_DB", HERE / "modem_history.db"))
HTML_PATH = HERE / "dashboard.html"
RAW_DIR = Path(os.environ.get("MODEM_RAW", HERE / "modem-dump"))
KEEP_DAYS = 30


# --------------------------------------------------------------------------- HTML-Tabellen

class TableParser(HTMLParser):
    """Sammelt alle (auch verschachtelten) Tabellen als Liste von Zeilen mit Zelltexten."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None

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


def parse_tables(html: str) -> list[list[list[str]]]:
    p = TableParser()
    p.feed(html)
    return p.tables


def num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", "."))
    return float(m.group()) if m else None


def freq_mhz(s: str | None) -> float | None:
    v = num(s)
    if v is None:
        return None
    if "mhz" in (s or "").lower():
        return v
    return v / 1e6 if v > 10000 else v


# --------------------------------------------------------------------------- Seiten auswerten

def map_columns(header: list[str]) -> dict[str, int]:
    hl = [h.lower() for h in header]

    def find(pred):
        return next((i for i, h in enumerate(hl) if pred(h)), None)

    cols = {
        "ch": find(lambda h: "channel id" in h or "chan id" in h)
        if find(lambda h: "channel id" in h or "chan id" in h) is not None
        else find(lambda h: "channel" in h and "type" not in h),
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


def split_title(table: list[list[str]]) -> tuple[str, list[str], list[list[str]]]:
    """Titelzeile (eine Zelle), Kopfzeile und Datenzeilen trennen."""
    title = ""
    rows = list(table)
    while rows and len(rows[0]) == 1:
        title = (title + " " + rows[0][0]).strip()
        rows.pop(0)
    if not rows:
        return title, [], []
    return title, rows[0], rows[1:]


def parse_status(html: str) -> dict:
    res: dict = {"ds": [], "us": [], "startup": [], "other": []}
    for table in parse_tables(html):
        title, header, rows = split_title(table)
        tl = title.lower()
        hl = " ".join(header).lower()
        # Key/Value-Tabelle "Startup Procedure"
        if "startup" in tl or ("procedure" in hl and "status" in hl):
            res["startup"] = [{"name": r[0], "status": r[1] if len(r) > 1 else "",
                               "comment": r[2] if len(r) > 2 else ""} for r in rows if r]
            continue
        cols = map_columns(header)
        is_channel = "power" in cols and ("freq" in cols or "ch" in cols)
        if not is_channel:
            if rows or header:
                res["other"].append({"title": title, "header": header, "rows": rows})
            continue
        if "downstream" in tl or ("uncorr" in cols and "upstream" not in tl):
            kind = "ds"
        elif "upstream" in tl or "width" in cols:
            kind = "us"
        else:
            kind = "ds"
        ofdm_title = "ofdm" in tl
        for r in rows:
            if len(r) < len(header) - 1:
                continue
            g = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else None  # noqa: E731
            mod = g("mod") or ""
            ch = {
                "ch": g("ch"),
                "lock": g("lock"),
                "mod": mod,
                "freq": freq_mhz(g("freq")),
                "power": num(g("power")),
                "snr": num(g("snr")),
                "corr": num(g("corr")),
                "uncorr": num(g("uncorr")),
                "width": g("width"),
                "ofdm": ofdm_title or "ofdm" in mod.lower() or "other" == mod.lower().strip(),
            }
            if ch["power"] is None and ch["freq"] is None:
                continue
            if ch["freq"] == 0 and ch["power"] in (0, None):
                continue  # leere Platzhalterkanäle
            res[kind].append(ch)
    return res


def parse_kv(html: str) -> dict[str, str]:
    kv: dict[str, str] = {}
    for table in parse_tables(html):
        for r in table:
            if len(r) == 2 and r[0]:
                kv[r[0].rstrip(":").strip()] = r[1]
    return kv


def parse_eventlog(html: str) -> list[dict]:
    out = []
    for table in parse_tables(html):
        title, header, rows = split_title(table)
        hl = [h.lower() for h in header]
        if not any("text" in h for h in hl) or not any("level" in h for h in hl):
            continue
        idx = {k: next((i for i, h in enumerate(hl) if k in h), None)
               for k in ("index", "id", "level", "count", "first", "last", "text")}
        for r in rows:
            g = lambda k: r[idx[k]] if idx[k] is not None and idx[k] < len(r) else ""  # noqa: E731
            text = g("text")
            out.append({
                "index": g("index"), "level": num(g("level")), "count": g("count"),
                "first": g("first"), "last": g("last"),
                "text": re.sub(r";CM-MAC=.*$", "", text),
            })
    return out


# --------------------------------------------------------------------------- Modem-Abfrage

class Modem:
    def __init__(self, user: str, pw: str) -> None:
        self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def get(self, path: str, timeout: float = 30) -> str:
        req = urllib.request.Request(HOST + path, headers={
            "Authorization": self.auth, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")


class Collector:
    def __init__(self, modem: Modem, interval: int) -> None:
        self.modem = modem
        self.interval = interval
        self.lock = threading.Lock()
        self.state: dict = {"ok": False, "error": "Noch keine Abfrage", "updated": None}
        self.wake = threading.Event()
        self.last_slow = 0.0
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS samples(
            ts INTEGER, kind TEXT, ch TEXT, freq REAL, power REAL, snr REAL,
            corr REAL, uncorr REAL, ofdm INTEGER)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS i_ts ON samples(ts)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS meta(
            ts INTEGER PRIMARY KEY, temp REAL, ok INTEGER, duration REAL)""")
        self.db.commit()
        self.dblock = threading.Lock()

    def poll_once(self) -> None:
        t0 = time.time()
        st: dict = {}
        try:
            html = self.modem.get("/cgi-bin/cmconnectionstatus.php", timeout=120)
            RAW_DIR.mkdir(exist_ok=True)
            (RAW_DIR / "cmconnectionstatus_latest.html").write_text(html, encoding="utf-8")
            st["status"] = parse_status(html)
            # Langsamer ändernde Seiten seltener abfragen
            if time.time() - self.last_slow > 300 or "swinfo" not in self.state:
                st["swinfo"] = parse_kv(self.modem.get("/cgi-bin/cmswinfo.php"))
                st["info"] = parse_kv(self.modem.get("/cgi-bin/info.php"))
                st["events"] = parse_eventlog(self.modem.get("/cgi-bin/CmEventLog.php"))
                self.last_slow = time.time()
            else:
                st["swinfo"] = parse_kv(self.modem.get("/cgi-bin/cmswinfo.php"))
                for k in ("info", "events"):
                    st[k] = self.state.get(k)
            st.update(ok=True, error=None)
        except urllib.error.HTTPError as e:
            st = dict(self.state, ok=False,
                      error="Login abgelehnt (401)" if e.code == 401 else f"HTTP {e.code}")
        except Exception as e:  # Timeout, Netzwerkfehler, Parserfehler
            st = dict(self.state, ok=False, error=f"{type(e).__name__}: {e}")
        dur = round(time.time() - t0, 1)
        st["updated"] = int(time.time())
        st["duration"] = dur
        st["interval"] = self.interval
        with self.lock:
            self.state = st
        self.store(st, dur)

    def store(self, st: dict, dur: float) -> None:
        ts = st["updated"]
        temp = num((st.get("swinfo") or {}).get("Board Temperature"))
        with self.dblock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?,?,?)",
                            (ts, temp, 1 if st.get("ok") else 0, dur))
            if st.get("ok"):
                rows = []
                for kind in ("ds", "us"):
                    for c in st["status"][kind]:
                        rows.append((ts, kind, c["ch"], c["freq"], c["power"], c["snr"],
                                     c["corr"], c["uncorr"], 1 if c["ofdm"] else 0))
                self.db.executemany("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?)", rows)
            cutoff = ts - KEEP_DAYS * 86400
            self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM meta WHERE ts < ?", (cutoff,))
            self.db.commit()

    def history(self, hours: float) -> dict:
        """Verlauf, in Zeit-Buckets zusammengefasst (max. ~600 Punkte pro Serie).

        Serien: ds (SC-QAM), dso (OFDM), us (SC-QAM), uso (OFDMA).
        Je Bucket: [t, Pegel min, avg, max, SNR min, avg, korrigiert, unkorrigierbar]
        Fehler sind Zuwächse der Zähler (Neustart/Zählerreset wird ignoriert).
        """
        now = int(time.time())
        since = int(now - hours * 3600)
        bucket = max(self.interval, int(hours * 3600 / 600))
        with self.dblock:
            s = self.db.execute(
                "SELECT ts, kind, ch, ofdm, power, snr, corr, uncorr FROM samples "
                "WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
            m = self.db.execute("SELECT ts, temp, ok FROM meta WHERE ts >= ? ORDER BY ts",
                                (since,)).fetchall()
        last: dict[tuple, tuple] = {}
        agg: dict[str, dict[int, list]] = {}
        for ts, kind, ch, ofdm, p, snr, corr, unc in s:
            key = kind + ("o" if ofdm else "")
            b = ts - ts % bucket
            a = agg.setdefault(key, {}).setdefault(b, [[], [], 0.0, 0.0])
            if p is not None:
                a[0].append(p)
            if snr is not None:
                a[1].append(snr)
            prev = last.get((kind, ch, ofdm))
            if prev and corr is not None and unc is not None and prev[0] is not None:
                dc, du = corr - prev[0], unc - prev[1]
                if dc >= 0 and du >= 0:
                    a[2] += dc
                    a[3] += du
            last[(kind, ch, ofdm)] = (corr, unc)
        out: dict = {"bucket": bucket, "since": since, "now": now}
        for key, buckets in agg.items():
            rows = []
            for b in sorted(buckets):
                pw, sn, dc, du = buckets[b]
                rows.append([
                    b,
                    min(pw) if pw else None, round(sum(pw) / len(pw), 2) if pw else None,
                    max(pw) if pw else None,
                    min(sn) if sn else None, round(sum(sn) / len(sn), 2) if sn else None,
                    dc, du,
                ])
            out[key] = rows
        tb: dict[int, list] = {}
        for ts, temp, ok in m:
            e = tb.setdefault(ts - ts % bucket, [[], 0])
            if temp is not None:
                e[0].append(temp)
            if not ok:
                e[1] += 1
        out["meta"] = [[b, round(sum(v[0]) / len(v[0]), 1) if v[0] else None, v[1]]
                       for b, v in sorted(tb.items())]
        return out

    def run(self) -> None:
        while True:
            self.poll_once()
            self.wake.wait(self.interval)
            self.wake.clear()


# --------------------------------------------------------------------------- Webserver

def make_handler(col: Collector):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # ruhig bleiben
            pass

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self.send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/state":
                with col.lock:
                    body = json.dumps(col.state)
                self.send(200, body.encode(), "application/json")
            elif u.path == "/api/history":
                h = float(parse_qs(u.query).get("hours", ["24"])[0])
                self.send(200, json.dumps(col.history(min(h, 24 * KEEP_DAYS))).encode(),
                          "application/json")
            else:
                self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path == "/api/refresh":
                col.wake.set()
                self.send(200, b'{"ok":true}', "application/json")
            else:
                self.send(404, b"not found", "text/plain")

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description="Kabelmodem-Dashboard")
    ap.add_argument("--interval", type=int, default=60, help="Abfrageintervall in Sekunden")
    ap.add_argument("--port", type=int, default=8089)
    args = ap.parse_args()

    user = os.environ.get("MODEM_USER") or input("Modem-Benutzername: ").strip()
    pw = os.environ.get("MODEM_PASS") or getpass.getpass("Modem-Passwort (unsichtbar): ")
    modem = Modem(user, pw)
    try:
        modem.get("/cgi-bin/info.php", timeout=15)
    except urllib.error.HTTPError as e:
        print("Login fehlgeschlagen." if e.code == 401 else f"Fehler: HTTP {e.code}")
        sys.exit(1)
    except Exception as e:
        print(f"Modem nicht erreichbar: {e}")
        sys.exit(1)

    col = Collector(modem, max(15, args.interval))
    threading.Thread(target=col.run, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(col))
    print(f"Login ok. Dashboard: http://localhost:{args.port}  (Beenden mit Strg+C)")
    # Server im Hintergrund-Thread, damit Strg+C unter Windows zuverlässig ankommt
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
        print("Beendet.")


if __name__ == "__main__":
    main()
