#!/usr/bin/env bash
# Start a new Claude Code session — driven by the "+" button in the
# floating sphere popup (sphere_popup.py), or bindable to a key.
#
# Two rofi prompts, then a terminal:
#   1. the project directory (recently used first, then $PROJECTS_ROOT/*)
#   2. an opening task, sent to Claude as its first prompt (Enter to skip)
#   3. kitty running `claude --name <dir basename> "<task>"` in that project
#
# Why rofi and not a GTK dialog: the popup that launches this closes itself
# the moment "+" is clicked (before this script even runs), so there's no
# lingering window whose rules a dialog could awkwardly inherit — but rofi's
# own independent focus is simpler regardless and was kept.
#
# This used to also hand focus back from a persistent GTK panel's parking
# workspace before opening rofi (the panel — since retired — stayed focused
# after the click that launched this, so "the active workspace" at that point
# was the panel's rather than the one being worked on). The waybar popup this
# now launches from isn't parked anywhere — it appears at the cursor and is
# already gone by the time this script runs — so that workaround no longer
# applies and was removed rather than left as a permanent no-op.

set -uo pipefail

PROJECTS_ROOT="${CLAUDE_NEW_PROJECTS_ROOT:-/data/apps}"
RECENT_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/claude-monitor/recent-dirs"
RECENT_MAX=15
ROFI_CONFIG="$(dirname "$(readlink -f "$0")")/rofi-claude-new.rasi"

notify() {
    command -v notify-send >/dev/null 2>&1 && notify-send -a "Claude" "$@"
}

# ------------------------------------------------------------------ prompts

candidates() {
    local dir
    if [ -f "$RECENT_FILE" ]; then
        while IFS= read -r dir; do
            [ -n "$dir" ] && [ -d "$dir" ] && printf '%s\n' "$dir"
        done < "$RECENT_FILE"
    fi
    find "$PROJECTS_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort
}

record_recent() {
    local dir=$1 tmp
    mkdir -p "$(dirname "$RECENT_FILE")" || return 0
    tmp=$(mktemp) || return 0
    {
        printf '%s\n' "$dir"
        [ -f "$RECENT_FILE" ] && cat "$RECENT_FILE"
    } | awk 'NF && !seen[$0]++' | head -n "$RECENT_MAX" > "$tmp"
    mv "$tmp" "$RECENT_FILE"
}

# A picker is already open — a second click should not stack another one.
pidof rofi >/dev/null 2>&1 && exit 0

dir=$(candidates | awk 'NF && !seen[$0]++' |
    rofi -dmenu -i -config "$ROFI_CONFIG" -p "project" \
        -theme-str 'entry { placeholder: "  project directory"; }')
[ -n "$dir" ] || exit 0

# rofi returns whatever was typed when nothing matched, so a bare project name
# ("myalgo") and an absolute path both work.
dir="${dir/#\~/$HOME}"
if [ ! -d "$dir" ] && [ -d "$PROJECTS_ROOT/$dir" ]; then
    dir="$PROJECTS_ROOT/$dir"
fi
if [ ! -d "$dir" ]; then
    notify "No such project directory: $dir"
    exit 1
fi

# Escape aborts (rofi exits 1); Enter on an empty line starts a bare session.
task=$(printf '' | rofi -dmenu -i -config "$ROFI_CONFIG" -l 0 -p "task" \
    -theme-str 'listview { enabled: false; }
                entry { placeholder: "  opening task (Enter to skip)"; }') || exit 0

record_recent "$dir"

# Passed through the environment rather than interpolated into the command
# string: the task is free text, and this way no amount of quoting in it can
# change what the shell ends up running.
export CLAUDE_NEW_LABEL="$(basename "$dir")"
export CLAUDE_NEW_PROMPT="$task"

# `zsh -ic` sources .zshrc (the proxy exports claude needs live there), and the
# trailing `exec zsh -i` keeps the terminal around after the session ends.
setsid kitty --directory "$dir" \
    zsh -ic 'command claude --name "$CLAUDE_NEW_LABEL" ${CLAUDE_NEW_PROMPT:+"$CLAUDE_NEW_PROMPT"}; exec zsh -i' \
    >/dev/null 2>&1 &
