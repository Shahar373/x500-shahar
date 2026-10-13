#!/usr/bin/env bash
# Explicit, reviewable installation of MediaMTX for the X500 camera stream.
#
# This is a DEPLOYMENT step, separate from monitoring. The desktop app never
# runs it. Read it before running. It:
#   1. downloads the official MediaMTX release for this CPU from GitHub,
#   2. verifies its SHA256 against the release's checksums.sha256,
#   3. installs /usr/local/bin/mediamtx and /etc/mediamtx/mediamtx.yml
#      (an existing config is NOT overwritten - the new one is saved as .new),
#   4. creates a 'mediamtx' system user in the 'video' group and a systemd unit,
#   5. enables/starts the service ONLY if you pass --enable.
#
# Usage (on the Pi):
#   bash install_mediamtx.sh --dry-run            # show what would happen
#   sudo bash install_mediamtx.sh                  # install, do not start
#   sudo bash install_mediamtx.sh --enable         # install and start now + at boot
#   sudo bash install_mediamtx.sh --version v1.21.2
#
# Requires: curl or wget, tar, sha256sum. Internet access on the Pi.
set -euo pipefail

VERSION="v1.21.2"   # pin; check https://github.com/bluenviron/mediamtx/releases before changing
ENABLE=0
DRY=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --enable) ENABLE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64 ;;
  armv7l) ARCH=armv7 ;;
  armv6l) ARCH=armv6 ;;
  x86_64) ARCH=amd64 ;;
  *) echo "unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac

ASSET="mediamtx_${VERSION}_linux_${ARCH}.tar.gz"
BASE="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}"

say() { printf '>> %s\n' "$*"; }
do_() { if [ "$DRY" = 1 ]; then echo "   (dry-run) $*"; else "$@"; fi; }

say "MediaMTX ${VERSION} for linux/${ARCH}"
say "asset: ${BASE}/${ASSET}"
if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
  echo "run with sudo (or use --dry-run)" >&2; exit 1
fi

fetch() {
  if command -v curl >/dev/null; then curl -fsSL --retry 3 -o "$2" "$1"; else wget -q -O "$2" "$1"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ "$DRY" = 0 ]; then
  say "downloading"
  fetch "${BASE}/${ASSET}" "$TMP/${ASSET}"
  fetch "${BASE}/checksums.sha256" "$TMP/checksums.sha256"
  say "verifying SHA256"
  ( cd "$TMP" && grep " ${ASSET}\$" checksums.sha256 | sha256sum -c - ) || { echo "CHECKSUM MISMATCH - aborting" >&2; exit 1; }
  tar -xzf "$TMP/${ASSET}" -C "$TMP" mediamtx
fi

say "installing binary to /usr/local/bin/mediamtx"
do_ install -m 0755 "$TMP/mediamtx" /usr/local/bin/mediamtx

say "installing config"
do_ install -d -m 0755 /etc/mediamtx
if [ -e /etc/mediamtx/mediamtx.yml ]; then
  say "existing /etc/mediamtx/mediamtx.yml kept; new one written to /etc/mediamtx/mediamtx.yml.new"
  do_ install -m 0644 "$HERE/mediamtx-x500.yml" /etc/mediamtx/mediamtx.yml.new
else
  do_ install -m 0644 "$HERE/mediamtx-x500.yml" /etc/mediamtx/mediamtx.yml
fi

if ! id mediamtx >/dev/null 2>&1; then
  say "creating system user 'mediamtx' (member of group video for camera access)"
  do_ useradd --system --no-create-home --shell /usr/sbin/nologin --groups video mediamtx
fi

say "installing systemd unit /etc/systemd/system/mediamtx.service"
do_ install -m 0644 "$HERE/mediamtx.service" /etc/systemd/system/mediamtx.service
do_ systemctl daemon-reload

if [ "$ENABLE" = 1 ]; then
  say "enabling and starting mediamtx"
  do_ systemctl enable --now mediamtx
  sleep 2
  do_ systemctl --no-pager status mediamtx || true
else
  say "not started. To start: sudo systemctl enable --now mediamtx"
fi

say "done. In the app: Settings -> Pi -> 'MediaMTX service' = mediamtx; video path = x500cam."
say "Check the log after starting: journalctl -u mediamtx -n 50 --no-pager"
