# Sessions

## 2026-09-27 — Initial floating sphere usage monitor
Built `floatingsphere.py`, a GTK4/cairo always-on-top sphere (Hyprland float+pin via runtime `hyprctl` windowrules) showing running Claude instance count, a 7d-usage ring, and 5h-window "water" read from claude-maxer's `~/.claude/state/usage_snapshot.json`. Idle vs. working (green/purple) is inferred from each instance's process-tree CPU ticks since measurements showed busy ≈5–8 ticks/2s vs. idle 0–1; Hyprland `move` rules turned out to be monitor-relative, which caused an initial off-screen placement.
