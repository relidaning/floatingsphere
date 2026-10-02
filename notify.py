"""
notify: a push notification on the phone when a Claude Code session needs you.

Runs as a thread in sphere_web (always up as a systemd service). Every POLL_S it reads
the session registry (claude_sessions.collect_sessions) and pushes (webpush) to every
device that turned notifications on in the web app, when a terminal session
  - stops on a dialog (status `waiting`): "Awaiting approval" (a permission prompt) or
    "Awaiting your answer", plus the tool call or question parsed off the screen
  - finishes a turn (`busy`/`waiting` -> `idle`): "Completed", plus the start of its reply
The title is the session's name. Text is kept plain (markdown and terminal glyphs
stripped, one line of detail) so it reads like any other app's notification.
Tapping the notification opens that session's chat in the web app.

A status has to hold for SETTLE_S before it counts, so a permission prompt answered
right away at the desk doesn't ping the phone. Nothing else delays it: the user wants the
phone to know about as quickly as the sphere's water does. (Holding back sessions focused
on the desktop was tried and dropped: it made the session in use the slowest to notify.) Headless runs (cron jobs)
are never sent; there's nobody to answer them.
"""
import os
import re
import threading
import time

from claude_sessions import collect_sessions
import session_chat
import webpush

POLL_S = 1          # a registry scan is ~0.1 ms
SETTLE_S = 3
DETAIL_MAX = 180    # the lock screen shows about three lines
REPLY_TAIL = 64 * 1024  # the last reply is at the very end; read_chat's chat-opening tail is 512 KB+


def _sid(s):
    return s["session_id"] or f"pid:{s['pid']}"


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def plain(text):
    """Markdown / terminal text as one plain line."""
    text = re.sub(r"```.*?(```|$)", " ", text or "", flags=re.S)       # code blocks
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)              # [label](url)
    text = re.sub(r"[`*_~]+|^\s*(#+|[-•>]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[│─╭╮╰╯❯⎿●✔☐☒]", " ", text)                          # box / cursor glyphs
    # Lines become sentences, so a heading or list item doesn't run into the next one.
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    text = " ".join(ln if ln[-1] in ".:;!?,)" else ln + "." for ln in lines if ln)
    return _clip(text, DETAIL_MAX)


def last_reply(s):
    """The session's last assistant text from its transcript tail."""
    if not s["session_id"]:
        return ""
    path = session_chat.transcript_path(s["cwd"], s["session_id"])
    try:
        size = os.path.getsize(path) if path else 0
    except OSError:
        return ""
    for read in (lambda: session_chat._read_items(path, max(0, size - REPLY_TAIL), size, True),
                 lambda: session_chat.read_chat(path)):
        text = next((it["text"] for it in reversed(read()["items"]) if it["k"] == "text"), "")
        if text or size <= REPLY_TAIL:
            return text
    return ""


def waiting_notice(s):
    """(status line, detail) for a session stopped on a dialog."""
    d = session_chat.parse_dialog(session_chat.read_screen(s["pids"]))
    lines = [ln.strip() for ln in (d["title"] if d else "").split("\n") if ln.strip()]
    # A permission prompt is the tool call ("Bash command", the command, its description)
    # then "Do you want to …?"; AskUserQuestion is the question itself.
    permission = (re.search(r"permission|approv", s["waiting_for"] or "", re.I)
                  or (lines and re.match(r"do you want to", lines[-1], re.I)))
    if permission:
        if lines and lines[-1].endswith("?"):
            lines = lines[:-1]
        detail = lines[0] + (": " + " — ".join(lines[1:3]) if len(lines) > 1 else "") if lines else ""
        return "Awaiting approval", plain(detail)
    question = next((ln for ln in reversed(lines) if ln.endswith("?")), lines[-1] if lines else "")
    return "Awaiting your answer", plain(question)


class Notifier(threading.Thread):
    def __init__(self):
        super().__init__(name="notifier", daemon=True)
        self.seen = {}  # sid -> {"status", "prev", "since", "done"}

    def run(self):
        first = True
        while True:
            try:
                self.tick(seed=first)
                first = False
            except Exception as e:  # noqa: BLE001 — never let one bad poll kill the thread
                print(f"notify: {e!r}", flush=True)
            time.sleep(POLL_S)

    def tick(self, seed=False, now=None):
        now = now or time.time()
        live = {}
        for s in collect_sessions():
            if s["headless"]:
                continue
            sid = _sid(s)
            live[sid] = st = self.seen.get(sid)
            if st is None or st["status"] != s["status"]:
                # Sessions already there when the service starts are taken as read.
                live[sid] = st = {"status": s["status"], "prev": st and st["status"], "since": now,
                                  "done": seed or st is None}
            if st["done"] or now - st["since"] < SETTLE_S:
                continue
            if s["status"] == "waiting":
                self.send(s, *waiting_notice(s))
            elif s["status"] == "idle" and st["prev"] in ("busy", "waiting"):
                self.send(s, "Completed", plain(last_reply(s)))
            st["done"] = True
        self.seen = live

    def send(self, s, status, detail):
        name = s["name"] or os.path.basename(s["cwd"] or "") or "Claude Code"
        sid = _sid(s)
        # tag: a newer notice about the same session replaces the old one on the phone.
        print(f"notify: {name}: {status}", flush=True)
        webpush.send_all({"title": name, "body": f"{status}\n{detail}" if detail else status,
                          "tag": sid, "id": sid})
