# floatingsphere

A small always-on-top sphere at the right edge of the screen that shows how much of your
Claude subscription you're using, and what your Claude Code sessions are doing.

```
        ╭── 7-day ring: amber arc = used %, white tick = time elapsed in the week
      ╭─┴─╮
     │  2  │ ← running Claude Code sessions
     │~~~~~│ ← water: height = current 5-hour window used %
      ╰───╯     green = all sessions idle, purple = any busy / waiting
                white rim ticks = time elapsed in the 5-hour window
```

If the water is above the rim ticks (or the amber arc passes the white ring tick), you are
using quota faster than time is passing and will hit the limit before the reset.

## Interaction

| Action              | Does                                                                        |
| ------------------- | --------------------------------------------------------------------------- |
| Hover               | Chart card: 5h / 7d meters with pace marker and projection, sessions by status |
| Left-click          | Session list; click a card to jump to its terminal, `+` starts a new session |
| Double right-click  | Quit                                                                        |
| `SUPER` + drag      | Move (it's a normal Hyprland floating window)                               |

## Data sources

- **Usage**: `~/.claude/state/usage_snapshot.json` (`rate_limits.five_hour` / `seven_day`
  → `used_percentage`, `resets_at`, plus `cached_at`), refreshed every 15 min by the
  claude-maxer fetch cron. The sphere re-reads it whenever the file changes and dims the
  display when the snapshot is more than 40 min old.
- **Sessions**: Claude Code's own registry, `~/.claude/sessions/<pid>.json` (live status
  `busy` / `waiting` / `idle`), checked against `/proc`. Subagents are hidden; headless
  background runs (cron jobs) are counted.

## Requirements

- Linux + Hyprland (window placement uses `hyprctl`)
- Python 3 with PyGObject, GTK 4, pycairo, PangoCairo
- `rofi` and `kitty` for the popup's `+` new-session button

## Run

```sh
python3 floatingsphere.py &
```

Autostart via Hyprland:

```ini
# Startup_Apps.conf
exec-once = python3 /path/to/floatingsphere/floatingsphere.py
```

The app sets its own window rules at launch, but `hyprctl reload` wipes runtime rules, so
also put these in your Hyprland config to keep the sphere borderless and transparent
after a reload:

```ini
windowrulev2 = float, class:^(dev.floatingsphere)$
windowrulev2 = pin, class:^(dev.floatingsphere)$
windowrulev2 = size 75 75, class:^(dev.floatingsphere)$
windowrulev2 = noborder, class:^(dev.floatingsphere)$
windowrulev2 = noshadow, class:^(dev.floatingsphere)$
windowrulev2 = noblur, class:^(dev.floatingsphere)$
windowrulev2 = noinitialfocus, class:^(dev.floatingsphere)$
windowrulev2 = rounding 0, class:^(dev.floatingsphere)$
windowrulev2 = noanim, class:^(dev.floatingsphere)$
windowrulev2 = nodim, class:^(dev.floatingsphere)$
windowrulev2 = opaque, class:^(dev.floatingsphere)$
windowrulev2 = float, class:^(dev.floatingsphere.popup)$
windowrulev2 = noborder, class:^(dev.floatingsphere.popup)$
windowrulev2 = rounding 14, class:^(dev.floatingsphere.popup)$
windowrulev2 = opaque, class:^(dev.floatingsphere.popup)$
windowrulev2 = nodim, class:^(dev.floatingsphere.popup)$
windowrulev2 = noanim, class:^(dev.floatingsphere.popup)$
windowrulev2 = pin, class:^(dev.floatingsphere.popup)$
```

## Files

| File                   | Role                                                          |
| ---------------------- | ------------------------------------------------------------- |
| `floatingsphere.py`    | The sphere window, drawing, polling, Hyprland placement      |
| `hovercard.py`         | Animated chart card shown on hover                            |
| `sphere_popup.py`      | Clickable session list (left-click)                           |
| `claude_sessions.py`   | Reads Claude Code's session registry and transcripts          |
| `claude_new.sh`        | `+` button: pick a project and a task in rofi, open kitty     |
| `rofi-claude-new.rasi` | rofi theme for `claude_new.sh`                                |
