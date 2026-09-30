"""
usage_history: how much of the 7-day limit each week used, for the web app's weekly chart.

sphere_web runs Recorder, which reads claude-maxer's usage snapshot every POLL_S and keeps
one period per 7-day window in ~/.config/floatingsphere/usage-weeks.json:
  {"start", "end", "reset", "peak", "last", "early", "days": {"YYYY-MM-DD": 7d % at the day's end}}
`days` holds the running 7d % per local day, so the chart can split a week's bar into
what each day added.

A new period starts when the snapshot's `resets_at` moves on (the normal weekly reset),
or when 7d drops by more than DROP_PP under the same `resets_at`: Anthropic sometimes
resets usage early without moving the reset time (2026-09-28 went 99% -> 0% with the
reset still on 10-01), and that has to close the week, not dent its peak.

On the first run (no file yet) the current window is backfilled from claude-maxer's
log, whose runs note the 7d % they saw (`seven`, or "7d usage at N%" in `detail`).
"""
import json
import os
import re
import threading
import time
from datetime import datetime

STATE_DIR = os.path.expanduser("~/.config/floatingsphere")
HISTORY_PATH = os.path.join(STATE_DIR, "usage-weeks.json")
MAXER_LOG = os.path.expanduser("~/.claude/state/claude-maxer.log.jsonl")
WEEK_S = 7 * 24 * 3600
POLL_S = 60        # the snapshot itself only changes every ~15 min
DROP_PP = 5        # a fall this big under the same reset time is an early reset
RESET_SLACK_S = 3600  # resets_at jitters by fractions of a second between fetches
KEEP = 104         # two years of weeks

_lock = threading.Lock()


def _day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def load():
    try:
        with open(HISTORY_PATH) as f:
            h = json.load(f)
        return h if isinstance(h.get("periods"), list) else {"periods": []}
    except (OSError, ValueError, AttributeError):
        return {"periods": []}


def _save(h):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(h, f, indent=1)
    os.replace(tmp, HISTORY_PATH)


def _update(h, pct, reset, at):
    """Fold one reading (7d %, its resets_at, when it was fetched) into the history."""
    periods = h["periods"]
    cur = periods[-1] if periods else None
    moved = cur is None or abs(reset - cur["reset"]) > RESET_SLACK_S
    dropped = cur is not None and not moved and pct < cur["last"] - DROP_PP
    if moved or dropped:
        if cur is not None:
            cur["end"] = min(cur["reset"], at)
        start = reset - WEEK_S
        if cur is not None:
            start = max(start, cur["end"])
        cur = {"start": start, "end": reset, "reset": reset, "peak": 0, "last": 0,
               "early": False, "days": {}}
        if dropped:
            periods[-1]["early"] = True
        periods.append(cur)
        del periods[:-KEEP]
    cur["last"] = pct
    cur["peak"] = max(cur["peak"], pct)
    day = _day(at)
    cur["days"][day] = max(cur["days"].get(day, 0), pct)
    h["at"] = at


def _maxer_samples():
    out = []
    try:
        with open(MAXER_LOG) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                pct = d.get("seven")
                if pct is None:
                    m = re.search(r"7d usage at (\d+)%", d.get("detail") or "")
                    pct = int(m.group(1)) if m else None
                if isinstance(pct, (int, float)) and d.get("ts"):
                    out.append((d["ts"], pct))
    except OSError:
        pass
    return sorted(out)


def _backfill(h, reset):
    """Seed the current 7-day window from maxer's log; older samples have no reset time."""
    for ts, pct in _maxer_samples():
        if reset - WEEK_S <= ts < reset:
            _update(h, pct, reset, ts)


def record(u):
    """Fold the current usage snapshot (sphere_web.read_usage()) into the history."""
    if not u or u.get("seven_pct") is None or not u.get("seven_reset"):
        return
    pct, reset, at = u["seven_pct"], u["seven_reset"], u.get("cached_at") or time.time()
    if reset <= at:
        return  # the window it describes is already over
    with _lock:
        fresh = not os.path.exists(HISTORY_PATH)
        h = load()
        if fresh:
            _backfill(h, reset)
        if not fresh and at <= h.get("at", 0):
            return  # the same snapshot as last time
        _update(h, pct, reset, at)
        _save(h)


def weeks_json(target=None):
    """What /api/weeks serves: the periods, oldest first, plus maxer's weekly target."""
    with _lock:
        h = load()
    return {"periods": h["periods"], "target": target, "now": time.time()}


class Recorder(threading.Thread):
    def __init__(self, read_usage):
        super().__init__(daemon=True, name="usage-history")
        self.read_usage = read_usage

    def run(self):
        while True:
            try:
                record(self.read_usage())
            except Exception as e:  # noqa: BLE001 - keep recording through a bad snapshot
                print("usage_history:", e, flush=True)
            time.sleep(POLL_S)
