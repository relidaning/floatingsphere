# floatingsphere

Floating desktop monitor for Claude subscription usage and Claude Code sessions. Python, GTK4 via PyGObject, cairo drawing. Run: `python3 floatingsphere.py` (double right-click quits). User-facing overview in `README.md`.

## Visual mapping
- Center number: live Claude Code sessions (`len(collect_sessions())`).
- Outer ring: 7-day usage % (white tick = time elapsed in the week).
- Water: height = current 5h window usage %; white rim ticks = time elapsed in the 5h window. Green when every session is idle, purple while any is busy/waiting.
- Snapshot older than `STALE_S` (40 min) is drawn dimmed.
- Hover (after `HOVER_OPEN_MS`) opens `hovercard.py` in a `Gtk.Popover` (autohide off, so no grab): 5h/7d meters with pace tick and projection-at-reset, sessions bar by status. Animations are per-frame easing driven by the app's `FPS` timer; `restart()` on open sweeps values in from zero.
- Left-click spawns `sphere_popup.py` (session cards → `focus_session()`; `+` → `claude_new.sh`).

## Data sources
- Usage: claude-maxer's snapshot `~/.claude/state/usage_snapshot.json` (`rate_limits.five_hour`/`seven_day` → `used_percentage`, `resets_at`; `cached_at`), refreshed every ~15 min by maxer's cron (`/data/apps/lidaning-skills/skills/claude-maxer/maxer.py`); re-read on mtime change.
- Sessions: `claude_sessions.py` reads Claude Code's registry `~/.claude/sessions/<pid>.json` (authoritative `status`: busy/waiting/idle). **Don't go back to CPU-based busy detection** — the first version did (process-tree CPU ticks) and flickered purple on idle sessions whenever an MCP child or redraw spiked.
- `claude_sessions.py`, `sphere_popup.py`, `claude_new.sh`, `rofi-claude-new.rasi` came from the retired waybar monitor in `/data/apps/dotfiles` (removed in its commit `02e0278`).

## Window placement (Hyprland)
- No layer-shell (gtk4-layer-shell isn't installed); window rules keyed on class `dev.floatingsphere` (popup: `dev.floatingsphere.popup`).
- The app sets rules at launch via `hyprctl keyword windowrulev2`, but `hyprctl reload` wipes runtime rules and the sphere then gets the user's border/shadow/blur and `inactive_opacity` 0.7 (a visible square) — so the static rules are also in dotfiles `hypr/UserConfigs/WindowRules.conf`. Only `move` stays launch-time (computed from the monitor size).
- `move` coordinates are **monitor-relative** and must account for monitor scale.
- Needs `nodim` + `opaque`: the user's config dims and fades inactive windows.
- Focus: the user runs `input:float_switch_override_focus = 0`, so once a click focuses the (floating) sphere, hovering back onto a tiled window doesn't take focus back. `nofocus` is **not** a fix — it also stops pointer events (no hover, no clicks). Instead `on_active` dispatches `focuscurrentorlast` whenever the sphere becomes active.
- Testing hover: `hyprctl dispatch movecursor` alone sends no pointer enter; a real motion is needed (a python-evdev uinput device nudging REL_X works — `/dev/uinput` is user-writable).
