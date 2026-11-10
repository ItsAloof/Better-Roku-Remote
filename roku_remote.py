#!/usr/bin/env python3
"""
Roku Remote server
  - Finds Rokus on your network (SSDP, with a subnet-scan fallback)
  - Serves the remote page (index.html, kept next to this file)
  - Forwards button presses to the Roku's ECP API on port 8060

Run:   python3 roku_remote.py
Then:  open http://<this-computer's-ip>:8080 on your phone (same Wi-Fi)

Standard library only. Set PORT=xxxx to use a different port.
"""
import http.client
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

PORT = int(os.environ.get("PORT", "8080"))
ECP_PORT = 8060
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
LOG_FILE = os.path.join(HERE, "server.log")      # what happened while running in the background
FAKE_TV = os.path.join(HERE, "fake-roku.html")   # the virtual Roku TV screen (/?fake-roku)
DESKTOP = os.path.join(HERE, "desktop.html")    # the computer layout (/desktop)
CONFIG = os.path.join(HERE, "config.json")   # themes + defaults, re-read on every page load
SECRETS = os.path.join(HERE, "secrets.json") # your TMDB key; ignored by git, never committed
TMDB_BASE = os.environ.get("TMDB_BASE", "https://api.themoviedb.org/3")

KEYS = {
    "Home", "Back", "Select", "Up", "Down", "Left", "Right", "Info",
    "Rev", "Fwd", "Play", "InstantReplay", "Search", "Backspace", "Enter",
    "VolumeUp", "VolumeDown", "VolumeMute", "Power", "PowerOn", "PowerOff",
    "ChannelUp", "ChannelDown",
}
APP_ID_RE = re.compile(r"^[\w.\-]{1,64}$")

_devices = {}                    # ip -> info dict
_devices_lock = threading.Lock()
_send_locks = {}                 # ip -> lock, keeps presses to one Roku in order
_send_locks_lock = threading.Lock()


# ---------------------------------------------------------------- ECP helpers
def ecp(ip, method, path, timeout=2.5):
    conn = http.client.HTTPConnection(ip, ECP_PORT, timeout=timeout)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read(), resp.getheader("Content-Type", "")
    finally:
        conn.close()


def port_open(ip, timeout=0.6):
    try:
        with socket.create_connection((ip, ECP_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def device_info(ip, serial_hint=""):
    """Name/model/serial for a Roku. If the Roku is in Limited mode it refuses this
    query, so we still return a basic entry (keypresses usually work there)."""
    try:
        status, body, _ = ecp(ip, "GET", "/query/device-info", timeout=1.5)
        root = ET.fromstring(body) if status == 200 else None
    except (OSError, ET.ParseError, http.client.HTTPException):
        root, status = None, 0

    if root is None:
        if status == 0 and not port_open(ip):
            return None                      # nothing there
        return {"ip": ip, "id": serial_hint or ip, "name": f"Roku ({ip})", "model": "",
                "isTv": False, "limited": True}

    def get(tag):
        return (root.findtext(tag) or "").strip()

    return {
        "ip": ip,
        # The serial number follows the Roku if your router gives it a new IP,
        # so the app can never quietly end up controlling a different Roku.
        "id": get("serial-number") or get("device-id") or serial_hint or ip,
        # Same name the Roku app shows: your custom name, else the room/location
        "name": get("user-device-name") or get("user-device-location")
                or get("friendly-device-name") or get("model-name") or ip,
        "model": get("friendly-model-name") or get("model-name"),
        "isTv": get("is-tv") == "true",
        "limited": False,
    }


# ------------------------------------------------------------------ discovery
def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ssdp_search(wait=1.5):
    """Returns {ip: serial}. The serial comes from the USN line, which Rokus send
    even in Limited mode."""
    msg = ("M-SEARCH * HTTP/1.1\r\n"
           "HOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\n'
           "MX: 1\r\n"
           "ST: roku:ecp\r\n\r\n").encode()
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.3)
        for _ in range(2):
            sock.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + wait
        while time.time() < end:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            m = re.search(rb"LOCATION:\s*http://([\d.]+):8060", data, re.I)
            if m:
                usn = re.search(rb"USN:\s*uuid:roku:ecp:(\S+)", data, re.I)
                found[m.group(1).decode()] = usn.group(1).decode() if usn else ""
    except OSError:
        pass
    finally:
        sock.close()
    return found


def scan_subnet():
    """Fallback when multicast is blocked: try port 8060 on every /24 address."""
    me = local_ip()
    if me.startswith("127."):
        return {}
    net = ipaddress.ip_network(f"{me}/24", strict=False)

    def probe(host):
        return str(host) if port_open(str(host), 0.4) else None

    with ThreadPoolExecutor(max_workers=64) as pool:
        return {ip: "" for ip in pool.map(probe, net.hosts()) if ip}


def discover():
    found = ssdp_search() or scan_subnet()
    with _devices_lock:
        for ip, d in _devices.items():        # re-check known ones (incl. manually added)
            found.setdefault(ip, d["id"] if d["id"] != ip else "")
    with ThreadPoolExecutor(max_workers=16) as pool:
        infos = [i for i in pool.map(lambda kv: device_info(*kv), found.items()) if i]
    with _devices_lock:
        _devices.clear()                       # drop stale IPs (a Roku that moved)
        for info in infos:
            _devices[info["ip"]] = info
        return sorted(_devices.values(), key=lambda d: d["name"].lower())


# ------------------------------------------------- virtual Roku (/?fake-roku)
# A pretend Roku for testing the remote without a TV. Open /?fake-roku on any screen and
# "Virtual Roku" shows up in the remote's device list while that page is open. Presses
# are queued here and the page picks them up (long-poll), so it reacts instantly.
VIRTUAL = "virtual"
VIRTUAL_APPS = [  # id, name, tile colour, text colour
    ("12", "Netflix", "#e50914", "#fff"), ("837", "YouTube", "#ff0033", "#fff"),
    ("2285", "Hulu", "#1ce783", "#06291a"), ("13", "Prime Video", "#00a8e1", "#fff"),
    ("291097", "Disney+", "#113ccf", "#fff"), ("61322", "Max", "#002be7", "#fff"),
    ("593099", "Peacock", "#1b1b1b", "#fff"), ("151908", "The Roku Channel", "#6c3ab0", "#fff"),
]
_v = threading.Condition()
_v_events, _v_seq, _v_seen = [], 0, 0.0
_v_state = {"app": None, "player": None}


def virtual_active():
    return time.time() - _v_seen < 30


def virtual_push(event):
    global _v_seq
    with _v:
        _v_seq += 1
        _v_events.append((_v_seq, event))
        del _v_events[:-200]
        _v.notify_all()


def virtual_device():
    return {"ip": VIRTUAL, "id": VIRTUAL, "name": "Virtual Roku", "model": "Demo TV",
            "isTv": True, "limited": False}


def virtual_icon(app_id):
    app = next((a for a in VIRTUAL_APPS if a[0] == app_id), None)
    if not app:
        return None
    _, name, bg, fg = app
    size = 34 if len(name) < 9 else 24
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 290 218"><rect width="290" height="218" fill="{bg}"/>'
            f'<text x="145" y="118" font-family="Helvetica,Arial,sans-serif" font-weight="700" font-size="{size}" '
            f'fill="{fg}" text-anchor="middle">{name}</text></svg>').encode()


def known(ip):
    """Is ip a Roku we may talk to? If it isn't in the list yet (say the server just
    restarted), check that it's a local address answering on the Roku port."""
    if ip == VIRTUAL:
        return virtual_active()
    with _devices_lock:
        if ip in _devices:
            return True
    try:
        if not ipaddress.ip_address(ip).is_private:
            return False
    except ValueError:
        return False
    info = device_info(ip)
    if not info:
        return False
    with _devices_lock:
        _devices[ip] = info
    return True


def lock_for(ip):
    with _send_locks_lock:
        return _send_locks.setdefault(ip, threading.Lock())


# ---------------------------------------------------------- TMDB (show search)
_tmdb_cache = {}                 # url -> (time, data); keeps repeat searches instant
_tmdb_lock = threading.Lock()


def tmdb_settings():
    """(key, region). Key comes from TMDB_API_KEY or secrets.json; region from config.json."""
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        try:
            with open(SECRETS, encoding="utf-8") as f:
                key = str(json.load(f).get("tmdb_api_key", "")).strip()
        except (OSError, ValueError):
            key = ""
    region = "US"
    try:
        with open(CONFIG, encoding="utf-8") as f:
            region = str(json.load(f).get("tmdb_region", "US")).upper()[:2] or "US"
    except (OSError, ValueError):
        pass
    return key, region


def tmdb_get(path, params):
    key, _ = tmdb_settings()
    if not key:
        raise LookupError("no key")
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ"):                  # v4 "API Read Access Token"
        headers["Authorization"] = "Bearer " + key
    else:                                      # v3 "API Key"
        params = {**params, "api_key": key}
    url = f"{TMDB_BASE}{path}?{urlencode(params)}"
    now = time.time()
    with _tmdb_lock:
        hit = _tmdb_cache.get(url)
        if hit and now - hit[0] < 600:
            return hit[1]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
        data = json.load(r)
    with _tmdb_lock:
        if len(_tmdb_cache) > 300:
            _tmdb_cache.clear()
        _tmdb_cache[url] = (now, data)
    return data


def tmdb_item(r, kind):
    when = r.get("release_date") or r.get("first_air_date") or ""
    return {
        "id": r.get("id"), "type": kind,
        "title": r.get("title") or r.get("name") or "",
        "date": when, "year": when[:4], "overview": r.get("overview") or "",
        "poster": r.get("poster_path"), "rating": round(r.get("vote_average") or 0, 1),
        "votes": r.get("vote_count") or 0,
    }


def tmdb_search(q):
    data = tmdb_get("/search/multi", {"query": q, "include_adult": "false", "language": "en-US", "page": 1})
    return [tmdb_item(r, r["media_type"]) for r in data.get("results", [])
            if r.get("media_type") in ("movie", "tv")][:20]


# ------------------------------------------------------------ Discover (browse)
DISCOVER_SORTS = ("best", "popular", "newest", "top")


def discover_settings():
    """Best = recent titles (last N months) sorted by rating, newest first on ties."""
    window, min_votes = 12, 40
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        window = max(1, min(120, int(c.get("best_window_months", window))))
        min_votes = max(0, min(5000, int(c.get("best_min_votes", min_votes))))
    except (OSError, ValueError, TypeError):
        pass
    return window, min_votes


def tmdb_genres(kind):
    data = tmdb_get(f"/genre/{kind}/list", {"language": "en-US"})
    return [{"id": g["id"], "name": g["name"]} for g in data.get("genres", [])]


def tmdb_provider_list(kind):
    _, region = tmdb_settings()
    data = tmdb_get(f"/watch/providers/{kind}", {"watch_region": region, "language": "en-US"})
    return [{"id": p.get("provider_id"), "name": p.get("provider_name")} for p in data.get("results", [])]


def tmdb_discover(kind, sort, genre, page, providers):
    _, region = tmdb_settings()
    today = date.today()
    when = "primary_release_date" if kind == "movie" else "first_air_date"
    small = kind == "tv"                      # TV shows get fewer reviews, so need lower bars
    params = {"include_adult": "false", "language": "en-US", "page": page,
              f"{when}.lte": today.isoformat()}  # only things already out
    if genre:
        params["with_genres"] = genre
    if providers:                              # "On my apps": streaming on services this Roku has
        params.update({"with_watch_providers": providers, "watch_region": region,
                       "with_watch_monetization_types": "flatrate|free|ads"})
    if sort == "popular":
        params["sort_by"] = "popularity.desc"
    elif sort == "newest":
        params.update({"sort_by": f"{when}.desc", "vote_count.gte": 5 if small else 10})
    elif sort == "top":
        params.update({"sort_by": "vote_average.desc", "vote_count.gte": 150 if small else 300})
    else:                                      # best
        window, min_votes = discover_settings()
        params.update({"sort_by": "vote_average.desc",
                       f"{when}.gte": (today - timedelta(days=round(window * 30.44))).isoformat(),
                       "vote_count.gte": max(5, min_votes // 2) if small else min_votes})
    data = tmdb_get(f"/discover/{kind}", params)
    items = [tmdb_item(r, kind) for r in data.get("results", [])]
    if sort == "best":                         # highest rated first; same rating -> newest first
        items.sort(key=lambda r: r["date"], reverse=True)
        items.sort(key=lambda r: r["rating"], reverse=True)
    return {"results": items, "page": data.get("page", page), "pages": min(data.get("total_pages") or 1, 500)}


def tmdb_providers(kind, tmdb_id):
    _, region = tmdb_settings()
    data = tmdb_get(f"/{kind}/{int(tmdb_id)}/watch/providers", {})
    here = data.get("results", {}).get(region, {})
    groups = {}
    for group in ("flatrate", "free", "ads", "rent", "buy"):
        groups[group] = [{"id": p.get("provider_id"), "name": p.get("provider_name"), "logo": p.get("logo_path")}
                         for p in sorted(here.get(group, []), key=lambda p: p.get("display_priority", 99))]
    return {"region": region, "link": here.get("link"), **groups}


# --------------------------------------------------------------- HTTP server
log = logging.getLogger("roku_remote")
EXIT_PORT_IN_USE = 3


class Handler(BaseHTTPRequestHandler):
    server_version = "RokuRemote/1.0"
    # Give up on a connection that goes quiet for a minute (a phone that went to sleep or
    # left Wi-Fi mid-request). Without this, each one held a thread forever, and over a
    # day or more they could pile up until the server stopped answering.
    timeout = 60

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body=b"", ctype="application/json", cache="no-store"):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass    # the page closed or reloaded before we answered; nothing to do

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode())

    # ---- GET
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query, keep_blank_values=True)

        if url.path == "/api/ping":
            return self._json({"ok": True})

        if url.path in ("/", "/index.html", "/fake-roku", "/desktop"):
            page = (FAKE_TV if (url.path == "/fake-roku" or "fake-roku" in q)
                    else DESKTOP if url.path == "/desktop" else INDEX)
            try:
                with open(page, "rb") as f:
                    body = f.read()
            except OSError:
                return self._json({"error": "index.html not found next to roku_remote.py"}, 500)
            return self._send(200, body, "text/html; charset=utf-8")

        if url.path.startswith("/icons/"):
            name = os.path.basename(url.path)
            path = os.path.join(HERE, "icons", name)
            if not re.fullmatch(r"[\w.-]+\.png", name) or not os.path.isfile(path):
                return self._send(404, b"", "text/plain")
            with open(path, "rb") as f:
                return self._send(200, f.read(), "image/png", cache="max-age=86400")

        if url.path == "/manifest.webmanifest":
            manifest = {
                "name": "Better Roku Remote", "short_name": "Remote", "start_url": "/",
                "display": "standalone", "background_color": "#0a0418", "theme_color": "#0a0418",
                "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}],
            }
            return self._send(200, json.dumps(manifest).encode(), "application/manifest+json")

        if url.path in ("/api/tmdb/search", "/api/tmdb/providers", "/api/tmdb/discover",
                        "/api/tmdb/genres", "/api/tmdb/provider-list"):
            try:
                if url.path == "/api/tmdb/search":
                    query = q.get("q", [""])[0].strip()[:100]
                    return self._json({"results": tmdb_search(query) if query else []})
                if url.path in ("/api/tmdb/discover", "/api/tmdb/genres", "/api/tmdb/provider-list"):
                    kind = q.get("type", ["movie"])[0]
                    if kind not in ("movie", "tv"):
                        return self._json({"error": "Bad request"}, 400)
                    if url.path == "/api/tmdb/genres":
                        return self._json({"genres": tmdb_genres(kind)})
                    if url.path == "/api/tmdb/provider-list":
                        return self._json({"providers": tmdb_provider_list(kind)})
                    sort = q.get("sort", ["best"])[0]
                    genre = q.get("genre", [""])[0]
                    page = q.get("page", ["1"])[0]
                    providers = q.get("providers", [""])[0]
                    if (sort not in DISCOVER_SORTS or not re.fullmatch(r"\d{0,6}", genre)
                            or not re.fullmatch(r"\d{1,3}", page) or not re.fullmatch(r"[\d|]{0,300}", providers)):
                        return self._json({"error": "Bad request"}, 400)
                    return self._json(tmdb_discover(kind, sort, genre, max(1, min(500, int(page))), providers))
                kind, tid = q.get("type", [""])[0], q.get("id", [""])[0]
                if kind not in ("movie", "tv") or not tid.isdigit():
                    return self._json({"error": "Bad request"}, 400)
                return self._json(tmdb_providers(kind, tid))
            except LookupError:
                return self._json({"error": "Add your TMDB key to secrets.json to search shows."}, 503)
            except urllib.error.HTTPError as e:
                msg = "TMDB didn't accept the key in secrets.json." if e.code == 401 else f"TMDB answered {e.code}."
                return self._json({"error": msg}, 502)
            except (OSError, ValueError):
                return self._json({"error": "Couldn't reach TMDB. Is the computer online?"}, 502)

        if url.path == "/api/config":
            try:
                with open(CONFIG, encoding="utf-8") as f:
                    cfg = json.load(f)
                    cfg["tmdb"] = bool(tmdb_settings()[0])     # tells the app whether show search is set up
                    return self._json({"config": cfg})
            except FileNotFoundError:
                return self._json({"config": None})
            except (OSError, ValueError) as e:
                return self._json({"config": None, "error": f"config.json has a mistake: {e}"})

        if url.path == "/api/devices":
            with _devices_lock:
                have = bool(_devices)
            if "refresh" in q or not have:
                devs = discover()
            else:
                with _devices_lock:
                    devs = sorted(_devices.values(), key=lambda d: d["name"].lower())
            if virtual_active():
                devs = devs + [virtual_device()]
            return self._json({"devices": devs})

        if url.path == "/api/virtual/events":       # the TV page waits here for presses
            global _v_seen
            _v_seen = time.time()
            try:
                after = int(q.get("after", ["-1"])[0])
            except ValueError:
                after = -1
            with _v:
                if 0 <= after <= _v_seq:
                    _v.wait_for(lambda: _v_seq > after, timeout=20)
                events = [e for n, e in _v_events if n > after] if 0 <= after <= _v_seq else []
                seq = _v_seq
            _v_seen = time.time()
            return self._json({"seq": seq, "events": events,
                               "apps": [{"id": a[0], "name": a[1], "bg": a[2], "fg": a[3]} for a in VIRTUAL_APPS]})

        if url.path == "/api/now":
            ip = q.get("ip", [""])[0]
            if not known(ip):
                return self._json({"error": "Unknown Roku"}, 404)
            if ip == VIRTUAL:
                return self._json(_v_state)
            out = {"app": None, "player": None}
            try:
                status, body, _ = ecp(ip, "GET", "/query/active-app", timeout=1.5)
                if status == 200:
                    app = ET.fromstring(body).find("app")
                    if app is not None and app.get("id"):          # no id = Roku home screen
                        out["app"] = {"id": app.get("id"), "name": (app.text or "").strip()}
                elif status == 403:
                    out["limited"] = True
                if out["app"]:
                    status, body, _ = ecp(ip, "GET", "/query/media-player", timeout=1.5)
                    if status == 200:
                        root = ET.fromstring(body)

                        def ms(tag):
                            m = re.match(r"\s*(\d+)", root.findtext(tag) or "")
                            return int(m.group(1)) if m else None

                        out["player"] = {"state": root.get("state"), "position": ms("position"),
                                         "duration": ms("duration"),
                                         "live": (root.findtext("is_live") or "").strip() == "true"}
            except (OSError, ET.ParseError, http.client.HTTPException):
                pass
            return self._json(out)

        if url.path in ("/api/apps", "/api/icon"):
            ip = q.get("ip", [""])[0]
            if not known(ip):
                return self._json({"error": "Unknown Roku"}, 404)
            if ip == VIRTUAL:
                if url.path == "/api/apps":
                    return self._json({"apps": [{"id": a[0], "name": a[1], "type": "appl"} for a in VIRTUAL_APPS]})
                svg = virtual_icon(q.get("id", [""])[0])
                return self._send(200, svg, "image/svg+xml", cache="max-age=86400") if svg else self._send(404, b"", "text/plain")
            try:
                if url.path == "/api/apps":
                    status, body, _ = ecp(ip, "GET", "/query/apps")
                    if status == 403:
                        return self._json({"error": "This Roku is in Limited mode, so it won't share its app list. "
                                           "Set Control by mobile apps to Enabled to use Apps."}, 403)
                    if status != 200:
                        return self._json({"error": f"Roku answered {status}"}, 502)
                    root = ET.fromstring(body)
                    apps = [{"id": a.get("id"), "name": (a.text or "").strip(),
                             "type": a.get("type")} for a in root.findall("app")]
                    return self._json({"apps": apps})

                app_id = q.get("id", [""])[0]
                if not APP_ID_RE.match(app_id):
                    return self._json({"error": "Bad app id"}, 400)
                status, body, ctype = ecp(ip, "GET", f"/query/icon/{quote(app_id)}")
                if status != 200 or not body:
                    return self._send(404, b"", "text/plain")
                return self._send(200, body, ctype or "image/png", cache="max-age=86400")
            except (OSError, ET.ParseError, http.client.HTTPException):
                return self._json({"error": "Couldn't reach the Roku"}, 502)

        self._json({"error": "Not found"}, 404)

    # ---- POST
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self._json({"error": "Bad request"}, 400)

        if path == "/api/add":
            ip = str(data.get("ip", "")).strip()
            try:
                if not ipaddress.ip_address(ip).is_private:
                    raise ValueError
            except ValueError:
                return self._json({"error": "Enter a local IP like 192.168.1.50"}, 400)
            info = device_info(ip)
            if not info:
                return self._json({"error": f"No Roku answered at {ip}"}, 404)
            with _devices_lock:
                _devices[ip] = info
            return self._json(info)

        if path == "/api/virtual/state":            # the TV page reports what's playing
            global _v_state
            app, player = data.get("app"), data.get("player")
            _v_state = {"app": app if isinstance(app, dict) else None,
                        "player": player if isinstance(player, dict) else None}
            return self._json({"ok": True})

        ip = str(data.get("ip", ""))
        if not known(ip):
            return self._json({"error": "Unknown Roku - tap refresh"}, 404)

        if ip == VIRTUAL:
            if path == "/api/key" and data.get("key") in KEYS and data.get("action", "press") in ("press", "down", "up"):
                virtual_push({"type": "key", "key": data["key"], "action": data.get("action", "press")})
            elif path == "/api/launch" and APP_ID_RE.match(str(data.get("id", ""))):
                virtual_push({"type": "launch", "id": str(data["id"])})
            elif path == "/api/text":
                virtual_push({"type": "text", "text": str(data.get("text", ""))[:200]})
            elif path == "/api/search":
                virtual_push({"type": "search", "q": str(data.get("q", ""))[:100]})
            else:
                return self._json({"error": "Bad request"}, 400)
            return self._json({"ok": True})

        try:
            with lock_for(ip):
                if path == "/api/key":
                    key = data.get("key")
                    # press = a normal tap; down/up = finger on / finger off, so the Roku
                    # can tell a tap from a hold (e.g. long-pressing OK)
                    verb = {"press": "keypress", "down": "keydown", "up": "keyup"}.get(data.get("action", "press"))
                    if key not in KEYS or not verb:
                        return self._json({"error": "Unknown key"}, 400)
                    status, _, _ = ecp(ip, "POST", f"/{verb}/{key}")
                elif path == "/api/launch":
                    app_id = str(data.get("id", ""))
                    if not APP_ID_RE.match(app_id):
                        return self._json({"error": "Bad app id"}, 400)
                    status, _, _ = ecp(ip, "POST", f"/launch/{quote(app_id)}")
                elif path == "/api/search":
                    # Opens Roku's own search screen with results for the keyword
                    q = str(data.get("q", "")).strip()[:100]
                    if not q:
                        return self._json({"error": "Type something to search"}, 400)
                    status, _, _ = ecp(ip, "POST", "/search/browse?keyword=" + quote(q, safe=""))
                elif path == "/api/text":
                    status = 200
                    for ch in str(data.get("text", ""))[:200]:
                        status, _, _ = ecp(ip, "POST", "/keypress/Lit_" + quote(ch, safe=""))
                        if status >= 300:
                            break
                else:
                    return self._json({"error": "Not found"}, 404)
        except (OSError, http.client.HTTPException):
            return self._json({"error": "Couldn't reach the Roku"}, 502)

        if status == 403:
            return self._json({"error": "Roku blocked it: turn on Settings > System > "
                               "Advanced > Control by mobile apps"}, 403)
        if status >= 300:
            return self._json({"error": f"Roku answered {status}"}, 502)
        return self._json({"ok": True})


def setup_logging():
    """server.log next to the script, kept small (about 1.5 MB at most across 3 files)."""
    try:
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8")
    except OSError:
        return
    h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def heartbeat():
    """One line an hour, so the log shows the server was alive and whether threads pile up."""
    while True:
        time.sleep(3600)
        with _devices_lock:
            n = len(_devices)
        log.info("still running - %d threads, %d Rokus known", threading.active_count(), n)


def main():
    setup_logging()
    if not os.path.exists(INDEX):
        print("Warning: index.html not found next to this script.")
    if not os.path.exists(CONFIG):
        print("Note: config.json not found - using the built-in default theme.")

    class Server(ThreadingHTTPServer):
        block_on_close = False      # don't keep a list of every request thread (leaked memory on older Pythons)

        def handle_error(self, request, client_address):
            # A browser hanging up mid-request is normal (closed tab, reload, phone asleep): stay quiet.
            exc = sys.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
                return
            log.exception("error handling a request from %s", client_address[0])
            super().handle_error(request, client_address)

    try:
        server = Server(("0.0.0.0", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is already in use - the remote is probably already running")
        print("(for example in the background from autostart). Nothing else to do.")
        sys.exit(EXIT_PORT_IN_USE)
    log.info("server started on port %d (Python %s, %s)", PORT, platform.python_version(), platform.platform())
    threading.Thread(target=discover, daemon=True).start()   # warm the device list
    threading.Thread(target=heartbeat, daemon=True).start()
    print("Roku remote is running.")
    print(f"  On your phone, open:  http://{local_ip()}:{PORT}")
    print("  (Phone and this computer must be on the same Wi-Fi.)  Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("server crashed")
        raise


# ------------------------------------------------------------------ supervisor
# Running `python roku_remote.py` starts a small supervisor that runs the real server as a
# child process, checks every 30 seconds that it still answers, and restarts it if it
# stops answering or exits. That way the remote heals itself even when it runs hidden in
# the background. Use --no-supervise to run the server directly (for debugging).
HEALTH_EVERY = float(os.environ.get("ROKU_HEALTH_EVERY", "30"))


def slog(msg):
    """The supervisor's own log lines (opened and closed each time so the server can rotate the file)."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [supervisor] {msg}\n")
    except OSError:
        pass


def ping(timeout=10):
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
        try:
            conn.request("GET", "/api/ping")
            return conn.getresponse().status == 200
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def nice(seconds):
    m = int(seconds // 60)
    return f"{m // 1440}d {m // 60 % 24}h {m % 60}m" if m >= 1440 else f"{m // 60}h {m % 60}m" if m >= 60 else f"{int(seconds)}s"


def supervise():
    cmd = [sys.executable, os.path.abspath(__file__)]
    env = {**os.environ, "ROKU_REMOTE_CHILD": "1"}
    quick_exits = 0
    while True:
        started = time.time()
        child = subprocess.Popen(cmd, env=env)
        misses, killed = 0, False
        try:
            while child.poll() is None:
                time.sleep(HEALTH_EVERY)
                if child.poll() is not None or time.time() - started < 20:
                    continue
                if ping():
                    misses = 0
                    continue
                misses += 1
                slog(f"server didn't answer the health check ({misses}/3)")
                if misses >= 3:
                    slog(f"server stopped answering after {nice(time.time() - started)} - restarting it")
                    child.kill()
                    child.wait()
                    killed = True
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.kill()
            return
        code = child.returncode
        if code == EXIT_PORT_IN_USE:
            return                                   # another copy is already running
        up = time.time() - started
        if not killed:
            slog(f"server exited (code {code}) after {nice(up)} - restarting it")
        quick_exits = quick_exits + 1 if up < 60 else 0
        time.sleep(min(60, 2 * 2 ** min(quick_exits, 5)))   # back off if it keeps crashing right away


if __name__ == "__main__":
    if os.environ.get("ROKU_REMOTE_CHILD") or "--no-supervise" in sys.argv:
        main()
    else:
        supervise()
