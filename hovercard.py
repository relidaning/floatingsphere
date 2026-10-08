"""Hover card for the floating sphere: the numbers behind the sphere, as small charts.

  * 5h / 7d meters  - used % (solid) and a white tick for time elapsed in the window
  * quota minibar   - after the 5h line: today's share of the 7d limit (quota.py), how much
                      of today's budget 7d has used since midnight, tick = time of day
  * sessions bar    - one segment per status, with a labeled legend

Everything animates: bars sweep in from zero when the card opens (rows staggered),
then glide to new values as the data changes; the card fades in, and busy/waiting
legend dots pulse. `animating` says whether any of that is still moving, so the app can
stop redrawing a card that has settled (a pointer left resting on the sphere keeps it open).
"""
import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

W, H = 272, 214
PAD = 2

INK = (0.93, 0.94, 0.96)        # primary text
INK_2 = (0.70, 0.72, 0.78)      # secondary text
INK_3 = (0.50, 0.52, 0.58)      # muted text
TRACK = (1, 1, 1, 0.08)
FILL = (0.98, 0.75, 0.25)       # same amber as the sphere's 7d ring
CRITICAL = (0.90, 0.40, 0.40)   # stale snapshot, today's quota overspent

# session status -> (color, label); validated as a categorical set on the dark card
# surface (dataviz validate_palette.js: all checks pass), in stacking order
STATUS = {
    "busy":    ((0.659, 0.329, 0.969), "busy"),        # #a854f7 - the sphere's purple
    "bg":      ((0.224, 0.529, 0.898), "background"),  # #3987e5
    "agent":   ((0.224, 0.529, 0.898), "background"),
    "waiting": ((0.851, 0.349, 0.149), "waiting"),     # #d95926
    "idle":    ((0.122, 0.659, 0.455), "idle"),        # #1fa874
}
ORDER = ("busy", "bg", "waiting", "idle")

FIVE_H_S = 5 * 3600
WEEK_S = 7 * 24 * 3600
STAGGER_S = 0.09
EASE = 0.16      # per-frame approach factor for animated values
FADE_S = 0.18
SETTLE_S = 1.0   # every stagger delay and fade-in is over by then


def _fmt_left(seconds):
    m = max(0, int(seconds // 60))
    if m >= 24 * 60:
        return f"{m // 1440}d {m % 1440 // 60}h"
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def window_stats(pct, reset, length, now):
    """used %, elapsed fraction, reset-passed flag."""
    if pct is None or not reset:
        return None
    if reset <= now:
        return {"used": 0.0, "elapsed": None, "reset": reset, "passed": True}
    elapsed = 1 - (reset - now) / length
    return {"used": float(pct), "elapsed": max(0.0, min(1.0, elapsed)), "reset": reset, "passed": False}


def quota_stats(q, seven, now):
    """Today's quota (quota.py): % of today's budget used by 7d, elapsed share of the day."""
    if q is None or seven is None:
        return None
    spent = max(0.0, seven - q["start"])
    elapsed = (now - q["day_start"]) / (q["day_end"] - q["day_start"])
    return {"used": 100 * spent / q["budget"] if q["budget"] > 0 else 100.0,
            "elapsed": max(0.0, min(1.0, elapsed))}


class HoverCard(Gtk.DrawingArea):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.set_content_width(W)
        self.set_content_height(H)
        self.set_draw_func(self.draw)
        self.opened_at = 0.0
        self.shown = {}  # animated values, keyed by name
        self.animating = True  # set by draw(): something will look different next frame

    def restart(self):
        """Called when the card opens: sweep every value in from zero."""
        self.opened_at = time.monotonic()
        self.shown = {}
        self.animating = True

    def _anim(self, key, target, delay=0.0):
        """Advance one animated value toward target (after a stagger delay) and return it."""
        cur = self.shown.get(key, 0.0)
        if time.monotonic() - self.opened_at >= delay:
            cur += (target - cur) * EASE
            if abs(target - cur) < 0.05:
                cur = target
        self.shown[key] = cur
        if cur != target:
            self._moving = True
        return cur

    def _since(self, delay):
        return max(0.0, time.monotonic() - self.opened_at - delay)

    # ---- text helpers ----

    def _text(self, cr, x, y, text, size, color, bold=False, align="left", alpha=1.0):
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(f"Sans {'Bold ' if bold else ''}{size}")
        fd.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        if align == "right":
            x -= tw
        cr.set_source_rgba(*color, alpha)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)
        return tw, th

    @staticmethod
    def _rounded(cr, x, y, w, h, r):
        r = min(r, h / 2, w / 2)
        if w <= 0:
            return
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    # ---- rows ----

    def _minibar(self, cr, x, y, stats, delay):
        """Today's quota, small: fill = % of today's budget used, tick = time of day."""
        bw, bh = min(80, W - PAD - x), 5  # right-aligned; whatever the line leaves, up to 80
        if bw < 20:
            return
        x = W - PAD - bw
        fade = min(1.0, self._since(delay) / 0.25)
        used = self._anim("quota", stats["used"], delay)
        cr.set_source_rgba(*TRACK)
        self._rounded(cr, x, y, bw, bh, 2.5)
        cr.fill()
        if used > 0:
            cr.set_source_rgba(*(CRITICAL if used > 100 else FILL), fade)
            self._rounded(cr, x, y, max(bh, bw * min(used, 100.0) / 100), bh, 2.5)
            cr.fill()
        ex = x + bw * stats["elapsed"]
        cr.set_source_rgba(1, 1, 1, 0.9 * fade)
        cr.set_line_width(1.5)
        cr.move_to(ex, y - 2)
        cr.line_to(ex, y + bh + 2)
        cr.stroke()

    def _meter(self, cr, y, key, title, stats, delay, now, mini=None):
        x0, bw, bh = PAD, W - 2 * PAD, 8
        fade = min(1.0, self._since(delay) / 0.25)
        if stats is None:
            self._text(cr, x0, y, title, 11, INK_2, alpha=fade)
            self._text(cr, x0, y + 20, "no data", 10, INK_3, alpha=fade)
            return
        used = self._anim(key, stats["used"], delay)
        self._text(cr, x0, y, title, 11, INK_2, alpha=fade)
        self._text(cr, W - PAD, y - 2, f"{round(used)}%", 14, INK, bold=True, align="right", alpha=fade)

        by = y + 20
        cr.set_source_rgba(*TRACK)
        self._rounded(cr, x0, by, bw, bh, 4)
        cr.fill()

        if used > 0:
            cr.set_source_rgba(*FILL, 1)
            self._rounded(cr, x0, by, max(bh, bw * used / 100), bh, 4)
            cr.fill()

        if stats["elapsed"] is not None:
            ex = x0 + bw * self._anim(key + "_pace", stats["elapsed"], delay + 0.15)
            cr.set_source_rgba(1, 1, 1, 0.95 * min(1.0, self._since(delay + 0.15) / 0.3))
            cr.set_line_width(2)
            cr.move_to(ex, by - 3)
            cr.line_to(ex, by + bh + 3)
            cr.stroke()

        if stats["passed"]:
            sub = "window has reset · waiting for a fresh snapshot"
        else:
            reset_s = time.strftime("%a %H:%M" if stats["reset"] - now > 20 * 3600 else "%H:%M",
                                    time.localtime(stats["reset"]))
            sub = f"resets {reset_s} · {_fmt_left(stats['reset'] - now)} left"
        tw, th = self._text(cr, x0, by + bh + 6, sub, 10, INK_3, alpha=fade)
        if mini is not None:
            self._minibar(cr, x0 + tw + 8, by + bh + 6 + th / 2 - 2.5, mini, delay + 0.25)

    def _sessions(self, cr, y, delay):
        x0, bw, bh = PAD, W - 2 * PAD, 8
        sessions = self.state.sessions
        counts = {}
        for s in sessions:
            k = "bg" if s["status"] == "agent" else s["status"]
            counts[k] = counts.get(k, 0) + 1
        total = len(sessions)
        fade = min(1.0, self._since(delay) / 0.25)
        self._text(cr, x0, y, "Sessions", 11, INK_2, alpha=fade)
        self._text(cr, W - PAD, y - 2, str(total), 14, INK, bold=True, align="right", alpha=fade)

        by = y + 20
        if not total:
            cr.set_source_rgba(*TRACK)
            self._rounded(cr, x0, by, bw, bh, 4)
            cr.fill()
            self._text(cr, x0, by + bh + 6, "no Claude sessions running", 10, INK_3, alpha=fade)
            return

        # segment widths animate; a 2px surface gap separates neighbours
        widths = {k: self._anim("seg_" + k, bw * counts.get(k, 0) / total, delay) for k in ORDER}
        x = x0
        drawn = [k for k in ORDER if widths[k] > 0.5]
        for i, k in enumerate(drawn):
            w = widths[k] - (2 if i < len(drawn) - 1 else 0)
            cr.set_source_rgba(*STATUS[k][0], 1)
            self._rounded(cr, x, by, max(0.0, w), bh, 3)
            cr.fill()
            x += widths[k]

        # legend: dot + "N label", busy/waiting dots pulse
        lx, ly = x0, by + bh + 8
        t = time.monotonic()
        for k in ORDER:
            n = counts.get(k, 0)
            if not n:
                continue
            color, label = STATUS[k]
            pulse = 0.5 + 0.5 * math.sin(t * 4) if k in ("busy", "waiting") else 1.0
            if k in ("busy", "waiting"):
                self._moving = True
            cr.set_source_rgba(*color, fade * (0.45 + 0.55 * pulse))
            cr.arc(lx + 4, ly + 7, 3.5 + (0.8 * pulse if k in ("busy", "waiting") else 0), 0, 2 * math.pi)
            cr.fill()
            tw, _ = self._text(cr, lx + 11, ly, f"{n} {label}", 10, INK_2, alpha=fade)
            lx += 11 + tw + 12

    def draw(self, _area, cr, w, h):
        u = self.state.usage or {}
        now = time.time()
        self._moving = self._since(0) < SETTLE_S
        self.set_opacity(min(1.0, self._since(0) / FADE_S))

        self._text(cr, PAD, 0, "Claude usage", 13, INK, bold=True)
        if u:
            age = now - (u.get("cached_at") or 0)
            age_s = f"{int(age // 60)}m ago" if age >= 60 else "just now"
            self._text(cr, W - PAD, 2, f"snapshot {age_s}", 10, CRITICAL if age > 40 * 60 else INK_3,
                       align="right")

        five = window_stats(u.get("five_pct"), u.get("five_reset"), FIVE_H_S, now)
        seven = window_stats(u.get("seven_pct"), u.get("seven_reset"), WEEK_S, now)
        quota = quota_stats(self.state.quota, u.get("seven_pct"), now)
        self._meter(cr, 30, "five", "5-hour window", five, 0.0, now, mini=quota)
        self._meter(cr, 92, "seven", "7-day", seven, STAGGER_S, now)
        self._sessions(cr, 154, 2 * STAGGER_S)
        self.animating = self._moving
