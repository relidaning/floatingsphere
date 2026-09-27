# Sessions

## 2026-09-27 — Web + sheet: floating button, typing, auto-trust
Moved + out of the Sessions header into a floating round button at the bottom right (thumb reach; hidden while the sheet is open, toast moved above it). Focusing the opening task scrolls it to the top of the sheet (a spacer gives the room), and on phones the sheet is fitted to `visualViewport` so iOS's keyboard doesn't cover it. `start_session()` now pre-accepts claude's workspace trust dialog by setting `hasTrustDialogAccepted` in `~/.claude.json`. Verified with a launch into the untrusted `blank_dir` (went straight to the prompt, screenshot via grim) and the layout in headless Chrome with a shrunken viewport, not on a real phone.

## 2026-09-27 — Start a session from the web view (+)
Added a + to the Sessions header in `web/index.html`, which opens a bottom sheet on phones (a centered dialog from 760px): a filterable project grid for `/data/apps/*` (recent first, shared with `claude_new.sh`), switches for `--dangerously-skip-permissions` (on) and `--rc` (off; sent as `--remote-control`), an optional opening task, and a command preview. `POST /api/new` in `sphere_web.py` validates the project name against the listing and runs kitty through `systemd-run --user --expand-environment=no`. Tested end to end with a launch into `blank_dir`: the session registered, got both flags, and had a `bridgeSessionId` (Remote Control live); stopped via `/api/stop`. Checked the layout at 390px and 1280px in headless Chrome, not on a real phone.

## 2026-09-27 — Disable zoom in the web view on mobile
Turned off pinch and double-tap zoom in `web/index.html` in three ways: viewport `maximum-scale=1, user-scalable=no` (Android), `touch-action: pan-x pan-y` (also stops a double-tap on ■ from zooming), and a `gesturestart` `preventDefault()` because iOS Safari ignores `user-scalable=no`. Committed as `82754b0` and pushed. It was only checked with curl, not on a real phone.

## 2026-09-27 — Container vs. systemd service for sphere-web
Benchmarked `sphere_web.py` in a `python:3-alpine` container (port 8766) against the live `sphere-web.service`: the container was ~50% slower (docker-proxy hop), used more CPU and ~40 MB more memory, and couldn't stop sessions — Docker's default AppArmor blocks SIGTERM to host pids and `stop_session()` swallows the `OSError`, so ■ silently does nothing. Recommendation: stay on the systemd user service (optionally add systemd hardening); no code changed, test container removed.

## 2026-09-27 — Web view for phone/tablet (sphere-web)
Added `sphere_web.py` (stdlib HTTP on 0.0.0.0:8765, `/api/state` + `web/index.html`, a canvas port of the sphere, the hover-card meters and the session list), installed as the systemd user unit `sphere-web.service`. Starting headless instances from the phone was dropped because claude-maxer already does that; later the popup's two-tap ■ stop was added (`POST /api/stop`, pids resolved server-side, JSON + Origin check against cross-site requests), plus a sphere home-screen icon (`web/make_icons.py`). There is no auth because the user reaches the PC over their own sing-box tunnel. The JSON carries the PC's `now` so countdowns don't depend on the phone's clock.

## 2026-09-27 — Resource audit; hover-by-motion investigation
Measured the running sphere at ~2.5% of one core and 188 MB RSS (only ~61 MB private; the rest is shared GTK/NVIDIA GL libs). Nearly all the CPU comes from the constant 24 fps redraw that animates a 1.5px ripple. Proposed a slower idle frame rate (8–10 fps, 24 during transitions/hover) plus an A/B test of `GSK_RENDERER=cairo`; the user approved both. Work then moved to "can't hover by moving the cursor": warping the cursor onto the sphere does open the card, and uinput glide tests had started when the session ended. No code was changed yet.

## 2026-09-27 — Initial floating sphere usage monitor
Built `floatingsphere.py`, a GTK4/cairo always-on-top sphere (Hyprland float+pin via runtime `hyprctl` windowrules) showing running Claude instance count, a 7d-usage ring, and 5h-window "water" read from claude-maxer's `~/.claude/state/usage_snapshot.json`. Idle vs. working (green/purple) is inferred from each instance's process-tree CPU ticks since measurements showed busy ≈5–8 ticks/2s vs. idle 0–1; Hyprland `move` rules turned out to be monitor-relative, which caused an initial off-screen placement.
