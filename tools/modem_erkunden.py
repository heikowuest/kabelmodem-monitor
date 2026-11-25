"""Lädt die Weboberfläche des Kabelmodems (HTTP Basic Auth) herunter,
damit man sehen kann, welche Seiten/Daten es liefert.

Aufruf:  py modem_erkunden.py
Die Zugangsdaten werden beim Start abgefragt und nirgends gespeichert.
Ergebnis: Ordner "modem-dump" neben diesem Skript + index.txt mit Übersicht.
"""
import base64
import getpass
import gzip
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

HOST = "http://192.168.100.1"
MAX_FILES = 300
OUT = Path(__file__).resolve().parent / "modem-dump"

SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
            ".ttf", ".eot", ".bmp", ".webp", ".mp4", ".bin", ".img")

# typische Pfade, die nicht unbedingt verlinkt sind
EXTRA = [
    "/", "/index.html", "/index.htm", "/main.html", "/status.html", "/status.htm",
    "/cmconnectionstatus.html", "/cmswinfo.html", "/cmeventlog.html",
    "/RgConnect.asp", "/RgSwInfo.asp", "/cmSignalData.htm", "/cmLogsData.htm",
    "/DocsisStatus.htm", "/DocsisStatus.asp", "/eventLog.htm", "/cgi-bin/status",
    "/api/v1/modem/docsis", "/api/v1/sta_docsis_status", "/api/v1/CableModem",
    "/data/getCMStatus.asp", "/getRouterStatus", "/htdocs/cm_info.php",
    "/walk?oids=1.3.6.1.2.1.10.127.1.1.1", "/snmpGet?oids=1.3.6.1.2.1.1.1.0",
    # Broadcom-Menü dieses Modems (aus menuBcm.js)
    "/cgi-bin/cmconnectionstatus.php", "/cgi-bin/CmEventLog.php", "/cgi-bin/cmswinfo.php",
    "/cgi-bin/arpview.php", "/cgi-bin/MtaStatus.php", "/cgi-bin/MtaProvisioning.php",
    "/cgi-bin/MtaEventLog.php", "/cgi-bin/MtaDhcp.php", "/cgi-bin/MtaQos.php",
    "/static/rfSpectrumAnalyzer.html.gz", "/static/rfSpectrumAnalyzerUda.html.gz",
]

# Links/Pfade in HTML und JavaScript finden
LINK_RE = re.compile(
    r"""(?:href|src|action|url)\s*[=:]\s*["']([^"'#?\s][^"'\s]*)["']"""
    r"""|["'](/?[\w\-./]+\.(?:html?|asp|aspx|php|cgi|lua|js|json|xml|txt|sh)(?:\?[^"'\s]*)?)["']"""
    r"""|["'](/(?:api|cgi-bin|cgi|data|goform|walk|snmp)[\w\-./?=&%]*)["']""",
    re.I,
)


def main() -> None:
    print(f"Modem: {HOST}")
    user = input("Benutzername: ").strip()
    pw = getpass.getpass("Passwort (Eingabe unsichtbar): ")
    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    OUT.mkdir(exist_ok=True)
    seen: set[str] = set()
    queue: deque[str] = deque(EXTRA)
    summary: list[str] = []

    def fetch(path: str):
        req = urllib.request.Request(HOST + path, headers={"Authorization": auth,
                                                           "User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()
        except Exception as e:  # Timeout, Verbindungsfehler
            return 0, str(e), b""

    # Login prüfen
    status, _, _ = fetch("/")
    if status == 401:
        print("Login fehlgeschlagen (401). Benutzername/Passwort prüfen.")
        sys.exit(1)
    if status == 0:
        print("Modem nicht erreichbar.")
        sys.exit(1)

    count = 0
    while queue and count < MAX_FILES:
        path = queue.popleft()
        if path.startswith(("http://", "https://")):
            u = urllib.parse.urlparse(path)
            if u.netloc and u.netloc != urllib.parse.urlparse(HOST).netloc:
                continue
            path = u.path + (("?" + u.query) if u.query else "")
        if not path.startswith("/"):
            path = "/" + path
        path = re.sub(r"/+", "/", path)
        if path in seen or path.lower().split("?")[0].endswith(SKIP_EXT):
            continue
        if path == "/cgi-bin/Logout.php":
            continue
        if path.lower().startswith(("/javascript:", "/mailto:", "/data:")):
            continue
        seen.add(path)

        status, ctype, body = fetch(path)
        count += 1
        line = f"{status:>3}  {len(body):>8} B  {ctype[:40]:<40}  {path}"
        print(line)
        summary.append(line)
        if status != 200 or not body:
            continue
        if body[:2] == b"\x1f\x8b":  # gzip-Dateien entpacken
            try:
                body = gzip.decompress(body)
                path_noq = path.split("?")[0]
                if path_noq.endswith(".gz"):
                    ctype = "text/html" if ".htm" in path_noq else "text/javascript"
            except OSError:
                pass

        name = re.sub(r"[^\w.\-]+", "_", path.strip("/")) or "index"
        if "." not in name.split("_")[-1] and "html" in ctype:
            name += ".html"
        (OUT / name[:150]).write_bytes(body)

        if any(t in ctype for t in ("html", "javascript", "json", "xml", "text")) or \
                path.lower().split("?")[0].endswith((".js", ".htm", ".html", ".asp", ".php")):
            text = body.decode("utf-8", "replace")
            base = path.split("?")[0].rsplit("/", 1)[0] + "/"
            for m in LINK_RE.finditer(text):
                link = next(g for g in m.groups() if g)
                if link.startswith(("http://", "https://", "/")):
                    queue.append(link)
                else:
                    queue.append(urllib.parse.urljoin(base, link))

    (OUT / "index.txt").write_text("\n".join(summary), encoding="utf-8")
    print(f"\nFertig: {count} Anfragen, Dateien in {OUT}")


if __name__ == "__main__":
    main()
