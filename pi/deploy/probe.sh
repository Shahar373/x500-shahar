#!/usr/bin/env bash
# x500fs probe - READ-ONLY inventory of the Raspberry Pi.
#
# Run this on the Pi BEFORE installing anything, and send the output back
# (it contains no passwords; review it first - it shows hostnames, IPs and
# service names). It changes nothing: no sudo, no installs, no restarts.
#
#   bash probe.sh > probe.txt 2>&1
set -u

section() { printf '\n===== %s =====\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
run() { echo "\$ $*"; timeout 15 "$@" 2>&1 | head -n "${LINES_MAX:-60}"; echo; }

section "system"
run uname -a
run cat /etc/os-release
[ -r /etc/rpi-issue ] && run head -n 3 /etc/rpi-issue
run cat /proc/device-tree/model
echo
run getconf LONG_BIT
run uptime
run id

section "python"
run python3 --version

section "camera stack"
for t in rpicam-hello rpicam-vid libcamera-hello libcamera-vid ffmpeg mediamtx; do
  if have "$t"; then echo "$t: $(command -v "$t")"; else echo "$t: (not found)"; fi
done
have rpicam-hello && run rpicam-hello --version
have rpicam-hello && run rpicam-hello --list-cameras
have dpkg-query && run dpkg-query -W -f='${Package} ${Version}\n' rpicam-apps rpicam-apps-lite libcamera0.5 libcamera0.4 libcamera-ipa python3-picamera2 ffmpeg
echo "video devices:"; ls -l /dev/video* 2>/dev/null | head -n 30
echo "/dev/video11 (Pi 4 style HW H.264 encoder) present: $([ -e /dev/video11 ] && echo yes || echo no)"

section "power / thermal"
have vcgencmd && run vcgencmd get_throttled
have vcgencmd && run vcgencmd measure_temp
have vcgencmd && run vcgencmd pmic_read_adc

section "services (names only)"
have systemctl && systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null \
  | grep -Ei 'x500|mavlink|watchdog|black|box|log|camera|mediamtx|tailscale|modem|rpicam' | head -n 60
echo
for u in mavlink-router mediamtx tailscaled ModemManager watchdog; do
  have systemctl && echo "$u: $(systemctl is-active "$u" 2>/dev/null) / $(systemctl is-enabled "$u" 2>/dev/null)"
done
have systemctl && run systemctl show --property=RuntimeWatchdogUSec,RebootWatchdogUSec
ls -l /dev/watchdog* 2>/dev/null

section "existing x500 tools"
for t in x500-preflight x500-status; do
  if have "$t"; then echo "$t: $(command -v "$t")"; file "$(command -v "$t")" 2>/dev/null; else echo "$t: (not found in PATH)"; fi
done

section "mavlink-router config"
for f in /etc/mavlink-router/main.conf /etc/mavlink-router/config.d/*.conf; do
  [ -r "$f" ] && { echo "--- $f"; grep -v '^\s*#' "$f" | sed '/^\s*$/d'; }
done
have ss && run ss -H -l -n -t -u

section "network"
have ip && run ip -br addr
have ip && run ip route show default
have iw && for w in $(ls /sys/class/net | grep '^wl'); do run iw dev "$w" link; done
have tailscale && run tailscale status --self --peers=false
have mmcli && run mmcli -L
echo "USB devices (vid:pid product):"
for d in /sys/bus/usb/devices/*; do
  [ -r "$d/idVendor" ] || continue
  printf '  %s:%s %s %s\n' "$(cat "$d/idVendor")" "$(cat "$d/idProduct")" "$(cat "$d/manufacturer" 2>/dev/null)" "$(cat "$d/product" 2>/dev/null)"
done
for n in /sys/class/net/*; do
  name=$(basename "$n"); drv=$(basename "$(readlink -f "$n/device/driver" 2>/dev/null)" 2>/dev/null)
  echo "  iface $name driver=${drv:-?}"
done

section "ssh host key fingerprints (compare these when the app asks)"
for k in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$k"; done

section "done"
echo "Nothing was changed on this system."
