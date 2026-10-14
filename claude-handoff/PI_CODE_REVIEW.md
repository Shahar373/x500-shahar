# Review of the uploaded Pi source snapshot

This review is grounded in source files from `x500-pi-source-20261014-101501.tar.gz`. It describes code behavior, not current hardware state. No script or service was executed.

## Existing implementation to reuse

| Component | Source path within `pi-reference/current` | Observed implementation |
|---|---|---|
| Preflight | `usr/local/bin/x500-preflight` | Bash bench/flight checks; three inline pymavlink probes; PASS/WARN/FAIL summary |
| Status | `opt/x500/bin/x500-status` | Pi, network, camera and short vehicle telemetry snapshot, including attitude, position, battery and EKF |
| Pi health | `usr/local/bin/x500-health` | Human output plus `--key` and `--machine`; temperature, throttle flags, RAM and storage |
| Health monitor | `opt/x500/bin/x500-health-monitor` | Journald messages on state changes; sleeps 15 seconds |
| Network | `usr/local/bin/x500-net`, `opt/x500/bin/x500-net-monitor` | Interface, route, ping, LTE gateway, Tailscale and ground reachability observations |
| Black box | `opt/x500/bin/x500-blackbox` | Daily CSV; samples observations then sleeps 10 seconds |
| Black-box status | `usr/local/bin/x500-blackbox-status` | Service, CSV file and age inspection |
| Diagnostics | `usr/local/bin/x500-diag` | Generates a report under `/var/log/x500/diag`; includes network, USB, camera, service and kernel information |
| FC discovery | `usr/local/bin/x500-fc-detect` | Serial by-id, USB candidate, permissions and router diagnostics |
| Video launcher | `usr/local/bin/x500-video` | Starts/stops systemd video profiles using sudo; status and logs |
| Video pipeline | `opt/x500/bin/x500-video-run` | Raw YUV420 camera frames → ffmpeg/libx264 → MPEG-TS over SRT |
| Router | `etc/mavlink-router/main.conf` | TCP 5760; USB by-id serial at 115200; UDP ground endpoint 14550 |
| Services | `etc/systemd/system/x500-*.service` | Health, network, black-box and video services |
| Watchdog | `etc/systemd/system.conf.d/60-x500-watchdog.conf` | Configured RuntimeWatchdogSec=15s and RebootWatchdogSec=2min |
| Journald | `etc/systemd/journald.conf.d/60-x500.conf` | Persistent/runtime size limits and 7-day journal retention |

The archive stores `/usr/local/bin/x500-status` as a hard-link entry referring to the canonical `/opt/x500/bin/x500-status` content. The handoff contains an equivalent regular-file copy for portability.

## Video is already implemented

The Pi resolves its own Tailscale IPv4 address and listens for an SRT caller on port 5600. This is NOT a Pi-to-Windows push to the `GROUND_IP` in `video.conf`: that variable is currently sourced but unused by the runner. The receiver on Windows must connect to the Pi listener.

| Profile | Dimensions | Requested FPS | Target bitrate | Configured SRT latency |
|---|---|---|---|---|
| high | 640×480 | 60 | 1200 kbps | 120000 µs |
| normal | 640×480 | 30 | 650 kbps | 180000 µs |
| degraded | 480×360 | 20 | 350 kbps | 300000 µs |

These are requested settings, not measured performance or end-to-end delay. The encoder is explicitly software `libx264` with ultrafast/zerolatency settings. The service restarts after failure. Camera start currently uses privileged service commands.

## Required corrections for the new application

1. **Pre-arm can produce a false clear result.** The preflight script marks any ACK for `MAV_CMD_RUN_PREARM_CHECKS` as acknowledged without examining `COMMAND_ACK.result`. If no `PreArm:`/`Arm:` text appears in its six-second window, it prints `PREARM_CLEAR=1`. A rejected/unsupported command, disabled checks or missing messages must not become PASS. Verify documented firmware behavior, check ACK result and source, inspect fresh enabled/health pre-arm bits and arming configuration, and retain UNKNOWN when evidence is insufficient.

2. **Pi health has healthy-looking missing-data defaults.** Failed temperature and throttling reads become `temp=0` and `throttled=0x0`; invalid throttle text becomes zero. Collect unavailable/error as explicit data rather than normal. Distinguish current bit flags from historical flags; the current preflight fails on any nonzero throttle value while the health script leaves historical events under an overall OK state.

3. **Some MAVLink probes do not filter all messages by vehicle source.** Status filters telemetry by vehicle system ID, but preflight telemetry, sensor and pre-arm message loops lack equivalent filtering after selecting a vehicle heartbeat. Use a single persistent connection and explicitly match the selected vehicle and appropriate component. Mission Planner/GCS and unrelated devices must not satisfy checks.

4. **Battery validity is not battery readiness.** The scripts accept voltage >=5 V as valid battery telemetry. That is not a charge or suitability criterion for the actual flight battery. Use configured chemistry/cell count, calibrated monitor settings and explicit unknown sentinel handling. Do not infer charge from a generic voltage gate.

5. **GPS/EKF checks are incomplete for flight readiness.** Current preflight mainly checks GPS fix >=3 and the EKF attitude bit. Preserve raw flags and assess navigation requirements from an explicit profile; include home and position validity when required. GPS sentinel values, including satellites=255, must remain unknown.

6. **Video progress is not proof of a live image in Windows.** Several scripts label a progress-file update within three seconds as CONNECTED. Treat this as encoder/transport-side evidence, and separately verify recent decoded frames in the desktop player. Configured SRT latency is not measured camera-to-screen delay.

7. **Local configuration is hardcoded.** Ground address, gateway, interfaces, Python virtual-environment path and exact serial ID appear in the source. Parameterize them. The cleaned snapshot uses nonfunctional reference markers; do not deploy those markers.

8. **Repeated preflight execution is unsuitable for fast polling.** It creates several connections, scans the camera and runs network commands serially. Use slow cached system probes plus one live telemetry collector. Bound subprocesses and connection lifetimes, and distinguish snapshot timestamps from message freshness.

9. **Reports can contain private operational information.** The diagnostic script writes a local report and includes Tailscale status, network profiles and journals. It is not a strictly read-only stdout command. Do not run it automatically on every refresh; offer explicit diagnostics export with redaction.

10. **Black-box retention is not verified.** The status script prints “Retention: 14 days,” but the uploaded black-box logger does not delete old CSVs. No retention timer/cron job was included. Sampling period is probe time plus a 10-second sleep, not a guaranteed exact 10-second cadence.

## Snapshot limits

The archive does not contain an ArduPilot parameter export, active runtime outputs, installed package versions, the actual Python virtual environment, logs, the mavlink-router systemd unit or sudoers policy. Missing from this snapshot does not mean absent on the Pi. Do not invent their contents. Watchdog configuration is not proof that the hardware watchdog is active.

Historical backups and SITL configuration are separated from current files. No flight, radio or power-supply health can be certified from this source snapshot.
