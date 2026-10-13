#!/usr/bin/env bash
# OPTIONAL: install the read-only collector and camera helper on the Pi so they
# can also be run locally (e.g. from x500-status). The desktop app does NOT need
# this - by default it streams the scripts over SSH and installs nothing.
#
#   sudo bash install_collector.sh            # installs to /usr/local/bin
#   bash install_collector.sh --dry-run
set -euo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../src/x500fs/pi_side" && pwd)"
for pair in "x500fs_collector.py:x500fs-collector" "x500fs_camera.py:x500fs-camera"; do
  src="$SRC/${pair%%:*}"; dst="/usr/local/bin/${pair##*:}"
  if [ "$DRY" = 1 ]; then echo "(dry-run) install -m 0755 $src $dst"; else install -m 0755 "$src" "$dst"; echo "installed $dst"; fi
done
echo "Try: x500fs-collector --pretty | less"
echo "In the app you may then set Settings -> Pi -> collector mode = installed."
