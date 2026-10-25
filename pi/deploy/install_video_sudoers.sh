#!/bin/bash
# OPTIONAL, explicit: allow ONE user to start/stop ONLY the existing video units without a
# password, so the desktop app's "start/stop video" works with `sudo -n`.
#
#   sudo bash install_video_sudoers.sh <user>                     # dry run: shows the rule, validates it
#   sudo bash install_video_sudoers.sh <user> --apply             # installs /etc/sudoers.d/x500-video
#   sudo bash install_video_sudoers.sh <user> --router [--apply]  # also: start mavlink-router when it is down
#   sudo bash install_video_sudoers.sh --remove                   # removes it
#
# The rule permits exactly six commands (start|stop x500-video@high|normal|degraded.service)
# with fixed arguments; --router adds two (reset-failed|start mavlink-router.service), which the
# app sends only when systemd reports the router failed/inactive. No restart, stop, enable,
# other units, shells or wildcards.
set -euo pipefail
DST=/etc/sudoers.d/x500-video
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi
if [ "${1:-}" = "--remove" ]; then
    rm -f "$DST" && echo "removed $DST"
    exit 0
fi
USER_NAME="${1:-}"
APPLY=0; ROUTER=0
for arg in "${@:2}"; do
    case "$arg" in
        --apply) APPLY=1 ;;
        --router) ROUTER=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done
if ! [[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "Usage: sudo bash $0 <existing-user> [--router] [--apply]" >&2
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
    if [ "$ROUTER" = 1 ]; then
        cmds+=("$SYSTEMCTL reset-failed mavlink-router.service" "$SYSTEMCTL start mavlink-router.service")
    fi
    printf '%s ALL=(root) NOPASSWD: ' "$USER_NAME"
    (IFS=','; echo "${cmds[*]}" | sed 's/,/, /g')
} > "$TMP"
echo "---- rule ----"
cat "$TMP"
echo "--------------"
visudo -cf "$TMP"
if [ "$APPLY" != 1 ]; then
    echo "Dry run (validated). Install with: sudo bash $0 $USER_NAME$([ "$ROUTER" = 1 ] && echo " --router") --apply"
    exit 0
fi
install -m 0440 -o root -g root "$TMP" "$DST"
visudo -c >/dev/null
echo "Installed $DST. Test (as $USER_NAME): sudo -n $SYSTEMCTL start x500-video@degraded.service"
