#!/usr/bin/env bash
# Install floatingsphere: enable the sphere-web user service and check the
# runtime dependencies. The sphere itself is started by Hyprland
# (dotfiles hypr/UserConfigs/Startup_Apps.conf, which also holds its window
# rules). Idempotent; the dotfiles install.sh calls it when this repo is
# cloned next to it.
# Usage: ./install.sh

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

missing=()
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('PangoCairo','1.0'); from gi.repository import Gtk, PangoCairo" 2>/dev/null \
    || missing+=("PyGObject with GTK4 + PangoCairo (apt: python3-gi python3-gi-cairo gir1.2-gtk-4.0 / pacman: python-gobject python-cairo gtk4)")
python3 -c "import cryptography" 2>/dev/null \
    || missing+=("cryptography, for phone push (apt: python3-cryptography / pacman: python-cryptography)")
python3 -c "import yaml" 2>/dev/null \
    || missing+=("PyYAML (apt: python3-yaml / pacman: python-yaml)")
command -v kitty   &>/dev/null || missing+=("kitty (sessions open and are typed into through it)")
command -v hyprctl &>/dev/null || missing+=("hyprctl (Hyprland: window placement and focus)")
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "==> floatingsphere: missing dependencies:"
    printf '  - %s\n' "${missing[@]}"
    exit 1
fi

# Render the unit for this checkout. Replace atomically so a legacy deployed
# symlink cannot cause a write into the tracked template.
if command -v systemctl &>/dev/null; then
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    changed=$(python3 - "$REPO_DIR" "$unit_dir" <<'PY'
from pathlib import Path
import os,sys,tempfile
repo, directory = map(Path,sys.argv[1:])
directory.mkdir(parents=True,exist_ok=True)
unit=directory/'sphere-web.service'
quoted=str(repo).replace('\\','\\\\').replace('"','\\"').replace('%','%%')
text=(repo/'sphere-web.service').read_text().replace('/data/apps/floatingsphere/sphere_web.py','"'+quoted+'/sphere_web.py"')
if unit.is_file() and not unit.is_symlink() and unit.read_text()==text and unit.stat().st_mode & 0o777 == 0o644:
    print('0')
else:
    fd,path=tempfile.mkstemp(dir=directory,prefix='.sphere-web-')
    with os.fdopen(fd,'w') as stream:stream.write(text)
    os.chmod(path,0o644)
    os.replace(path,unit)
    print('1')
PY
    ) || exit 1
    if [[ "$changed" == 1 ]] || [[ "$(systemctl --user show -p NeedDaemonReload --value sphere-web.service)" == yes ]]; then
        systemctl --user daemon-reload || exit 1
    fi
    systemctl --user is-enabled --quiet sphere-web.service || systemctl --user enable sphere-web.service || exit 1
    if [[ "$changed" == 1 ]] && systemctl --user is-active --quiet sphere-web.service; then
        systemctl --user restart sphere-web.service || exit 1
    else
        systemctl --user is-active --quiet sphere-web.service || systemctl --user start sphere-web.service || exit 1
    fi
    echo "==> sphere-web.service enabled (http://localhost:8765)"
else
    echo 'ERROR: systemctl is required for sphere-web.service.' >&2
    exit 1
fi
[[ -f "$HOME/.config/floatingsphere/tls-cert.pem" ]] \
    || echo "  NOTE: phone push needs ~/.config/floatingsphere/tls-{cert,key}.pem (see README)."
echo "==> floatingsphere ready."
[[ ${#missing[@]} == 0 ]]
