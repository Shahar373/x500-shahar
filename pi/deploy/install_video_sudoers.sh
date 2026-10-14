#!/bin/bash
# OPTIONAL, explicit: allow ONE user to start/stop ONLY the existing video units without a
# password, so the desktop app's "start/stop video" works with `sudo -n`.
#
#   sudo bash install_video_sudoers.sh <user>            # dry run: shows the rule, validates it
#   sudo bash install_video_sudoers.sh <user> --apply    # installs /etc/sudoers.d/x500-video
#   sudo bash install_video_sudoers.sh --remove          # removes it
#
# The rule permits exactly six commands (start|stop x500-video@high|normal|degraded.service)
# with fixed arguments. It does not allow restart, enable, other units, shells or wildcards.
set -euo pipefail
DST=/etc/sudoers.d/x500-video
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi
if [ "${1:-}" = "--remove" ]; then
    rm -f "$DST" && echo "removed $DST"
    exit 0
fi
USER_NAME="${1:-}"
APPLY=0; [ "${2:-}" = "--apply" ] && APPLY=1
if ! [[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Usage: sudo bash $0 <existing-user> [--apply]" >&2
    exit 1
fi
SYSTEMCTL=/usr/bin/systemctl
[ -x "$SYSTEMCTL" ] || { echo "$SYSTEMCTL not found" >&2; exit 1; }
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
{
    echo "# Installed by x500fs install_video_sudoers.sh on $(date -Iseconds)."
    echo "# Lets $USER_NAME start/stop only the existing X500 video units, nothing else."
    cmds=()
    for p in high normal degraded; do
        for a in start stop; do cmds+=("$SYSTEMCTL $a x500-video@$p.service"); done
    done
    printf '%s ALL=(root) NOPASSWD: ' "$USER_NAME"
    (IFS=','; echo "${cmds[*]}" | sed 's/,/, /g')
} > "$TMP"
echo "---- rule ----"
cat "$TMP"
echo "--------------"
visudo -cf "$TMP"
if [ "$APPLY" != 1 ]; then
    echo "Dry run (validated). Install with: sudo bash $0 $USER_NAME --apply"
    exit 0
fi
install -m 0440 -o root -g root "$TMP" "$DST"
visudo -c >/dev/null
echo "Installed $DST. Test (as $USER_NAME): sudo -n $SYSTEMCTL start x500-video@degraded.service"
