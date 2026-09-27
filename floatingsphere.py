#!/usr/bin/env python3
"""Floating sphere: a small always-on-top Claude usage monitor.

  * number in the middle  - running Claude Code instances
  * outer ring            - 7-day usage (colored arc = used %, white tick = time elapsed in the week)
  * water inside          - current 5-hour window usage (height = used %)
  * rim ticks on the water- time elapsed in the 5-hour window (water above them = ahead of pace)
  * water color           - green when every instance is idle, purple while any is busy/waiting

Usage comes from claude-maxer's snapshot (~/.claude/state/usage_snapshot.json),
refreshed every 15 min by its fetch cron; the file is re-read whenever it changes.
Instances and their status come from Claude Code's own session registry
(~/.claude/sessions/<pid>.json, read by claude_sessions.py).

Left-click for the session list, double right-click to quit, hover for the chart card
(move it with SUPER+drag like any float).
"""
import json
import math
import os
import subprocess
import sys
import time

import gi

# The sphere is a 75px cairo drawing: GTK's GPU renderer (GL/Vulkan) costs more per
# frame than it saves here (upload + driver threads), so default to software.
os.environ.setdefault("GSK_RENDERER", "cairo")

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_sessions import collect_sessions  # noqa: E402
from hovercard import HoverCard  # noqa: E402

APP_ID = "dev.floatingsphere"
SNAPSHOT_PATH = os.path.expanduser("~/.claude/state/usage_snapshot.json")
POPUP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sphere_popup.py")

SIZE = 75             # window size in px
MARGIN_RIGHT = 12     # distance from the right screen edge
RING_WIDTH = 5
FPS = 24              # while something eases in or the hover card is open
IDLE_FPS = 8          # at rest only the ripple moves; it doesn't need more
POLL_S = 2.0          # session / snapshot poll interval
STALE_S = 40 * 60     # snapshot older than this is shown dimmed
HOVER_OPEN_MS = 250   # hover this long before the card opens
WEEK_S = 7 * 24 * 3600
FIVE_H_S = 5 * 3600

GREEN = (0.20, 0.83, 0.60)
PURPLE = (0.66, 0.33, 0.97)
RING_OK = (0.98, 0.75, 0.25)
RING_HOT = (0.97, 0.36, 0.36)


# ---------- data ----------

def read_snapshot():
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


class State:
    def __init__(self):
        self.usage = None
        self.snap_mtime = None
        self.sessions = []
        self.instances = 0
        self.working = 0

    def poll(self):
        try:
            mtime = os.stat(SNAPSHOT_PATH).st_mtime
        except OSError:
            mtime = None
        if mtime != self.snap_mtime:
            self.snap_mtime = mtime
            self.usage = read_snapshot()

        self.sessions = collect_sessions()
        self.instances = len(self.sessions)
        self.working = sum(1 for x in self.sessions if x["status"] != "idle")


# ---------- drawing ----------

class Sphere(Gtk.DrawingArea):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.set_content_width(SIZE)
        self.set_content_height(SIZE)
        self.set_draw_func(self.draw)
        self.level = 0.0          # animated water level (0..1)
        self.color = list(GREEN)  # animated water color
        self.t0 = time.monotonic()

    def targets(self):
        u = self.state.usage or {}
        five = u.get("five_pct")
        if u.get("five_reset") and u["five_reset"] < time.time():
            five = 0  # the window has reset since the snapshot
        level = max(0.0, min(1.0, (five or 0) / 100))
        color = PURPLE if self.state.working else GREEN
        return level, color

    def step(self):
        """Ease toward the targets; True while still visibly moving."""
        level, color = self.targets()
        self.level += (level - self.level) * 0.08
        self.color = [c + (t - c) * 0.1 for c, t in zip(self.color, color)]
        return abs(level - self.level) > 0.002 or any(abs(t - c) > 0.004 for c, t in zip(self.color, color))

    def draw(self, _area, cr, w, h):
        u = self.state.usage or {}
        now = time.time()
        stale = not u or now - (u.get("cached_at") or 0) > STALE_S
        t = time.monotonic() - self.t0
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - RING_WIDTH / 2 - 1
        r = R - RING_WIDTH / 2 - 3

        # 7d ring: track, used arc, week-elapsed tick
        cr.set_line_width(RING_WIDTH)
        cr.set_source_rgba(1, 1, 1, 0.13)
        cr.arc(cx, cy, R, 0, 2 * math.pi)
        cr.stroke()
        seven = u.get("seven_pct")
        if seven is not None:
            frac = max(0.0, min(1.0, seven / 100))
            rc = RING_HOT if seven >= 90 else RING_OK
            cr.set_source_rgba(*rc, 0.45 if stale else 1.0)
            cr.set_line_cap(1)  # ROUND
            cr.arc(cx, cy, R, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac)
            cr.stroke()
        if u.get("seven_reset"):
            elapsed = 1 - (u["seven_reset"] - now) / WEEK_S
            if 0 <= elapsed <= 1:
                a = -math.pi / 2 + 2 * math.pi * elapsed
                cr.set_line_width(1.5)
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.move_to(cx + (R - RING_WIDTH / 2 - 1) * math.cos(a), cy + (R - RING_WIDTH / 2 - 1) * math.sin(a))
                cr.line_to(cx + (R + RING_WIDTH / 2 + 1) * math.cos(a), cy + (R + RING_WIDTH / 2 + 1) * math.sin(a))
                cr.stroke()

        # glass ball
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(0.08, 0.09, 0.13, 0.82)
        cr.fill_preserve()
        cr.save()
        cr.clip()

        # water: two phase-shifted waves
        top = cy + r - 2 * r * self.level
        amp = 1.5 if 0.01 < self.level < 0.99 else 0.0
        for layer, (speed, alpha, phase) in enumerate(((1.6, 0.45, 0.0), (1.1, 0.9, 2.0))):
            cr.move_to(cx - r, cy + r)
            x = cx - r
            while x <= cx + r + 1:
                y = top + amp * math.sin((x - cx) / r * 2.2 * math.pi + t * speed + phase) + layer * 1
                cr.line_to(x, y)
                x += 1.5
            cr.line_to(cx + r, cy + r)
            cr.close_path()
            cr.set_source_rgba(*self.color, alpha * (0.55 if stale else 1.0))
            cr.fill()

        # 5h pace: short ticks on both rims at the share of the window already elapsed
        if u.get("five_reset") and u["five_reset"] > now:
            elapsed = 1 - (u["five_reset"] - now) / FIVE_H_S
            if 0 <= elapsed <= 1:
                y = cy + r - 2 * r * elapsed
                half = math.sqrt(max(0.0, r * r - (y - cy) ** 2))
                cr.set_line_width(1.5)
                cr.set_source_rgba(1, 1, 1, 0.85)
                for x0, x1 in ((cx - half, cx - half + 5), (cx + half, cx + half - 5)):
                    cr.move_to(x0, y)
                    cr.line_to(x1, y)
                cr.stroke()
        cr.restore()
        cr.set_line_width(1)
        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()

        # instance count
        text = str(self.state.instances)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(r * 0.95)
        ext = cr.text_extents(text)
        tx, ty = cx - ext.width / 2 - ext.x_bearing, cy - ext.height / 2 - ext.y_bearing
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.move_to(tx + 1, ty + 1)
        cr.show_text(text)
        cr.set_source_rgba(1, 1, 1, 0.96)
        cr.move_to(tx, ty)
        cr.show_text(text)


# ---------- window ----------

def hyprland_rules():
    """Float, pin and park the window at the right edge (runtime rules, nothing persisted)."""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return
    try:
        mons = json.loads(subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True).stdout)
        m = next((m for m in mons if m.get("focused")), mons[0])
        scale = m.get("scale", 1) or 1
        mw, mh = m["width"] / scale, m["height"] / scale
        x, y = int(mw - SIZE - MARGIN_RIGHT), int((mh - SIZE) / 2)  # monitor-relative
    except Exception:
        x, y = None, None
    sel = f"class:^({APP_ID})$"
    rules = ["float", "pin", f"size {SIZE} {SIZE}", "noborder", "noshadow", "noblur",
             "noinitialfocus", "nofollowmouse", "rounding 0", "noanim", "nodim", "opaque"]
    if x is not None:
        rules.append(f"move {x} {y}")
    batch = ";".join(f"keyword windowrulev2 {r}, {sel}" for r in rules)
    subprocess.run(["hyprctl", "--batch", batch], capture_output=True)


CSS = b"""
window, window.background { background: transparent; box-shadow: none; }
popover.fs-card > contents {
    background: rgba(21, 22, 30, 0.96);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    padding: 14px 16px 12px 16px;
    box-shadow: none;
}
"""


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.state = State()

    def do_activate(self):
        if self.get_windows():
            return
        hyprland_rules()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Gtk.ApplicationWindow(application=self, title="floatingsphere")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_default_size(SIZE, SIZE)
        self.sphere = Sphere(self.state)
        self.sphere.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        win.set_child(self.sphere)
        # The sphere never needs the keyboard. `nofollowmouse` keeps hovering from
        # focusing it, but a click still does, and with Hyprland's
        # float_switch_override_focus = 0 focus then stays stuck on it (the pointer
        # moving back onto a tiled window doesn't switch focus). `nofocus` isn't an
        # option: it also stops pointer events, so hover and clicks die.
        # Instead hand focus straight back to the previous window.
        win.connect("notify::is-active", self.on_active)

        self.card = HoverCard(self.state)
        self.popover = Gtk.Popover(child=self.card, autohide=False, has_arrow=False,
                                   position=Gtk.PositionType.LEFT)
        self.popover.add_css_class("fs-card")
        self.popover.set_parent(self.sphere)
        self.hover_timer = None
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self.on_enter)
        motion.connect("leave", self.on_leave)
        self.sphere.add_controller(motion)

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self.on_click)
        self.sphere.add_controller(click)

        self.state.poll()
        self.sphere.level, self.sphere.color = self.sphere.targets()[0], list(self.sphere.targets()[1])
        GLib.timeout_add(int(POLL_S * 1000), self.on_poll)
        self.fps = None
        self.schedule_frames(FPS)
        win.present()

    def on_click(self, gesture, n_press, _x, _y):
        btn = gesture.get_current_button()
        if btn == 3 and n_press == 2:  # double, so a stray right-click can't close it
            self.quit()
        elif btn == 1:
            self.hide_card()
            subprocess.Popen([sys.executable, POPUP_SCRIPT], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def on_active(self, win, _pspec):
        if not (win.is_active() and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")):
            return
        # Only a click should be undone. Switching to an empty workspace also focuses
        # the pinned sphere (it's the only window there); handing focus "back" then
        # makes Hyprland pick the sphere again and warp the cursor onto it.
        if not self.cursor_on_sphere():
            return
        # A focus dispatch warps the cursor to the new window's center, which would
        # yank the pointer off the sphere; suspend warps around it.
        try:
            warps = json.loads(subprocess.run(["hyprctl", "-j", "getoption", "cursor:no_warps"],
                                              capture_output=True, text=True).stdout)["int"]
        except (ValueError, KeyError):
            warps = 0
        subprocess.Popen(["hyprctl", "--batch", "keyword cursor:no_warps 1; dispatch focuscurrentorlast; "
                          f"keyword cursor:no_warps {warps}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def cursor_on_sphere(self):
        try:
            cur = json.loads(subprocess.run(["hyprctl", "-j", "cursorpos"],
                                            capture_output=True, text=True).stdout)
            me = next(c for c in json.loads(subprocess.run(["hyprctl", "-j", "clients"],
                                                           capture_output=True, text=True).stdout)
                      if c.get("class") == APP_ID)
        except (ValueError, StopIteration):
            return True
        (x, y), (w, h) = me["at"], me["size"]
        return x <= cur["x"] < x + w and y <= cur["y"] < y + h

    def on_enter(self, *_):
        if self.hover_timer is None:
            self.hover_timer = GLib.timeout_add(HOVER_OPEN_MS, self.show_card)

    def on_leave(self, *_):
        self.hide_card()

    def show_card(self):
        self.hover_timer = None
        self.card.restart()
        self.popover.popup()
        return False

    def hide_card(self):
        if self.hover_timer is not None:
            GLib.source_remove(self.hover_timer)
            self.hover_timer = None
        self.popover.popdown()

    def on_poll(self):
        self.state.poll()
        return True

    def schedule_frames(self, fps):
        self.fps = fps
        GLib.timeout_add(1000 // fps, self.on_frame)

    def on_frame(self):
        moving = self.sphere.step()
        self.sphere.queue_draw()
        card_open = self.popover.get_visible()
        if card_open:
            self.card.queue_draw()
        fps = FPS if moving or card_open else IDLE_FPS
        if fps != self.fps:
            self.schedule_frames(fps)
            return False
        return True


if __name__ == "__main__":
    sys.exit(App().run(sys.argv[:1]))
