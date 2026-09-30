"""
quota: today's share of the 7-day limit, as claude-maxer budgets it.

Once a day maxer splits what's left of the week (up to its `weekly_target`) evenly over
the days left until the weekly reset, and today may spend one share: 7d may climb from
where it stood at the start of the day (`start`) by `budget` pp, up to `ceiling` %.
It saves that to ~/.claude/state/claude-maxer-day.json. When that file isn't today's
(maxer switched off, or it hasn't run yet today) the same split is computed here.
"""
import json
import os
import re
from datetime import datetime, timedelta

DAY_PATH = os.path.expanduser("~/.claude/state/claude-maxer-day.json")
MAXER_SKILL = "/data/apps/lidaning-skills/skills/claude-maxer/SKILL.md"
WEEKLY_TARGET = 95  # maxer's default, when its settings can't be read

_own = {}  # (date, reset) -> the split computed here, so its start holds all day


def weekly_target():
    try:
        with open(MAXER_SKILL) as f:
            m = re.search(r"^weekly_target:\s*(\d+(?:\.\d+)?)", f.read(), re.M)
        return float(m.group(1)) if m else WEEKLY_TARGET
    except OSError:
        return WEEKLY_TARGET


def daily_quota(u, now):
    """{"start", "budget", "ceiling", "day_start", "day_end"} for today, or None without 7d data."""
    if not u or u.get("seven_pct") is None:
        return None
    day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    midnight, day_end = day.timestamp(), (day + timedelta(days=1)).timestamp()
    seven, reset = u["seven_pct"], u.get("seven_reset")
    q = None
    try:
        with open(DAY_PATH) as f:
            b = json.load(f)
        # maxer's reset time can differ from the snapshot's by a fraction of a second.
        if b.get("date") == day.strftime("%Y-%m-%d") and abs((b.get("seven_reset") or 0) - (reset or 0)) < 60:
            q = {"start": b["seven_start"], "budget": b["budget"], "ceiling": b["ceiling"]}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    key = (day.strftime("%Y-%m-%d"), round(reset or 0, -2))
    if q is None:
        q = _own.get(key)
    if q is None:
        tgt = weekly_target()
        days_left = ((reset or now + 86400) - midnight) / 86400
        budget = max(0.0, tgt - seven) / max(days_left, 1.0)
        q = {"start": seven, "budget": round(budget, 1), "ceiling": round(min(tgt, seven + budget), 1)}
        _own.clear()
        _own[key] = q
    return dict(q, day_start=midnight, day_end=day_end)
