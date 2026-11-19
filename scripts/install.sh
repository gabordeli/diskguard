#!/usr/bin/env bash
# Install a DiskGuard egg on the Deluge host (run as root there).
#
#   ./scripts/install.sh path/to/DiskGuard-*.egg
#   DELUGE_CONFIG_DIR=/var/lib/deluged/config ./scripts/install.sh
set -euo pipefail

DELUGE_CONFIG_DIR="${DELUGE_CONFIG_DIR:-/var/lib/deluged/config}"
DELUGE_SERVICE="${DELUGE_SERVICE:-deluged}"
EGG="${1:-${EGG:-}}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root (installs into $DELUGE_CONFIG_DIR and restarts $DELUGE_SERVICE)." >&2
    exit 1
fi
if [[ -z "$EGG" ]]; then
    EGG="$(ls -t "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"/dist/DiskGuard-*.egg 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$EGG" || ! -f "$EGG" ]]; then
    echo "DiskGuard egg not found; pass the path as the first argument." >&2
    exit 1
fi

PLUGIN_DIR="$DELUGE_CONFIG_DIR/plugins"
install -d -m 755 "$PLUGIN_DIR"
find "$PLUGIN_DIR" -maxdepth 1 -type f -name 'DiskGuard-*.egg' -delete
install -m 644 "$EGG" "$PLUGIN_DIR/"
systemctl restart "$DELUGE_SERVICE"
systemctl is-active --quiet "$DELUGE_SERVICE"
echo "Installed $(basename "$EGG") into $PLUGIN_DIR"
