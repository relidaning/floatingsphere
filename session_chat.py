"""
Talk to a running Claude Code session from the web view: read its conversation,
type a prompt into it, and answer the dialogs it stops on.

  conversation   the session's transcript ~/.claude/projects/<slug>/<sessionId>.jsonl,
                 read incrementally by byte offset and boiled down to chat items.
  typing, keys   kitty remote control (`kitty @ send-text` / `send-key`) into the
                 terminal window the session runs in. Needs `allow_remote_control
                 socket-only` + `listen_on unix:${XDG_RUNTIME_DIR}/kitty-{kitty_pid}`
                 in kitty.conf (dotfiles), so only kitty windows opened since then work.
  dialogs        permission prompts and AskUserQuestion aren't in the transcript until
                 answered, so they're parsed off the screen (`kitty @ get-text`): the
                 numbered options under the last horizontal rule. A digit picks one,
                 which is what the terminal user would press.

Claude Code's own peer socket (`messagingSocketPath`) was not used: it delivers
"messages from another session", which are framed as untrusted and can't answer a dialog.
"""
import glob
import json
import os
import re
import subprocess
import time

from claude_sessions import PROJECTS_DIR, _has_ancestor, _project_slug

KITTY = "kitty"
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
INITIAL_BYTES = 512 * 1024    # transcript tail read when a chat opens...
INITIAL_ITEMS = 120           # ...grown 4x at a time until it holds this many items
INITIAL_MAX_BYTES = 32 * 1024 * 1024  # (a screenshot in a tool result is a line of MBs)
RESULT_MAX = 600              # chars of a tool result kept for the phone
TEXT_MAX = 20000
# What the phone may press. Digits go as text; the rest are kitty key names.
KEYS = {"enter", "escape", "up", "down", "left", "right", "tab", "shift+tab", "backspace"}
STEP_GAP_S = 0.25             # let Ink redraw between keys (a digit opens a text field)


# ---------------------------------------------------------------- transcript

def transcript_path(cwd, session_id):
    path = os.path.join(PROJECTS_DIR, _project_slug(cwd), f"{session_id}.jsonl")
    if os.path.exists(path):
        return path
    # The registry's cwd is the session's current one; the transcript stays where it started.
    found = glob.glob(os.path.join(PROJECTS_DIR, "*", f"{session_id}.jsonl"))
    return found[0] if found else None


_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_TAG = lambda name, s: (re.search(rf"<{name}>(.*?)</{name}>", s, re.S) or [None, None])[1]  # noqa: E731


def _clip(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _short_path(p):
    p = str(p or "")
    parts = p.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else p


def tool_summary(name, inp):
    """One line saying what a tool call does, like the terminal's `● Bash(…)`."""
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        return (inp.get("command") or "").strip().splitlines()[0][:200] if inp.get("command") else ""
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return _short_path(inp.get("file_path") or inp.get("notebook_path"))
    if name in ("Grep", "Glob"):
        return inp.get("pattern") or ""
    if name == "WebFetch":
        return inp.get("url") or ""
    if name == "WebSearch":
        return inp.get("query") or ""
    if name in ("Task", "Agent"):
        return inp.get("description") or ""
    if name == "Skill":
        return inp.get("skill") or ""
    if name == "TodoWrite":
        return f"{len(inp.get('todos') or [])} todos"
    if name == "AskUserQuestion":
        return " · ".join(q.get("question", "") for q in inp.get("questions") or [])
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0][:200]
    return ""


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "image":
                out.append("[image]")
        return "\n".join(out)
    return ""


def _user_items(entry, content):
    """Chat items for a `user` entry: a prompt, a slash command, or tool results."""
    uid, ts = entry.get("uuid"), entry.get("timestamp")
    if isinstance(content, str):
        if content.startswith("<local-command-caveat>"):
            return []
        cmd = _TAG("command-name", content)
        if cmd:
            args = (_TAG("command-args", content) or "").strip()
            return [{"k": "cmd", "id": uid, "ts": ts, "text": f"{cmd} {args}".strip()}]
        out = _TAG("local-command-stdout", content)
        if out is not None:
            return [{"k": "note", "id": uid, "ts": ts, "text": _clip(out, RESULT_MAX)}] if out.strip() else []
        text = _REMINDER.sub("", content).strip()
        if not text:
            return []
        if text.startswith("[Request interrupted"):
            return [{"k": "note", "id": uid, "ts": ts, "text": "interrupted"}]
        return [{"k": "user", "id": uid, "ts": ts, "text": _clip(text, TEXT_MAX)}]
    items, texts = [], []
    for i, b in enumerate(content or []):
        t = b.get("type")
        if t == "tool_result":
            items.append({"k": "result", "id": f"{uid}:{i}", "tool": b.get("tool_use_id"),
                          "err": bool(b.get("is_error")),
                          "text": _clip(_REMINDER.sub("", _result_text(b.get("content"))), RESULT_MAX)})
        elif t == "text":
            s = _REMINDER.sub("", b.get("text") or "").strip()
            if s and not s.startswith("[Request interrupted"):
                texts.append(s)
            elif s:
                items.append({"k": "note", "id": f"{uid}:{i}", "ts": ts, "text": "interrupted"})
        elif t == "image":
            texts.append("[image]")
    if texts:
        items.insert(0, {"k": "user", "id": uid, "ts": ts, "text": _clip("\n\n".join(texts), TEXT_MAX)})
    return items


def entry_items(entry):
    kind = entry.get("type")
    if entry.get("isSidechain") or entry.get("isMeta"):
        return []
    if kind == "system" and entry.get("subtype") == "compact_boundary":
        return [{"k": "note", "id": entry.get("uuid"), "ts": entry.get("timestamp"),
                 "text": "conversation compacted"}]
    message = entry.get("message") or {}
    if kind == "user":
        if entry.get("isCompactSummary"):
            return []
        return _user_items(entry, message.get("content"))
    if kind != "assistant":
        return []
    items = []
    for i, b in enumerate(message.get("content") or []):
        bid = f"{entry.get('uuid')}:{i}"
        if b.get("type") == "text" and (b.get("text") or "").strip():
            items.append({"k": "text", "id": bid, "ts": entry.get("timestamp"), "text": _clip(b["text"], TEXT_MAX)})
        elif b.get("type") == "tool_use":
            items.append({"k": "tool", "id": bid, "tool": b.get("id"), "name": b.get("name"),
                          "arg": tool_summary(b.get("name"), b.get("input"))})
    return items


def read_chat(path, offset=None):
    """Chat items from `offset` (a byte position this returned before) to the end.

    Without an offset it reads the tail, enough for INITIAL_ITEMS items. Only whole
    lines are consumed, so a line claude is still writing is picked up on the next call.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"items": [], "offset": 0, "reset": True}
    if offset is None or offset > size:
        span = INITIAL_BYTES
        while True:
            got = _read_items(path, max(0, size - span), size, True)
            if len(got["items"]) >= INITIAL_ITEMS or span >= min(size, INITIAL_MAX_BYTES):
                if len(got["items"]) > 3 * INITIAL_ITEMS:
                    got.update(items=got["items"][-3 * INITIAL_ITEMS:], truncated=True)
                return got
            span *= 4
    return _read_items(path, offset, size, False)


def _read_items(path, start, size, reset):
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(size - start)
    if reset and start > 0:
        cut = data.find(b"\n") + 1
        start, data = start + cut, data[cut:]
    end = data.rfind(b"\n") + 1
    items = []
    for line in data[:end].split(b"\n"):
        if not line.startswith(b"{"):
            continue
        try:
            items.extend(entry_items(json.loads(line)))
        except (ValueError, AttributeError):
            continue
    return {"items": items, "offset": start + end, "reset": reset, "truncated": reset and start > 0}


# ---------------------------------------------------------------- kitty

def _kitty(sock, *args, timeout=3):
    try:
        r = subprocess.run([KITTY, "@", "--to", f"unix:{sock}", *args],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _sockets():
    out = []
    for sock in glob.glob(os.path.join(RUNTIME_DIR, "kitty-*")):
        pid = sock.rsplit("-", 1)[-1]
        if pid.isdigit() and os.path.isdir(f"/proc/{pid}"):
            out.append(sock)
    return out


_windows = {}  # tuple(pids) -> (socket, window id)
_no_window = {}  # tuple(pids) -> when it was last looked for and not found
NO_WINDOW_RETRY_S = 20  # headless runs never get one; don't re-list kitty every poll


def find_window(pids):
    """(kitty socket, window id) of the terminal a session runs in, or None."""
    key = tuple(sorted(pids))
    if key in _windows:
        return _windows[key]
    if time.monotonic() - _no_window.get(key, -NO_WINDOW_RETRY_S) < NO_WINDOW_RETRY_S:
        return None
    for sock in _sockets():
        try:
            tree = json.loads(_kitty(sock, "ls") or "[]")
        except ValueError:
            continue
        for osw in tree:
            for tab in osw.get("tabs", []):
                for w in tab.get("windows", []):
                    fg = {p.get("pid") for p in w.get("foreground_processes") or []}
                    for pid in pids:
                        if pid in fg or pid == w.get("pid") or _has_ancestor(pid, {w.get("pid")}):
                            _windows[key] = (sock, w["id"])
                            return _windows[key]
    _no_window[key] = time.monotonic()
    return None


def _in_window(pids, *args):
    """Run a kitty @ command against the session's window, re-locating it once if it moved."""
    for _ in range(2):
        win = find_window(pids)
        if not win:
            return None
        out = _kitty(win[0], *args[:1], "--match", f"id:{win[1]}", *args[1:])
        if out is not None:
            return out
        _windows.pop(tuple(sorted(pids)), None)
    return None


def read_screen(pids):
    return _in_window(pids, "get-text")


def send_prompt(pids, text):
    """Type `text` into the session's prompt and submit it. False if it has no kitty window."""
    # Bracketed paste keeps newlines inside the prompt instead of submitting early.
    if _in_window(pids, "send-text", "--bracketed-paste=enable", "--", text) is None:
        return False
    time.sleep(0.2)
    return _in_window(pids, "send-key", "enter") is not None


def send_steps(pids, steps):
    """Press keys / type text in order: [{"key": "2"}, {"text": "mango"}, {"key": "enter"}]."""
    for i, step in enumerate(steps):
        if i:
            time.sleep(STEP_GAP_S)
        key, text = step.get("key"), step.get("text")
        if isinstance(key, str) and (key in KEYS or (len(key) == 1 and key.isdigit())):
            ok = _in_window(pids, "send-text", key) if key.isdigit() else _in_window(pids, "send-key", key)
        elif isinstance(text, str):
            # An answer field is one line; a newline would press Enter mid-answer.
            ok = _in_window(pids, "send-text", "--", " ".join(text.splitlines())[:TEXT_MAX])
        else:
            return False
        if ok is None:
            return False
    return True


# ---------------------------------------------------------------- resume

RESUME_MAX = 60               # newest transcripts offered by the web's /resume sheet
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_resume_cache = {}            # path -> (mtime, size, info)


def _prompt_text(entry):
    """A typed prompt's text, or None for tool results, commands and injected context."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    text = _REMINDER.sub("", content or "").strip()
    return None if not text or text.startswith("<") else text


def _transcript_info(path):
    """Title and prompts of one transcript, as /resume shows it; None if nothing was ever asked."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _resume_cache.get(path)
    if hit and hit[:2] == (st.st_mtime, st.st_size):
        return hit[2]
    titles, first, last, headless = {}, None, None, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                # Cheap substring checks first: transcripts run to megabytes of tool output.
                if first is None and '"type":"user"' in line:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    # `claude -p` runs (SESSION.md writer, maxer): claude's own picker hides them too.
                    if e.get("entrypoint", "cli") != "cli":
                        headless = True
                        break
                    first = _prompt_text(e)
                elif '"type":"custom-title"' in line or '"type":"ai-title"' in line or '"type":"last-prompt"' in line:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    for k in ("customTitle", "aiTitle", "lastPrompt"):
                        if e.get(k):
                            titles[k] = e[k]
    except OSError:
        return None
    last = titles.get("lastPrompt") or first
    info = None
    if (first or last) and not headless:
        info = {"title": _clip(titles.get("customTitle") or titles.get("aiTitle") or first or last, 120),
                "prompt": _clip(last, 200), "mtime": st.st_mtime, "size": st.st_size}
    _resume_cache[path] = (st.st_mtime, st.st_size, info)
    return info


def list_resumable(cwd, exclude=()):
    """Past sessions of this project, newest first, for the web's /resume sheet."""
    files = glob.glob(os.path.join(PROJECTS_DIR, _project_slug(cwd), "*.jsonl"))
    files = sorted(files, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    out = []
    for path in files:
        sid = os.path.basename(path)[:-len(".jsonl")]
        if sid in exclude or not _UUID.match(sid):
            continue
        info = _transcript_info(path)
        if info:
            out.append(dict(info, id=sid))
            if len(out) >= RESUME_MAX:
                break
    return out


# ---------------------------------------------------------------- dialogs

_RULE = re.compile(r"^[─━]{8,}$")
_NAMED_RULE = re.compile(r"^[─━]{3,}\s.*\s[─━]+$")  # the prompt box's top border, with the session name
_OPT = re.compile(r"^(\s*)(❯\s*)?(\d{1,2})\.\s(.*)$")
_BOX = re.compile(r"^\[(.)\]\s*(.*)$")
_FOOTER = re.compile(r"Esc to cancel|Enter to select|to navigate")


def parse_dialog(screen):
    """The choice dialog at the bottom of a session's screen, or None.

    Shape, both for permission prompts and AskUserQuestion:
        ────────────
        <title lines: tool preview, question, tab bar "←  ☐ Color  ✔ Submit  →">
        ❯ 1. Yes                   (❯ = cursor)
             description lines     (indented past the number)
          2. [✔] Dog               (multi-select: the digit toggles)
             Submit                (multi-select: reached with arrows)
        Esc to cancel · …          (hint)
    """
    lines = [ln.rstrip() for ln in (screen or "").split("\n")]
    rules = [i for i, ln in enumerate(lines) if _RULE.match(ln.strip())]
    for start in reversed(rules):
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _NAMED_RULE.match(lines[j].strip()) or _FOOTER.search(lines[j]):
                end = j
                break
        region = lines[start + 1:end]
        if any((m := _OPT.match(ln)) and m.group(3) == "1" for ln in region):
            return _dialog(region, lines[end] if end < len(lines) and _FOOTER.search(lines[end]) else "")
    return None


def _dialog(region, footer):
    title, rows, tabs, cur = [], [], None, None
    for ln in region:
        s = ln.strip()
        if not s or _RULE.match(s):
            cur = None if s else cur
            continue
        m = _OPT.match(ln)
        if m:
            label, checked = m.group(4).strip(), None
            box = _BOX.match(label)
            if box:
                checked, label = box.group(1) != " ", box.group(2).strip()
            cur = {"n": int(m.group(3)), "label": label, "desc": "", "cursor": bool(m.group(2)),
                   "checked": checked, "col": m.start(3),
                   "free": label.lower().startswith("type something")}
            rows.append(cur)
            continue
        bare = s.lstrip("❯ ").strip()
        if rows and bare in ("Submit", "Next"):
            rows.append({"n": None, "label": bare, "desc": "", "cursor": s.startswith("❯"),
                         "checked": None, "col": 0, "free": False})
            cur = None
        elif cur and len(ln) - len(ln.lstrip()) > cur["col"]:
            cur["desc"] = (cur["desc"] + " " + s).strip()
        elif not rows:
            # the question tabs: "←  ☐ Color  ☐ Pets  ✔ Submit  →", or " ☐ Fruit " for one question
            if ("←" in s and "→" in s) or s[:1] in "☐☒✔":
                tabs = s.strip("←→ ").strip()
            else:
                title.append(ln)
    # Keep the title's own indentation (diffs, code) but drop the common margin.
    pad = min((len(t) - len(t.lstrip()) for t in title if t.strip()), default=0)
    for r in rows:
        r.pop("col")
    return {"title": "\n".join(t[pad:] for t in title).strip("\n"), "tabs": tabs, "rows": rows,
            "hint": footer.strip(), "multi": any(r["checked"] is not None for r in rows)}
