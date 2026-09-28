# Sessions

## 2026-09-28 — Plainer notification wording
The user found the notifications childish (emoji, checkbox symbols, numbered options) and asked for something more grown-up. Now:
- **Title:** the session name.
- **Body:** a status line, "Awaiting approval" (permission prompt), "Awaiting your answer" (AskUserQuestion) or "Completed", then one line of detail. That's the tool call ("Bash command: git push origin master — Push the branch to GitHub."), the question, or the start of the reply.
- **Cleanup:** markdown and terminal glyphs are stripped by `notify.plain()`.
- **Enable message:** "Notifications enabled".

Tested with sample dialog screens and the notification rules. Not yet seen on the phone.

## 2026-09-28 — Tapping a notification opens that session
The user asked whether tapping a notification takes them to the session. It already did (`sw.js` `notificationclick`), but it had never been tested. The handler's logic moved into `openSession(id)` in `sw.js` so Playwright could call it inside the service worker. Checked in headless Chrome: with the app open on the list, it opens the session's chat; from inside another chat, it switches; with the app closed, opening `/#<id>` (what the tap opens) goes straight into the chat. A tap from inside another chat now replaces that chat in history, so back goes to the list rather than the previous chat. A real tap on the iPhone isn't tested.

## 2026-09-28 — Red water while a session waits for an answer
The sphere's water is now red whenever any session is `waiting` (a permission prompt or question to answer); otherwise it's purple while any is busy and green when all are idle. This applies to both `floatingsphere.py` and the web canvas. The red is a deeper crimson (0.93, 0.16, 0.24) than `RING_HOT`, because with the 7d ring at 94% the first try in the ring's coral red blended into it. Checked in headless Chrome with a faked waiting session; the desktop sphere was restarted via `hyprctl dispatch exec`.

## 2026-09-28 — Push notifications for idle / waiting sessions
The phone now gets a notification when a session finishes a turn (✅, with its last reply) or waits on a dialog (⏳, with the options). Tapping one opens that session's chat in the web app. A first version sent them through the existing Telegram bot, but the user wanted everything in their own web app, so it was replaced with Web Push:
- **Server:** `notify.py` watches session status (6s settle; sessions focused on the desktop are skipped). `webpush.py` does the encryption and VAPID signing with stdlib + `cryptography`.
- **Page:** `web/sw.js` shows the pushes, and a 🔔 in the Sessions header subscribes.
- **HTTPS:** sphere_web now also serves HTTPS on 8766 with a copy of besmart's mkcert cert, which the iPhone already trusts.

Tested: encryption decrypted by `http_ece`; the VAPID JWT is accepted by Apple (a dummy token got `BadWebPushToken`, not a JWT error); a real round trip in headless Chrome (🔔 → FCM subscription → server push 201 → notification shown with the right title, body, tag and id). The notification rules were tested with fake sessions. Not yet tested on the iPhone: the user has to add `https://192.168.255.6:8766` to the home screen and tap 🔔.

## 2026-09-28 — Chat with a session from the phone
Tapping a session in the web view now opens a chat panel:
- **Conversation:** the transcript, incremental by byte offset.
- **Composer:** prompts are typed into the session's kitty window through kitty remote control.
- **Dialogs:** permission prompts and AskUserQuestion are parsed off the terminal screen and shown as a pop-up sheet, including multi-select, the Submit row and "Type something" free text.
- **⌨ view:** the raw screen with a key pad.
- **Leaving:** swipe right, ‹, or the phone's back gesture.

New `session_chat.py`, endpoints `/api/chat`, `/api/screen`, `/api/send`, `/api/keys`, and `allow_remote_control socket-only` + `listen_on` added to the dotfiles `kitty.conf`. Only kitty windows started after that change can be typed into. Claude Code's peer messaging socket was looked at and rejected: it sends untrusted peer messages and can't answer dialogs.

Tested end to end with a haiku session in a fresh kitty, in headless Chromium at 390px and 1280px: send, permission Yes, single-select, multi-select + Submit, free text, swipe back and ‹. Not tested on a real phone.

## 2026-09-27 — Interactive session view from the phone (started, not finished)
Goal: tap a session in the web view to open an interactive screen (send prompts, read replies, choose options in a dialog, swipe back to the list). Only exploration so far: after looking into Claude Code's uds messaging sockets, the chosen route was kitty remote control, turned on in the dotfiles `kitty/kitty.conf` (`allow_remote_control socket-only`, `listen_on unix:${XDG_RUNTIME_DIR}/kitty-{kitty_pid}`; not committed), and `kitty @ send-text`/`send-key`/`get-text` were tested against a throwaway haiku claude started through `systemd-run` (a plain launch picked up this session's env). No floatingsphere code changed, and the test `rctest` claude/kitty (pid 568224) was left running.

## 2026-09-27 — Fix purple water with no working sessions
The water turned purple because a jellyfin-web session reported a new registry status, `"shell"` (the turn was over but a background `webpack serve` was still running), and `floatingsphere.py` treats anything that isn't `idle` as working. `collect_sessions()` now maps every interactive status other than `busy`/`waiting` to `idle`, so the sphere, hover card, popup and web view agree even if more statuses appear later. `sphere-web.service` was restarted; the desktop sphere needed a manual restart to load the fix.

## 2026-09-27 — Release v0.03 (start sessions from the phone)
Committed the + sheet, floating button and auto-trust work as `1399aa1`, pushed `master`, and pushed an annotated tag `v0.03` (annotated to match the earlier tags). The earlier tags are named inconsistently (`v0.01`, `v0.0.2`); `v0.03` was used exactly as the user asked.

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
