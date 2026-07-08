#!/usr/bin/env bash
# Install the Photo Organizer icons + desktop entry into the current user's
# XDG data dir so the app shows up with an icon in menus and the dock.
#
#   ./install-icons.sh            # install for the current user
#
set -euo pipefail
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PNG_DIR="$(cd "$SELF_DIR/../../resources/icons/png" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}"

for s in 16 24 32 48 64 128 256 512; do
    src="$PNG_DIR/icon-$s.png"
    [ -f "$src" ] || continue
    install -Dm644 "$src" "$DEST/icons/hicolor/${s}x${s}/apps/photo-organizer.png"
done

install -Dm644 "$SELF_DIR/photo-organizer.desktop" \
    "$DEST/applications/photo-organizer.desktop"

gtk-update-icon-cache -f -t "$DEST/icons/hicolor" >/dev/null 2>&1 || true
update-desktop-database "$DEST/applications" >/dev/null 2>&1 || true

echo "Installed Photo Organizer icon + .desktop entry under $DEST"
echo "Make sure the 'photo-organizer' binary is on your PATH (e.g. /usr/local/bin)."
