#!/bin/bash
# Explicit deployment of the corrected X500 scripts and the shared x500fs library.
#
#   sudo bash deploy_x500.sh            # DRY RUN: shows what would change (diffs), changes nothing
#   sudo bash deploy_x500.sh --apply    # backs up every replaced file, then installs
#
# Run it from an unpacked bundle (python tools/make_pi_bundle.py) or from a checkout of the repo.
# The desktop app never runs this. Nothing is restarted automatically; the script prints the
# explicit commands. The video pipeline (x500-video, x500-video-run, x500-video@.service),
# mavlink-router configuration and the watchdog configuration are NOT touched.
set -euo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$HERE/x500/bin" ]; then PI_SRC="$HERE/x500"; LIB_SRC="$HERE/lib"          # bundle layout
else PI_SRC="$HERE/../x500"; LIB_SRC="$HERE/../../src"; fi                       # repo layout
[ -d "$PI_SRC/bin" ] && [ -d "$LIB_SRC/x500fs" ] || { echo "cannot find x500/bin and x500fs sources" >&2; exit 1; }

# X500FS_DEPLOY_ROOT: install under another root (used by the automated tests). Empty = real system.
R="${X500FS_DEPLOY_ROOT:-}"
CONF="$R/etc/x500/x500.conf"
LIB_DST="$R/opt/x500fs/lib"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$R/var/backups/x500fs/$STAMP"
OWN=(-o root -g root)

if [ -n "$R" ]; then
    OWN=()
elif [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo (installs into /usr/local/bin, /opt and /etc)." >&2; exit 1
fi
[ "$APPLY" = 1 ] && echo "== APPLY mode ==" || echo "== DRY RUN (nothing will be changed; add --apply to install) =="

# ---------------------------------------------------------------- configuration
if [ ! -f "$CONF" ]; then
    echo
    echo "No $CONF yet."
    if [ "$APPLY" = 1 ]; then
        install -D -m 0644 "${OWN[@]}" "$PI_SRC/etc/x500/x500.conf.example" "$CONF"
        echo "Installed the example to $CONF. Edit it (GROUND_IP, ZTE_GATEWAY, X500_VENV_PYTHON, ...),"
        echo "then run this script again with --apply."
    else
        echo "With --apply the example would be copied there first; you then edit it and run again."
    fi
    exit 0
fi
if grep -nE '^[^#]*(NOT_CONFIGURED|PI_USER)' "$CONF"; then
    echo "Refusing: $CONF still contains reference markers from the source snapshot (lines above)." >&2
    exit 1
fi
read -r owner mode < <(stat -c '%U %a' "$CONF")
[ -n "$R" ] && owner=root
if [ "$owner" != "root" ] || [ $(( 8#$mode & 8#022 )) -ne 0 ]; then
    echo "Refusing: $CONF must be owned by root and not group/world-writable (it is sourced by root services)." >&2
    echo "Fix: sudo chown root:root $CONF && sudo chmod 644 $CONF" >&2
    exit 1
fi

# ---------------------------------------------------------------- plan
declare -a PLAN=()
add() { PLAN+=("$1|$2|$3"); }   # src|dst|mode
for f in x500-preflight x500-health x500-net x500-blackbox-status x500-diag x500-fc-detect x500-status; do
    add "$PI_SRC/bin/$f" "$R/usr/local/bin/$f" 0755
done
for f in x500-blackbox x500-net-monitor x500-status; do
    add "$PI_SRC/bin/$f" "$R/opt/x500/bin/$f" 0755
done
LIB_FILES=(x500fs/__init__.py x500fs/pi_side/__init__.py x500fs/pi_side/x500fs_collector.py
           x500fs/pi_side/x500fs_diag.py x500fs/transport/__init__.py x500fs/transport/mav_ingest.py
           x500fs/transport/mavlink_client.py x500fs/transport/streams.py)
while IFS= read -r rel; do LIB_FILES+=("$rel"); done < <(cd "$LIB_SRC" && find x500fs/core x500fs/pi_tools \
    -name '*.py' -not -path '*/__pycache__/*' | sort; cd "$LIB_SRC" && ls x500fs/profiles/*.json)
for rel in "${LIB_FILES[@]}"; do
    [ -f "$LIB_SRC/$rel" ] || { echo "missing library file: $rel" >&2; exit 1; }
    add "$LIB_SRC/$rel" "$LIB_DST/$rel" 0644
done

changed=0
echo
for item in "${PLAN[@]}"; do
    IFS='|' read -r src dst mode <<<"$item"
    if [ ! -e "$dst" ]; then
        echo "NEW      $dst"
        changed=$((changed + 1))
    elif ! cmp -s "$src" "$dst"; then
        echo "REPLACE  $dst  (backup -> $BACKUP$dst)"
        case "$dst" in */usr/local/bin/*|*/opt/x500/bin/*) diff -u "$dst" "$src" | head -40 || true; echo "  ...";; esac
        changed=$((changed + 1))
    fi
done
echo
echo "$changed file(s) would change."
if [ "$APPLY" != 1 ]; then
    echo "Dry run only. Review the diffs above, then: sudo bash $0 --apply"
    exit 0
fi

# ---------------------------------------------------------------- apply
for item in "${PLAN[@]}"; do
    IFS='|' read -r src dst mode <<<"$item"
    if [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
        install -D -m 0644 "$dst" "$BACKUP$dst"
        chmod --reference="$dst" "$BACKUP$dst"
    fi
    if [ ! -e "$dst" ] || ! cmp -s "$src" "$dst"; then
        install -D -m "$mode" "${OWN[@]}" "$src" "$dst"
    fi
done
echo "Installed. Backups (if any): $BACKUP"
echo
echo "Nothing was restarted. The black-box logger and network monitor keep running the OLD"
echo "scripts until you restart them explicitly (do this on the bench, not during a flight):"
echo "  sudo systemctl restart x500-blackbox x500-net-monitor"
echo
echo "Try:  x500-health ; x500-blackbox-status ; x500-preflight bench"
echo "Rollback: sudo cp -a $BACKUP/. /   (then restart the services above)"
