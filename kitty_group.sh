#!/usr/bin/env bash
# Run a command that opens a kitty window (and returns right away, e.g.
# `setsid -f kitty …` or `systemd-run … kitty …`), then move that window into
# the kitty group on the active workspace as a new tab, like the dotfiles'
# OpenTermGrouped.sh does for Super+Return.
#
# Every kitty is a group (dotfiles WindowRules.conf: `group set`), so a lone
# window counts as a group of one. A real group (two or more tabs) on the
# active workspace wins over a lone kitty even when the lone one is focused:
# a launch from the phone happens with whatever window was focused last, and
# the tabs belong with the existing tab group. Among equals, the most recently
# focused one wins. With no kitty there, the new one stays its own group.
#
# Hyprland's moveintogroup only takes a direction, so the group is focused
# before the launch: dwindle then splits the new window off it, which makes
# the two neighbours. Cursor warps are suspended around the focus changes so
# the pointer stays where it was (on the sphere, say).
#
# The exit status is the command's, so a failed launch is still reported.

set -uo pipefail

clients=$(hyprctl clients -j 2>/dev/null) || clients='[]'
ws=$(hyprctl activeworkspace -j 2>/dev/null | jq -r '.id // empty')

# The visible member of the chosen group (a hidden one is a background tab).
target=$(jq -r --argjson ws "${ws:-0}" '
    [.[] | select(.class == "kitty" and .workspace.id == $ws
                  and (.grouped | length) > 0 and (.hidden | not) and (.floating | not))]
    | sort_by([(if (.grouped | length) > 1 then 0 else 1 end), .focusHistoryID])
    | .[0].address // empty' <<<"$clients")
before=$(jq -c '[.[].address]' <<<"$clients")

warps=$(hyprctl -j getoption cursor:no_warps 2>/dev/null | jq -r '.int // 0')
dispatch() {
    hyprctl --batch "keyword cursor:no_warps 1; $1; keyword cursor:no_warps $warps" >/dev/null
}

[ -n "$target" ] && dispatch "dispatch focuswindow address:$target"

"$@"
rc=$?
[ "$rc" -eq 0 ] && [ -n "$target" ] || exit "$rc"

# Up to ~10s for the window: a cold kitty under load can take a few seconds.
new=""
for _ in $(seq 1 200); do
    sleep 0.05
    new=$(hyprctl clients -j | jq -r --argjson before "$before" '
        [.[] | select(.class == "kitty" and (.address as $a | $before | index($a) | not))]
        | .[0].address // empty')
    [ -n "$new" ] && break
done
[ -n "$new" ] || { echo "kitty_group: no new kitty window" >&2; exit 0; }

# The window shows up in the client list before dwindle has tiled it, at a provisional
# position, and a direction taken from that one points away from the group. Wait until
# its geometry holds still for a few polls.
geom() { hyprctl clients -j | jq -c --arg n "$new" '.[] | select(.address == $n) | [.at, .size]'; }
last=$(geom); same=0
for _ in $(seq 1 40); do
    sleep 0.05
    g=$(geom)
    if [ "$g" = "$last" ]; then same=$((same + 1)); [ "$same" -ge 3 ] && break; else same=0; last=$g; fi
done

# Direction from the new window to the group, by their centres.
read -r dx dy < <(hyprctl clients -j | jq -r --arg t "$target" --arg n "$new" '
    def c(a): .[] | select(.address == a) | [.at[0] + .size[0] / 2, .at[1] + .size[1] / 2];
    [c($t)] as [$tc] | [c($n)] as [$nc]
    | if $tc and $nc then "\($tc[0] - $nc[0] | floor) \($tc[1] - $nc[1] | floor)" else empty end')
[ -n "${dx:-}" ] || exit 0

if [ "${dx#-}" -ge "${dy#-}" ]; then
    if [ "$dx" -lt 0 ]; then dir=l; else dir=r; fi
else
    if [ "$dy" -lt 0 ]; then dir=u; else dir=d; fi
fi
dispatch "dispatch focuswindow address:$new; dispatch moveintogroup $dir"

joined=$(hyprctl clients -j | jq -r --arg t "$target" --arg n "$new" '
    .[] | select(.address == $t) | .grouped | index($n) != null')
[ "$joined" = true ] || echo "kitty_group: moveintogroup $dir did not join (dx=$dx dy=$dy)" >&2
exit 0
