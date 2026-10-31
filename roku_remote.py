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
import os
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

PORT = int(os.environ.get("PORT", "8080"))
ECP_PORT = 8060
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
CONFIG = os.path.join(HERE, "config.json")   # themes + defaults, re-read on every page load

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


def device_info(ip):
    try:
        status, body, _ = ecp(ip, "GET", "/query/device-info", timeout=1.5)
        if status != 200:
            return None
        root = ET.fromstring(body)
    except (OSError, ET.ParseError, http.client.HTTPException):
        return None

    def get(tag):
        return (root.findtext(tag) or "").strip()

    return {
        "ip": ip,
        # Same name the Roku app shows: your custom name, else the room/location
        "name": get("user-device-name") or get("user-device-location")
                or get("friendly-device-name") or get("model-name") or ip,
        "model": get("friendly-model-name") or get("model-name"),
        "isTv": get("is-tv") == "true",
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
    msg = ("M-SEARCH * HTTP/1.1\r\n"
           "HOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\n'
           "MX: 1\r\n"
           "ST: roku:ecp\r\n\r\n").encode()
    found = set()
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
                found.add(m.group(1).decode())
    except OSError:
        pass
    finally:
        sock.close()
    return found


def scan_subnet():
    """Fallback when multicast is blocked: try port 8060 on every /24 address."""
    me = local_ip()
    if me.startswith("127."):
        return set()
    net = ipaddress.ip_network(f"{me}/24", strict=False)

    def probe(host):
        try:
            with socket.create_connection((str(host), ECP_PORT), timeout=0.4):
                return str(host)
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=64) as pool:
        return {ip for ip in pool.map(probe, net.hosts()) if ip}


def discover():
    ips = ssdp_search() or scan_subnet()
    with _devices_lock:
        ips |= set(_devices)          # re-check known ones (incl. manually added)
    with ThreadPoolExecutor(max_workers=16) as pool:
        infos = [i for i in pool.map(device_info, ips) if i]
    with _devices_lock:
        for info in infos:
            _devices[info["ip"]] = info
        return sorted(_devices.values(), key=lambda d: d["name"].lower())


def known(ip):
    with _devices_lock:
        return ip in _devices


def lock_for(ip):
    with _send_locks_lock:
        return _send_locks.setdefault(ip, threading.Lock())


# --------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "RokuRemote/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body=b"", ctype="application/json", cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode())

    # ---- GET
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)

        if url.path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
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

        if url.path == "/api/config":
            try:
                with open(CONFIG, encoding="utf-8") as f:
                    return self._json({"config": json.load(f)})
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
            return self._json({"devices": devs})

        if url.path in ("/api/apps", "/api/icon"):
            ip = q.get("ip", [""])[0]
            if not known(ip):
                return self._json({"error": "Unknown Roku"}, 404)
            try:
                if url.path == "/api/apps":
                    status, body, _ = ecp(ip, "GET", "/query/apps")
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

        ip = str(data.get("ip", ""))
        if not known(ip):
            return self._json({"error": "Unknown Roku - tap refresh"}, 404)

        try:
            with lock_for(ip):
                if path == "/api/key":
                    key = data.get("key")
                    if key not in KEYS:
                        return self._json({"error": "Unknown key"}, 400)
                    status, _, _ = ecp(ip, "POST", f"/keypress/{key}")
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


def main():
    if not os.path.exists(INDEX):
        print("Warning: index.html not found next to this script.")
    if not os.path.exists(CONFIG):
        print("Note: config.json not found - using the built-in default theme.")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=discover, daemon=True).start()   # warm the device list
    print("Roku remote is running.")
    print(f"  On your phone, open:  http://{local_ip()}:{PORT}")
    print("  (Phone and this computer must be on the same Wi-Fi.)  Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
