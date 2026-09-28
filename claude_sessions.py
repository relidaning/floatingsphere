"""
Shared data layer for the Claude Code session monitors.

Both ClaudeWaybar.py (the always-visible bar icon) and ClaudeWaybarPopup.py
(the clickable session list it opens) need the exact same picture of "what
Claude sessions are running right now" and the exact same way of jumping a
click to the right terminal — this module is that shared picture, factored
out so neither frontend re-implements or drifts from the other.

Everything here is pure file-reads and the occasional `hyprctl` shell-out;
nothing here talks to a GTK toolkit, so it is safe to import from a headless
script that waybar runs continuously.

  ~/.claude/sessions/<pid>.json   session registry Claude Code maintains
                                  itself: pid, cwd, name, and a live
                                  status of busy / waiting / idle.
  ~/.claude/projects/<slug>/<sessionId>.jsonl
                                  the transcript, tailed for the current
                                  task (`last-prompt`) and the tool the
                                  session is running right now.
  ~/.claude/state/usage_snapshot.json
                                  5h / 7d rate-limit percentages and reset
                                  times, cached by claude/scripts/statusline.py.
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
USAGE_SNAPSHOT = os.path.expanduser("~/.claude/state/usage_snapshot.json")

TAIL_BYTES = 256 * 1024      # transcript bytes scanned for prompt/tool
HEAD_BYTES = 64 * 1024       # transcript bytes scanned for the opening prompt
TASK_MAXLEN = 96
STALE_AFTER_S = 6 * 3600     # ignore session files this old even if pid lives

# Claude Code re-fetches rate limits roughly every 30s, so a snapshot a couple
# of refreshes old is still normal; past that it is worth saying out loud that
# the percentages are not live.
USAGE_REFRESH_S = 30
USAGE_STALE_AFTER_S = 4 * USAGE_REFRESH_S

# Subagents spawned by a live session are hidden by default — their work
# already shows as the parent's "busy". Unattended runs (cron, detached
# scripts) are always shown: those are the ones that burn quota unwatched.
SHOW_SUBAGENTS = os.environ.get("CLAUDE_MONITOR_SUBAGENTS") == "1"


# ---------------------------------------------------------------- data layer

def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    return os.path.isdir(f"/proc/{pid}")


def _ppid(pid):
    """Parent pid from /proc/<pid>/stat, skipping past the comm field.

    comm is wrapped in parentheses and may itself contain spaces or ')',
    so the fields are split from the *last* ')' rather than by whitespace.
    """
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        return int(data[data.rindex(")") + 2:].split()[1])
    except (OSError, ValueError):
        return None


def _has_ancestor(pid, candidates, max_depth=24):
    """True if any pid in `candidates` is an ancestor of `pid`."""
    seen = 0
    pid = _ppid(pid)
    while pid and pid > 1 and seen < max_depth:
        if pid in candidates:
            return True
        pid = _ppid(pid)
        seen += 1
    return False


def short_model(model):
    """claude-haiku-4-5-20251001 -> haiku-4.5; claude-opus-5 -> opus-5."""
    if not model:
        return None
    name = re.sub(r"^claude-", "", model)
    name = re.sub(r"-\d{8}$", "", name)
    return re.sub(r"(?<=\d)-(?=\d)", ".", name)


def _project_slug(cwd):
    """Claude Code's project-dir naming: non-alphanumerics collapse to '-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _tail_text(path, nbytes=TAIL_BYTES):
    """Last nbytes of a file, trimmed to start on a line boundary."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        data = f.read()
    if size > nbytes:
        _, _, data = data.partition(b"\n")
    return data.decode("utf-8", "replace")


def _first_prompt(path, nbytes=HEAD_BYTES):
    """The opening user message, read from the head of a transcript.

    `last-prompt` is only written once a turn completes, so a headless run
    has none for its whole lifetime — exactly the runs whose task most needs
    showing. The initial `-p` prompt is the first user entry instead.
    """
    try:
        with open(path, "rb") as f:
            data = f.read(nbytes)
    except OSError:
        return None

    for line in data.decode("utf-8", "replace").splitlines():
        if '"type":"user"' not in line.replace(" ", ""):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a truncated final line is expected on a bounded read
        if entry.get("type") != "user":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    return block["text"].strip()
    return None


_transcript_cache = {}  # sessionId -> (mtime, size, parsed)


def read_transcript(cwd, session_id):
    """Current task + in-flight tool for a session, or {} if unreadable."""
    path = os.path.join(PROJECTS_DIR, _project_slug(cwd), f"{session_id}.jsonl")
    try:
        st = os.stat(path)
    except OSError:
        return {}

    key = (st.st_mtime, st.st_size)
    cached = _transcript_cache.get(session_id)
    if cached and cached[0] == key:
        return cached[1]

    try:
        text = _tail_text(path)
    except OSError:
        return {}

    task = None
    tool = None
    model = None
    for line in reversed(text.splitlines()):
        if task and tool and model:
            break
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue

        kind = entry.get("type")
        if kind == "last-prompt" and not task:
            task = (entry.get("lastPrompt") or "").strip()
        elif kind == "assistant":
            message = entry.get("message") or {}
            model = model or message.get("model")
            if not tool:
                for block in message.get("content") or []:
                    if block.get("type") == "tool_use":
                        tool = block.get("name")
                        break

    if not task:
        task = _first_prompt(path)

    parsed = {"task": task, "tool": tool, "model": short_model(model)}
    _transcript_cache[session_id] = (key, parsed)
    return parsed


def collect_sessions():
    """Live Claude sessions, busy first, then most-recently-active."""
    now = time.time()
    try:
        filenames = os.listdir(SESSIONS_DIR)
    except OSError:
        return []

    live = []
    for filename in filenames:
        if not filename.endswith(".json"):
            continue
        info = _read_json(os.path.join(SESSIONS_DIR, filename))
        if not info:
            continue
        # The background daemon pre-warms "spare" processes to claim the next
        # background job; they register a session file but aren't a session yet.
        if info.get("spare"):
            continue
        pid = info.get("pid")
        # Session files outlive a crashed process; /proc is the ground truth.
        if pid and _pid_alive(pid):
            live.append(info)

    # Terminal sessions are entrypoint "cli"; everything headless (cron jobs,
    # SessionEnd hook runs, subagents) is "sdk-cli". Knowing which interactive
    # pids exist is what lets the sdk-cli runs be told apart below.
    interactive_pids = {
        i["pid"] for i in live
        if i.get("entrypoint") == "cli" and i.get("kind") == "interactive"
    }

    sessions = []
    for info in live:
        pid = info["pid"]
        cwd = info.get("cwd") or ""
        headless = info.get("entrypoint") != "cli"

        if headless:
            # A subagent has its spawning session somewhere up the process
            # tree; an unattended run (cron, a detached script) traces back to
            # cron/init instead. Only the latter is worth a card by default.
            is_subagent = _has_ancestor(pid, interactive_pids)
            if is_subagent and not SHOW_SUBAGENTS:
                continue
            status = "agent" if is_subagent else "bg"
            # Headless runs carry no `status` field, so age them from launch.
            status_since = (info.get("startedAt") or 0) / 1000
        else:
            status = info.get("status") or "idle"
            # Claude Code adds statuses over time: "shell" means the turn is
            # over but a background shell (e.g. a dev server) is still running.
            # Anything that isn't busy/waiting leaves the session at the prompt.
            if status not in ("busy", "waiting"):
                status = "idle"
            status_since = (info.get("statusUpdatedAt") or 0) / 1000
            updated = (info.get("updatedAt") or 0) / 1000
            if updated and now - updated > STALE_AFTER_S:
                continue

        session_id = info.get("sessionId") or ""
        transcript = read_transcript(cwd, session_id) if cwd and session_id else {}

        sessions.append({
            "pid": pid,
            "session_id": session_id,
            "name": info.get("name") or os.path.basename(cwd) or f"pid {pid}",
            "cwd": cwd,
            "status": status,
            "headless": headless,
            "waiting_for": info.get("waitingFor"),
            "status_since": status_since,
            "task": transcript.get("task"),
            "tool": transcript.get("tool"),
            "model": transcript.get("model"),
        })

    order = {"bg": 0, "busy": 1, "agent": 2, "waiting": 3, "idle": 4}
    rank = lambda s: (order.get(s["status"], 5), -s["status_since"])  # noqa: E731

    # One conversation can have several processes: a session moved to the
    # background keeps its terminal `claude --resume` alongside the daemon's
    # worker, both registered under the same sessionId. Show it once, with the
    # liveliest status, and remember every pid so stopping it stops all of them.
    merged = {}
    for s in sorted(sessions, key=rank):
        key = s["session_id"] or f"pid:{s['pid']}"
        if key in merged:
            merged[key]["pids"].append(s["pid"])
        else:
            merged[key] = dict(s, pids=[s["pid"]])
    return sorted(merged.values(), key=rank)


def _hypr_clients():
    try:
        result = subprocess.run(
            ["hyprctl", "-j", "clients"], capture_output=True, text=True, timeout=2
        )
        return json.loads(result.stdout) if result.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def window_for_pid(pid, clients=None, max_depth=24):
    """Address of the Hyprland window hosting `pid`, or None.

    A terminal's Wayland surface belongs to the *terminal* process, so the pid
    in `hyprctl clients` is kitty's, several levels above the claude process
    (claude → zsh → kitty). Walk the same PPid chain the subagent check uses
    until a pid matches a window. A subagent resolves to its parent session's
    terminal, which is exactly where you'd want to land; an unattended run
    has no terminal at all and returns None.
    """
    clients = _hypr_clients() if clients is None else clients
    by_pid = {c["pid"]: c["address"] for c in clients if c.get("pid") and c.get("address")}
    seen = 0
    while pid and pid > 1 and seen < max_depth:
        if pid in by_pid:
            return by_pid[pid]
        pid = _ppid(pid)
        seen += 1
    return None


def _starttime(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        return int(data[data.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _launcher_shell(pid):
    """The `zsh -ic 'command claude …; exec zsh -i'` that claude_new.sh or the web
    app's + opened kitty with, if `pid` is its claude; None for any other terminal."""
    ppid = _ppid(pid)
    try:
        with open(f"/proc/{ppid}/cmdline", "rb") as f:
            args = f.read().decode(errors="replace").split("\0")
    except (OSError, TypeError):
        return None
    if args[:2] == ["zsh", "-ic"] and len(args) > 2 and "$CLAUDE_NEW_LABEL" in args[2] \
            and args[2].rstrip().endswith("exec zsh -i"):
        return ppid, _starttime(ppid)
    return None


def _close_launcher_shells(pids, shells, wait_s=8.0):
    """Once claude has exited, end the shell its kitty fell back to, so the
    window (and kitty, when it was its only window) closes too."""
    deadline = time.monotonic() + wait_s
    while any(_pid_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(0.3)  # let the `exec zsh -i` happen
    for shell, started in shells:
        try:
            with open(f"/proc/{shell}/cmdline", "rb") as f:
                fresh = f.read().split(b"\0")[:2] == [b"zsh", b"-i"]
        except OSError:
            continue
        # Only the empty shell the launcher exec'd into, and not a reused pid. KILL, not
        # HUP: .zshrc's TRAPHUP runs the terminal-close fallback and doesn't exit.
        if fresh and _starttime(shell) == started:
            try:
                os.kill(shell, signal.SIGKILL)
            except OSError:
                pass


def stop_session(pids):
    """SIGTERM every process of a session (Claude Code exits cleanly on it), then
    close the kitty window it was started in, if one of our launchers opened it.
    A terminal the user started claude in themselves is left open."""
    shells = {sh for sh in map(_launcher_shell, pids) if sh and sh[1] is not None}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if shells:
        # not a daemon: a caller that exits right away (the popup) still finishes this
        threading.Thread(target=_close_launcher_shells, args=(list(pids), shells),
                         daemon=False).start()


def focus_session(pids):
    """Jump to the terminal running any of `pids`. False if none has a window."""
    if isinstance(pids, int):
        pids = [pids]
    clients = _hypr_clients()
    address = next((a for a in (window_for_pid(p, clients) for p in pids) if a), None)
    if not address:
        return False
    try:
        # focuswindow follows the window to its workspace, so this also
        # switches workspace/monitor when the terminal is elsewhere.
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
            capture_output=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def read_usage():
    """(rate_limits, cached_at) from the snapshot statusline.py writes."""
    snapshot = _read_json(USAGE_SNAPSHOT) or {}
    return snapshot.get("rate_limits") or {}, snapshot.get("cached_at") or 0


# ------------------------------------------------------------------ helpers

def fmt_elapsed(since):
    if not since:
        return ""
    seconds = int(time.time() - since)
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def fmt_reset(ts, now):
    """Reset time as a clock, matching the statusLine's ↺ format.

    The clock is always shown, date or no date: a 5h window that rolls over
    after midnight is only hours away, so a bare "08-11" answers the wrong
    question entirely.
    """
    dt = datetime.fromtimestamp(ts)
    if dt.date() == datetime.fromtimestamp(now).date():
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


def fmt_age(seconds):
    """Coarse age — one unit is enough to judge 'is this number still true?'."""
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def usage_text(rate_limits, cached_at, now=None):
    """Header usage string plus (warn, stale) flags.

    Kept module-level and now-injectable so the window-rollover and staleness
    branches can be exercised headlessly instead of by waiting five hours.
    """
    now = now or time.time()
    parts = []
    worst = 0
    stale = False

    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = rate_limits.get(key) or {}
        resets_at = window.get("resets_at")
        if not resets_at:
            continue
        if resets_at <= now:
            # The window rolled over after the snapshot was taken, so its
            # percentage describes a window that no longer exists. Nothing
            # will correct it until some session renders its statusLine again.
            parts.append(f"{label} reset")
            stale = True
            continue
        pct = round(window.get("used_percentage", 0))
        worst = max(worst, pct)
        parts.append(f"{label} {pct}% ↺{fmt_reset(resets_at, now)}")

    if not parts:
        return "", False, False

    age = max(0, now - cached_at) if cached_at else 0
    if age > USAGE_STALE_AFTER_S:
        parts.append(f"({fmt_age(age)} ago)")
        stale = True

    return "  ".join(parts), worst >= 85, stale


def shorten_path(path):
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def truncate(text, limit=TASK_MAXLEN):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
