#!/usr/bin/env python3
"""sphere_web: the floating sphere as a web page, for checking usage from a phone or tablet.

It serves `web/index.html` (the sphere, the hover card's meters and the session list,
redrawn in a canvas) and `/api/state`, a JSON snapshot built from the same sources as
the desktop sphere: claude-maxer's usage snapshot and Claude Code's session registry
(claude_sessions.collect_sessions). Two actions: `POST /api/stop`, the popup's ■, takes a
session id and SIGTERMs that session's processes; `POST /api/new`, the popup's +, opens a
kitty window on the desktop running `claude` in one of the projects under PROJECTS_ROOT.

Tapping a session opens its chat (session_chat): `GET /api/chat` streams the transcript
by byte offset plus the dialog the session is stopped on, `POST /api/send` types a
prompt into its kitty window and `POST /api/keys` answers a dialog.

Run: python3 sphere_web.py   (SPHERE_WEB_HOST / SPHERE_WEB_PORT override 0.0.0.0:8765)
"""
import gzip
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_sessions import collect_sessions, shorten_path, stop_session, truncate  # noqa: E402
import session_chat  # noqa: E402

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
# The page embeds live state and the API is live; the rest only changes on a deploy.
STATIC_MAX_AGE = 86400
INITIAL_MARK = b"/*INITIAL_STATE*/null"
SNAPSHOT_PATH = os.path.expanduser("~/.claude/state/usage_snapshot.json")
CACHE_S = 1.5  # several open tabs polling at once share one registry scan
# Shared with claude_new.sh, so the desktop picker and the phone agree on "recent".
PROJECTS_ROOT = os.environ.get("CLAUDE_NEW_PROJECTS_ROOT", "/data/apps")
RECENT_FILE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                           "claude-monitor", "recent-dirs")
RECENT_MAX = 15
PROMPT_MAX = 4000
CHAT_PROMPT_MAX = 20000
STEPS_MAX = 24
# The client picks flags by these keys; it never sends argv text.
CLAUDE_JSON = os.path.expanduser("~/.claude.json")
FLAGS = {"skip_permissions": "--dangerously-skip-permissions", "rc": "--remote-control"}


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


def session_json(s):
    return {
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
        "headless": s["headless"],
    }


def find_session(target):
    """The live session with this id; the client only ever names one, never pids."""
    return next((s for s in collect_sessions()
                 if (s["session_id"] or f"pid:{s['pid']}") == target), None)


def chat_json(s, offset):
    """New transcript items since `offset`, the session's status and any open dialog."""
    path = session_chat.transcript_path(s["cwd"], s["session_id"]) if s["session_id"] else None
    chat = session_chat.read_chat(path, offset) if path else {"items": [], "offset": 0, "reset": True}
    win = None if s["headless"] else session_chat.find_window(s["pids"])
    dialog = None
    if win and s["status"] == "waiting":
        dialog = session_chat.parse_dialog(session_chat.read_screen(s["pids"]))
    return dict(chat, session=session_json(s), ctl=bool(win), dialog=dialog, now=time.time())


def build_state():
    sessions = [session_json(s) for s in collect_sessions()]
    # `now` lets the page compute time left against the PC's clock, not the phone's.
    return {"now": time.time(), "usage": read_usage(), "sessions": sessions}


_cache = {"at": 0.0, "body": b""}


def _read_recent():
    try:
        with open(RECENT_FILE) as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def list_projects():
    """Project directory names under PROJECTS_ROOT, recently used first."""
    try:
        names = sorted(e.name for e in os.scandir(PROJECTS_ROOT)
                       if e.is_dir() and not e.name.startswith("."))
    except OSError:
        return []
    root = os.path.abspath(PROJECTS_ROOT)
    recent = [os.path.basename(d) for d in _read_recent() if os.path.dirname(d.rstrip("/")) == root]
    recent = [n for n in dict.fromkeys(recent) if n in names]
    return [{"name": n, "recent": n in recent} for n in recent + [n for n in names if n not in recent]]


def record_recent(path):
    try:
        os.makedirs(os.path.dirname(RECENT_FILE), exist_ok=True)
        lines = list(dict.fromkeys([path] + _read_recent()))[:RECENT_MAX]
        tmp = RECENT_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, RECENT_FILE)
    except OSError:
        pass


def trust_project(path):
    """Pre-answer claude's "do you trust this folder?" dialog, as pressing y would.

    The answer lives in ~/.claude.json (projects[path].hasTrustDialogAccepted), a file
    every running claude rewrites; read it right before an atomic replace so the window
    for clobbering one of their writes is a few milliseconds.
    """
    try:
        with open(CLAUDE_JSON) as f:
            cfg = json.load(f)
        proj = cfg.setdefault("projects", {}).setdefault(path, {})
        if proj.get("hasTrustDialogAccepted"):
            return
        proj["hasTrustDialogAccepted"] = True
        tmp = f"{CLAUDE_JSON}.tmp.sphere{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CLAUDE_JSON)
    except (OSError, ValueError, AttributeError):
        pass  # worst case the dialog shows up and waits for a y


def start_session(project, flags, prompt):
    """Open kitty on the desktop running claude in PROJECTS_ROOT/<project>, like claude_new.sh."""
    # Only a name from the listing is accepted, so the client can't point it anywhere else.
    if project not in {p["name"] for p in list_projects()}:
        return "no such project"
    path = os.path.join(os.path.abspath(PROJECTS_ROOT), project)
    trust_project(path)
    argv = [FLAGS[k] for k in FLAGS if flags.get(k)]
    unit = "claude-new-%s-%d" % (re.sub(r"[^A-Za-z0-9_.-]", "_", project), time.time() * 1000)
    # systemd-run puts the terminal in its own transient unit: started straight from this
    # service it would sit in sphere-web's cgroup and die on every restart of it.
    # The label, flags and prompt travel as environment, never interpolated into the
    # command string (same as claude_new.sh); `zsh -ic` sources .zshrc for the proxy
    # exports, and `exec zsh -i` keeps the terminal open after the session ends.
    # --expand-environment=no: systemd would otherwise eat the zsh ${...} below itself.
    cmd = ["systemd-run", "--user", "--collect", "--quiet", "--expand-environment=no", f"--unit={unit}",
           f"--working-directory={path}",
           f"--setenv=CLAUDE_NEW_LABEL={project}",
           f"--setenv=CLAUDE_NEW_FLAGS={' '.join(argv)}",
           f"--setenv=CLAUDE_NEW_PROMPT={prompt}",
           "kitty", "--directory", path, "zsh", "-ic",
           'command claude --name "$CLAUDE_NEW_LABEL" ${=CLAUDE_NEW_FLAGS} '
           '${CLAUDE_NEW_PROMPT:+"$CLAUDE_NEW_PROMPT"}; exec zsh -i']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"launch failed: {e}"
    if r.returncode:
        return "launch failed: " + (r.stderr.strip().splitlines() or ["?"])[-1]
    record_recent(path)
    return None


def state_json():
    if time.monotonic() - _cache["at"] > CACHE_S:
        _cache["body"] = json.dumps(build_state()).encode()
        _cache["at"] = time.monotonic()
    return _cache["body"]


class Handler(BaseHTTPRequestHandler):
    server_version = "sphere_web"
    # From the phone every request crosses the sing-box/WireGuard tunnel, so a new TCP
    # connection per request (HTTP/1.0) cost a handshake each. Keep-alive reuses them;
    # the timeout frees the thread behind a connection the phone left idle.
    protocol_version = "HTTP/1.1"
    timeout = 60

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/state":
            self._send(200, "application/json", state_json())
        elif path in ("/api/chat", "/api/screen"):
            q = parse_qs(query)
            s = find_session((q.get("id") or [""])[0])
            if not s:
                return self._send(404, "application/json", b'{"ok": false, "error": "no such session"}')
            if path == "/api/screen":
                text = None if s["headless"] else session_chat.read_screen(s["pids"])
                return self._send(200, "application/json", json.dumps({"text": text}).encode())
            try:
                offset = int((q.get("offset") or [""])[0])
            except ValueError:
                offset = None
            self._send(200, "application/json", json.dumps(chat_json(s, offset)).encode())
        elif path == "/api/projects":
            self._send(200, "application/json", json.dumps({"projects": list_projects()}).encode())
        elif path in STATIC:
            name, ctype = STATIC[path]
            try:
                with open(os.path.join(WEB_DIR, name), "rb") as f:  # re-read: edits need no restart
                    body = f.read()
            except OSError:
                return self._send(404, "text/plain", b"not found")
            if name == "index.html":
                # Inline the current state so the first paint doesn't wait on /api/state.
                body = body.replace(INITIAL_MARK, state_json().replace(b"</", b"<\\/"), 1)
                self._send(200, ctype, body)
            else:
                self._send(200, ctype, body, max_age=STATIC_MAX_AGE)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        # Read the body before any early return: on a kept-alive connection, unread
        # bytes would be parsed as the next request.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= 16384:
            self.close_connection = True
            return self._send(400, "text/plain", b"bad request")
        raw = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        if path not in ("/api/stop", "/api/new", "/api/send", "/api/keys"):
            return self._send(404, "text/plain", b"not found")
        # Requiring a JSON body makes a cross-site request need a CORS preflight,
        # which this server never answers, so another web page open on the phone
        # can't fire stops or starts at it. A mismatched Origin is refused outright as well.
        origin = self.headers.get("Origin")
        if (self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                or (origin and origin.split("://", 1)[-1] != self.headers.get("Host"))):
            return self._send(403, "text/plain", b"forbidden")
        try:
            body = json.loads(raw or b"{}")
            target = body.get("id")
        except (ValueError, AttributeError):
            return self._send(400, "text/plain", b"bad request")
        if path == "/api/new":
            return self._new(body)
        # Resolve pids here from the live registry; the client only names a session.
        match = find_session(target)
        if not match:
            return self._send(404, "application/json", b'{"ok": false, "error": "no such session"}')
        if path in ("/api/send", "/api/keys"):
            return self._type(path, match, body)
        stop_session(match["pids"])
        _cache["at"] = 0.0  # next poll rescans instead of serving the pre-stop list
        self._send(200, "application/json", b'{"ok": true}')

    def _new(self, body):
        project, flags, prompt = body.get("project"), body.get("flags") or {}, body.get("prompt") or ""
        if not isinstance(project, str) or not isinstance(flags, dict) or not isinstance(prompt, str):
            return self._send(400, "text/plain", b"bad request")
        err = start_session(project, flags, prompt.strip()[:PROMPT_MAX])
        if err:
            return self._send(404 if err == "no such project" else 500, "application/json",
                              json.dumps({"ok": False, "error": err}).encode())
        _cache["at"] = 0.0
        self._send(200, "application/json", b'{"ok": true}')

    def _type(self, path, s, body):
        """Type into the session's terminal: a prompt (/api/send) or dialog keys (/api/keys)."""
        if s["headless"]:
            return self._fail(409, "headless session: nothing to type into")
        if path == "/api/send":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > CHAT_PROMPT_MAX:
                return self._send(400, "text/plain", b"bad request")
            # Typed into an open dialog, the prompt would pick options instead.
            if s["status"] == "waiting":
                return self._fail(409, "it's waiting on a dialog: answer that first")
            ok = session_chat.send_prompt(s["pids"], text.strip())
        else:
            steps = body.get("steps")
            if (not isinstance(steps, list) or not 0 < len(steps) <= STEPS_MAX
                    or not all(isinstance(x, dict) for x in steps)):
                return self._send(400, "text/plain", b"bad request")
            ok = session_chat.send_steps(s["pids"], steps)
        if not ok:
            return self._fail(409, "no kitty window with remote control for this session")
        _cache["at"] = 0.0
        self._send(200, "application/json", b'{"ok": true}')

    def _fail(self, code, error):
        self._send(code, "application/json", json.dumps({"ok": False, "error": error}).encode())

    def _send(self, code, ctype, body, max_age=0):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # The page is ~20 KB raw, more than TCP's first flight at the tunnel's MTU 1280;
        # gzipped it fits. PNGs are already compressed.
        if (len(body) > 1024 and not ctype.startswith("image/")
                and "gzip" in self.headers.get("Accept-Encoding", "")):
            body = gzip.compress(body, 6)
            self.send_header("Content-Encoding", "gzip")
        if not ctype.startswith("image/"):
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", f"max-age={max_age}" if max_age else "no-store")
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
