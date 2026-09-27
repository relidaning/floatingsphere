# Sessions

## 2026-09-27 — Web view for phone/tablet (sphere-web)
Added `sphere_web.py` (stdlib HTTP on 0.0.0.0:8765, `/api/state` + `web/index.html`, a canvas port of the sphere, the hover-card meters and the session list), installed as the systemd user unit `sphere-web.service`. Starting headless instances from the phone was dropped because claude-maxer already does that; later the popup's two-tap ■ stop was added (`POST /api/stop`, pids resolved server-side, JSON + Origin check against cross-site requests), plus a sphere home-screen icon (`web/make_icons.py`). There is no auth because the user reaches the PC over their own sing-box tunnel. The JSON carries the PC's `now` so countdowns don't depend on the phone's clock.

## 2026-09-27 — Resource audit; hover-by-motion investigation
Measured the running sphere at ~2.5% of one core and 188 MB RSS (only ~61 MB private; the rest is shared GTK/NVIDIA GL libs). Nearly all the CPU comes from the constant 24 fps redraw that animates a 1.5px ripple. Proposed a slower idle frame rate (8–10 fps, 24 during transitions/hover) plus an A/B test of `GSK_RENDERER=cairo`; the user approved both. Work then moved to "can't hover by moving the cursor": warping the cursor onto the sphere does open the card, and uinput glide tests had started when the session ended. No code was changed yet.

## 2026-09-27 — Initial floating sphere usage monitor
Built `floatingsphere.py`, a GTK4/cairo always-on-top sphere (Hyprland float+pin via runtime `hyprctl` windowrules) showing running Claude instance count, a 7d-usage ring, and 5h-window "water" read from claude-maxer's `~/.claude/state/usage_snapshot.json`. Idle vs. working (green/purple) is inferred from each instance's process-tree CPU ticks since measurements showed busy ≈5–8 ticks/2s vs. idle 0–1; Hyprland `move` rules turned out to be monitor-relative, which caused an initial off-screen placement.
