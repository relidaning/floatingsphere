#!/usr/bin/env python3
"""
sphere_popup — the clickable "instance list" for the floating sphere
(copied from ~/.config/hypr/UserScripts/ClaudeWaybarPopup.py),
and the only GTK UI for Claude Code sessions in this repo (the persistent
workspace-10 panel, ClaudeMonitor.py, was retired in favor of this — a real
workspace switch just to check status was the thing the waybar module
existed to avoid in the first place). A transient popup, positioned at the
cursor (so it appears right under the bar icon that was clicked) with
clickable session cards, that closes itself the moment you're done with it
(pick a session, hit Escape, or click away).

Single-instance via pidfile (a second click while one is open replaces it,
never stacks) and pre-map `windowrulev2 move` positioning — both patterns
copied from WordPopup.py, which solves the exact same "small popup at the
cursor" problem for a different feature.
"""
import json
import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_sessions import (  # noqa: E402
    collect_sessions,
    focus_session,
    stop_session,
    fmt_elapsed,
    read_usage,
    shorten_path,
    truncate,
    usage_text,
)

APP_ID = "dev.floatingsphere.popup"
PIDFILE = "/tmp/floatingsphere-popup.pid"
POPUP_W = 400
POPUP_MAX_H = 480

NEW_SESSION_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_new.sh")

# status -> (label, css class). Colors live in CSS and match hovercard.STATUS,
# so the list, the hover card's sessions bar and the sphere's water agree.
STATUS_STYLE = {
    "busy":    ("busy",       "st-busy"),
    "waiting": ("waiting",    "st-waiting"),
    "idle":    ("idle",       "st-idle"),
    "bg":      ("background", "st-bg"),
    "agent":   ("subagent",   "st-bg"),
}
DISARM_MS = 3000  # an armed stop button reverts after this long

# Palette shared with the sphere and hovercard.py: the glass surface, INK text
# tiers, amber for usage (the 7d ring), the categorical status colors.
CSS = b"""
window.fs-popup {
    background: rgba(21, 22, 30, 0.96);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
}
.cwp-root { padding: 12px 12px 8px 12px; }
.cwp-root, .cwp-root label { font-family: "JetBrainsMono Nerd Font", monospace; color: #edf0f5; }

.cwp-title { font-size: 13px; font-weight: bold; }
.cwp-root label.cwp-usage { font-size: 11px; color: #fabf40; }
.cwp-root label.cwp-usage-warn { font-size: 11px; color: #f75c5c; font-weight: bold; }

.cwp-sep { background: rgba(255, 255, 255, 0.08); min-height: 1px; margin: 8px 0 6px 0; }

.cwp-card {
    border-radius: 10px;
    padding: 7px 6px 7px 10px;
    margin-bottom: 4px;
    background: rgba(255, 255, 255, 0.04);
}
.cwp-card:hover { background: rgba(255, 255, 255, 0.08); }

.cwp-dot { min-width: 8px; min-height: 8px; border-radius: 4px; margin-top: 5px; background: #1fa874; }
.st-busy .cwp-dot    { background: #a854f7; box-shadow: 0 0 6px rgba(168, 84, 247, 0.8); }
.st-waiting .cwp-dot { background: #d95926; box-shadow: 0 0 6px rgba(217, 89, 38, 0.8); }
.st-bg .cwp-dot      { background: #3987e5; }
.st-idle .cwp-dot    { background: #1fa874; }

.cwp-root label.cwp-name   { font-size: 12px; font-weight: bold; color: #edf0f5; }
.cwp-root label.cwp-status { font-size: 10px; color: #b3b8c7; }
.st-busy label.cwp-status    { color: #c58cfa; }
.st-waiting label.cwp-status { color: #ec8a5f; }
.cwp-root label.cwp-meta   { font-size: 10px; color: #80858f; }
.cwp-root label.cwp-tool   { font-size: 10px; color: #c58cfa; }
.cwp-root label.cwp-task   { font-size: 11px; color: #b3b8c7; }
.cwp-root label.cwp-empty  { font-size: 11px; color: #80858f; font-style: italic; padding: 6px 2px; }
.cwp-root label.cwp-hint   { font-size: 10px; color: #5c606b; }

/* background-image/box-shadow reset: GTK4's default button styling paints a
   gradient and a shadow over anything set here. */
.cwp-add, .cwp-stop {
    background-image: none;
    box-shadow: none;
    border-radius: 11px;
}
.cwp-add {
    min-width: 24px;
    min-height: 22px;
    padding: 0 6px;
    border: 1px solid rgba(250, 191, 64, 0.35);
    background: rgba(250, 191, 64, 0.10);
    color: #fabf40;
    font-size: 14px;
    font-weight: bold;
}
.cwp-add:hover { background: rgba(250, 191, 64, 0.28); color: #edf0f5; }
.cwp-stop {
    min-width: 22px;
    min-height: 22px;
    padding: 0 6px;
    margin-left: 4px;
    border: 1px solid transparent;
    background: transparent;
    color: #80858f;
    font-size: 13px;
}
.cwp-stop:hover { border-color: rgba(247, 92, 92, 0.45); color: #f75c5c; }
.cwp-stop.armed {
    border-color: rgba(247, 92, 92, 0.6);
    background: rgba(247, 92, 92, 0.18);
    color: #f75c5c;
    font-weight: bold;
}
"""


def _run(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout).stdout
    except Exception:
        return ""


def kill_previous_popup():
    try:
        with open(PIDFILE) as f:
            os.kill(int(f.read().strip()), signal.SIGTERM)
    except Exception:
        pass
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))


def launch_new_session():
    """Hand the '+' button off to ClaudeNew.sh — ported from the retired
    ClaudeMonitor.py verbatim. Detached with start_new_session so the rofi
    prompts and the terminal they start outlive this (about to close) popup.
    """
    try:
        subprocess.Popen(
            [NEW_SESSION_SCRIPT], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def cursor_position():
    """Popup origin near the cursor, clamped to the focused monitor.

    Same technique as WordPopup.cursor_position() — coordinates are
    *monitor-relative* because that's what a `windowrulev2 move` rule wants.
    """
    try:
        cx, cy = (int(v) for v in _run(["hyprctl", "cursorpos"]).split(","))
    except Exception:
        cx, cy = 100, 100
    x, y = cx + 12, cy + 16
    try:
        mons = json.loads(_run(["hyprctl", "monitors", "-j"]))
        m = next((mo for mo in mons if mo.get("focused")), mons[0])
        scale = m.get("scale") or 1
        w, h = m["width"] / scale, m["height"] / scale
        if m.get("transform", 0) in (1, 3, 5, 7):
            w, h = h, w
        x = max(8, min(x - m["x"], w - POPUP_W - 8))
        y = max(8, min(y - m["y"], h - POPUP_MAX_H - 8))
    except Exception:
        pass
    return int(x), int(y)


# ----------------------------------------------------------------------- ui

class PopupWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app)
        self._was_active = False

        self.set_title(f"floatingsphere-popup-{os.getpid()}")
        self.set_decorated(False)
        self.set_default_size(POPUP_W, -1)
        self.add_css_class("fs-popup")

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("notify::is-active", self.on_active_changed)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("cwp-root")
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Claude sessions", xalign=0)
        title.add_css_class("cwp-title")
        header.append(title)
        self.usage_label = Gtk.Label(label="", xalign=1)
        self.usage_label.add_css_class("cwp-usage")
        self.usage_label.set_hexpand(True)
        # Without an explicit cap, GTK grows the *window* to fit this
        # label's natural (un-truncated) width instead of ellipsizing it —
        # `set_ellipsize` alone only kicks in once allocated width is
        # already less than natural width, which never happens if nothing
        # else is capping it. That's what let the quota string (two windows
        # + reset clocks, easily 30+ chars) push the popup wider than
        # POPUP_W and off the right edge of the screen.
        self.usage_label.set_max_width_chars(24)
        self.usage_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        header.append(self.usage_label)
        root.append(header)

        sep = Gtk.Box()
        sep.add_css_class("cwp-sep")
        root.append(sep)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroller = Gtk.ScrolledWindow(child=self.list_box)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(POPUP_MAX_H - 70)
        root.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_margin_top(4)

        hint = Gtk.Label(label="click to jump · ■ twice to stop · Esc", xalign=0)
        hint.add_css_class("cwp-hint")
        hint.set_hexpand(True)
        footer.append(hint)

        add = Gtk.Button(label="+")
        add.add_css_class("cwp-add")
        add.set_tooltip_text("new session — pick a project, then an opening task")
        add.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        add.connect("clicked", self._on_new_session)
        footer.append(add)

        root.append(footer)

        self.populate()

    def _on_new_session(self, _button):
        launch_new_session()
        self.close()

    def on_key(self, _ctrl, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.close()
            return True
        return False

    def on_active_changed(self, *_):
        if self.is_active():
            self._was_active = True
        elif self._was_active:
            self.close()  # clicked/focused elsewhere -> dismiss

    def populate(self):
        rate_limits, cached_at = read_usage()
        usage, warn, _stale = usage_text(rate_limits, cached_at)
        self.usage_label.set_text(usage)
        self.usage_label.set_css_classes(["cwp-usage-warn" if warn else "cwp-usage"])

        self._fill_list()

    def _fill_list(self):
        while (child := self.list_box.get_first_child()) is not None:
            self.list_box.remove(child)
        sessions = collect_sessions()
        if not sessions:
            empty = Gtk.Label(label="no sessions running", xalign=0)
            empty.add_css_class("cwp-empty")
            self.list_box.append(empty)
            return

        for session in sessions:
            self.list_box.append(self._build_card(session))

    def _on_card_clicked(self, _gesture, _n_press, _x, _y, pids):
        focus_session(pids)
        self.close()

    def _on_stop_clicked(self, button, pids):
        """First click arms the button, the second stops the session."""
        if not button.has_css_class("armed"):
            button.add_css_class("armed")
            button.set_label("stop?")
            GLib.timeout_add(DISARM_MS, self._disarm, button)
            return
        stop_session(pids)
        button.set_sensitive(False)
        button.set_label("…")
        # give the processes a moment to exit and drop out of the registry
        GLib.timeout_add(700, lambda: self._fill_list() or False)

    def _disarm(self, button):
        if button.get_sensitive():
            button.remove_css_class("armed")
            button.set_label("■")
        return False

    def _build_card(self, session):
        label, css_class = STATUS_STYLE.get(session["status"], STATUS_STYLE["idle"])

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        card.add_css_class("cwp-card")
        card.add_css_class(css_class)

        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("released", self._on_card_clicked, session["pids"])
        card.add_controller(click)
        card.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        dot = Gtk.Box(valign=Gtk.Align.START)
        dot.add_css_class("cwp-dot")
        card.append(dot)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        card.append(body)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(label=session["name"], xalign=0)
        name.add_css_class("cwp-name")
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name.set_max_width_chars(26)  # same reasoning as usage_label above
        top.append(name)

        status_text = label
        elapsed = fmt_elapsed(session["status_since"])
        if elapsed:
            status_text = f"{label} {elapsed}"
        if session["status"] == "waiting" and session["waiting_for"]:
            status_text = f"{session['waiting_for']} · {elapsed}" if elapsed \
                else session["waiting_for"]

        status = Gtk.Label(label=status_text, xalign=1)
        status.add_css_class("cwp-status")
        status.set_hexpand(True)
        top.append(status)
        body.append(top)

        meta_text = shorten_path(session["cwd"])
        active = session["status"] in ("busy", "bg", "agent")
        if active and session["tool"]:
            meta_text = f"{meta_text}   {session['tool']}"
        if session["headless"] and session["model"]:
            meta_text = f"{meta_text}   {session['model']}"
        meta = Gtk.Label(label=meta_text, xalign=0)
        meta.add_css_class("cwp-tool" if active and session["tool"] else "cwp-meta")
        meta.set_ellipsize(1)  # Pango.EllipsizeMode.START — keep the leaf dir
        meta.set_max_width_chars(40)  # cwd can be arbitrarily long otherwise
        body.append(meta)

        if session["task"]:
            task = Gtk.Label(label=truncate(session["task"], 90), xalign=0)
            task.add_css_class("cwp-task")
            task.set_wrap(True)
            task.set_max_width_chars(40)
            task.set_lines(2)
            task.set_ellipsize(3)
            body.append(task)

        stop = Gtk.Button(label="■", valign=Gtk.Align.CENTER)
        stop.add_css_class("cwp-stop")
        stop.set_tooltip_text("stop this session (click twice)")
        stop.connect("clicked", self._on_stop_clicked, session["pids"])
        card.append(stop)

        return card


class PopupApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = PopupWindow(self)

        # pre-map positioning — must land before the window maps, exactly
        # like WordPopup/PinNotes (a post-map move leaves the window
        # permanently uncomposited on this Hyprland build).
        # float/pin/etc. set at runtime so nothing depends on hyprland.conf
        # (the waybar copy of this popup got these from WindowRules.conf)
        sel = f"class:^({APP_ID})$"
        rules = ["float", "noborder", "rounding 14", "opaque", "nodim", "noanim", "pin"]
        subprocess.run(
            ["hyprctl", "--batch",
             ";".join(f"keyword windowrulev2 {r}, {sel}" for r in rules)],
            capture_output=True)

        x, y = cursor_position()
        subprocess.run(
            ["hyprctl", "keyword", "windowrulev2",
             f"move {x} {y},title:^({win.get_title()})$"],
            capture_output=True)

        win.present()


if __name__ == "__main__":
    kill_previous_popup()
    sys.exit(PopupApp().run(None))
