#!/usr/bin/env python3
"""sphere_web: the floating sphere as a web page, for checking usage from a phone or tablet.

It serves `web/index.html` (the sphere, the hover card's meters and the session list,
redrawn in a canvas) and `/api/state`, a JSON snapshot built from the same sources as
the desktop sphere: claude-maxer's usage snapshot and Claude Code's session registry
(claude_sessions.collect_sessions). The one action is `POST /api/stop`, the popup's ■:
it takes a session id and SIGTERMs that session's processes. It never starts one.

Run: python3 sphere_web.py   (SPHERE_WEB_HOST / SPHERE_WEB_PORT override 0.0.0.0:8765)
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_sessions import collect_sessions, shorten_path, stop_session, truncate  # noqa: E402

HOST = os.environ.get("SPHERE_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("SPHERE_WEB_PORT", "8765"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# Only these are served; nothing else under web/ (or outside it) is reachable.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/favicon.ico": ("favicon.png", "image/png"),
    "/favicon.png": ("favicon.png", "image/png"),
}
SNAPSHOT_PATH = os.path.expanduser("~/.claude/state/usage_snapshot.json")
CACHE_S = 1.5  # several open tabs polling at once share one registry scan


def read_usage():
    try:
        with open(SNAPSHOT_PATH) as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    rl = snap.get("rate_limits") or {}
    five, seven = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    return {
        "five_pct": five.get("used_percentage"),
        "five_reset": five.get("resets_at"),
        "seven_pct": seven.get("used_percentage"),
        "seven_reset": seven.get("resets_at"),
        "cached_at": snap.get("cached_at", 0),
    }


def build_state():
    sessions = [{
        "id": s["session_id"] or f"pid:{s['pid']}",
        "name": s["name"],
        "cwd": shorten_path(s["cwd"]),
        "status": s["status"],
        "waiting_for": s["waiting_for"],
        "status_since": s["status_since"],
        "task": truncate(s["task"], 160) if s["task"] else None,
        "tool": s["tool"],
        "model": s["model"],
        "procs": len(s["pids"]),
    } for s in collect_sessions()]
    # `now` lets the page compute time left against the PC's clock, not the phone's.
    return {"now": time.time(), "usage": read_usage(), "sessions": sessions}


_cache = {"at": 0.0, "body": b""}


class Handler(BaseHTTPRequestHandler):
    server_version = "sphere_web"

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            if time.monotonic() - _cache["at"] > CACHE_S:
                _cache["body"] = json.dumps(build_state()).encode()
                _cache["at"] = time.monotonic()
            self._send(200, "application/json", _cache["body"])
        elif path in STATIC:
            name, ctype = STATIC[path]
            try:
                with open(os.path.join(WEB_DIR, name), "rb") as f:  # re-read: edits need no restart
                    self._send(200, ctype, f.read())
            except OSError:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/stop":
            return self._send(404, "text/plain", b"not found")
        # Requiring a JSON body makes a cross-site request need a CORS preflight,
        # which this server never answers, so another web page open on the phone
        # can't fire stops at it. A mismatched Origin is refused outright as well.
        origin = self.headers.get("Origin")
        if (self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                or (origin and origin.split("://", 1)[-1] != self.headers.get("Host"))):
            return self._send(403, "text/plain", b"forbidden")
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            target = json.loads(self.rfile.read(length) or b"{}").get("id")
        except (ValueError, AttributeError):
            return self._send(400, "text/plain", b"bad request")
        # Resolve pids here from the live registry; the client only names a session.
        match = next((s for s in collect_sessions()
                      if (s["session_id"] or f"pid:{s['pid']}") == target), None)
        if not match:
            return self._send(404, "application/json", b'{"ok": false, "error": "no such session"}')
        stop_session(match["pids"])
        _cache["at"] = 0.0  # next poll rescans instead of serving the pre-stop list
        self._send(200, "application/json", b'{"ok": true}')

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # polled every few seconds; access logs would only be noise


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"sphere_web on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
