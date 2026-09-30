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
fi

# sphere-web.service runs sphere_web.py from /data/apps/floatingsphere.
[[ "$REPO_DIR" == /data/apps/floatingsphere ]] \
    || echo "  WARNING: sphere-web.service expects this repo at /data/apps/floatingsphere (it is at $REPO_DIR)."
if command -v systemctl &>/dev/null; then
    mkdir -p "$HOME/.config/systemd/user"
    ln -sfn "$REPO_DIR/sphere-web.service" "$HOME/.config/systemd/user/sphere-web.service"
    systemctl --user daemon-reload \
        && systemctl --user enable --now sphere-web.service \
        && echo "==> sphere-web.service enabled (http://localhost:8765)" \
        || echo "  WARNING: could not enable sphere-web.service."
fi
[[ -f "$HOME/.config/floatingsphere/tls-cert.pem" ]] \
    || echo "  NOTE: phone push needs ~/.config/floatingsphere/tls-{cert,key}.pem (see README)."
echo "==> floatingsphere ready."
