# Build X500 Flight Station using the existing Pi implementation

Work in https://github.com/Shahar373/x500-shahar.

Build a working Windows desktop application and Setup.exe installer for preflight checks, continuous telemetry monitoring and live camera viewing for my Holybro X500 V2 PNP drone. Implement code, meaningful tests, documentation and packaging. Do not stop at a plan or a mockup.

## Start with the supplied source

Read `README.md`, `PI_CODE_REVIEW.md`, `SNAPSHOT_INFO.json` and the files under `pi-reference/current/` before designing the integration. `pi-reference/history/` is historical context, not the active implementation. Inspect any code already present in this repository and preserve useful work.

These files are a source snapshot of the Pi on 2026-10-14. They are not runtime evidence. Local network addresses, the account-specific home path and exact USB serial identifier have been sanitized. Strings ending in `NOT_CONFIGURED`, `PI_USER` in home paths and `DEVICE_SERIAL_NOT_CONFIGURED` are reference markers, not deployment values. Do not deploy the snapshot as-is or replace live files automatically.

Create a concise implementation plan and proceed through implementation. Reuse existing behavior where correct, but do not copy its false-PASS conditions. Implement a structured collector and a common evaluation layer instead of scraping terminal text as the permanent API.

## Hardware and operating context

- Holybro X500 V2 PNP, Pixhawk 6C running ArduPilot Copter.
- Holybro PM02 V3 power module and M10 GPS.
- Headless Raspberry Pi 5, hostname x500pi; previously Debian 13. Verify actual OS and installed capabilities during deployment.
- Raspberry Pi Camera Module v2 / IMX219.
- ZTE F30 USB LTE modem, Wi-Fi and Tailscale.
- No RC transmitter/receiver currently.
- Mission Planner remains the flight-control application; this app must coexist with it.
- Any later AI processing belongs on the ground laptop, not the Pi. This version does not require AI or cloud services.

## Existing interfaces to preserve

- `x500-preflight [bench|flight]`: existing checks and legacy exit codes 0/1/2 for pass/warnings/failure.
- `x500-status`: human system and telemetry status.
- `x500-health --key` and `--machine`: partial machine-readable health output.
- Health and network monitors plus black-box CSV logger and status command.
- `x500-fc-detect` and explicit diagnostics export via `x500-diag`.
- `mavlink-router`: local TCP `127.0.0.1:5760`; USB serial by-id at 115200; UDP ground endpoint 14550.
- Existing Python venv path is account-specific; make the path configurable.

Do not open the Pixhawk serial port directly from this application. Use the existing router through a verified endpoint or an SSH tunnel. Use one persistent MAVLink connection, explicitly select the vehicle and match incoming source IDs/components. Do not mistake Mission Planner heartbeats for a vehicle heartbeat.

## Scope boundaries

Implement observations, checks, manual confirmations, reports and video controls.

Do not implement arming, disarming, takeoff, landing, motor tests, mode changes, parameter writes or bypasses of pre-arm checks. Do not automatically reboot, restart critical services, install packages or change configuration when connecting or running a check.

Read-only telemetry and parameter requests are allowed. Supported non-actuating pre-arm diagnostics may run on an explicit preflight action while disarmed. Verify official firmware semantics and ACK results; never test readiness by attempting to arm.

Do not transmit GCS heartbeats by default. Document interactions with ArduPilot GCS failsafe, system/component IDs and Mission Planner so this app does not mask flight-control link loss.

Camera start/stop must be explicit. Initially require a fresh verified disarmed state before starting or reconfiguring it. Keep video controls separate from critical services.

## Architecture and interface

Use Python/PySide6, pymavlink, SSH and a small Pi collector with versioned JSON. Keep UI, transports, raw observations, policy evaluation, reports and video separate. Use worker threads/processes, deadlines, cancellation and bounded retries. Never block the UI.

Provide a clean Hebrew RTL interface, with technical identifiers readable. Actions: Connect, Run preflight, Start/stop video, Export report, Settings. Normal use after setup should take around 2–3 clicks.

Show bench/flight profile, automatic checks, manual checklist, live telemetry, video and recent warnings. Include roll/pitch/yaw, mode, armed state, GPS, battery and EKF where available; unavailable fields stay visibly unavailable.

Each check needs ID, PASS/WARN/FAIL/UNKNOWN/NOT_APPLICABLE, observed value, expected condition, explanation, source, timestamp and age. Use monotonic time for local freshness and account for remote clock differences. A critical unknown or stale observation prevents an overall checks-passed result. Separate automatic checks from manual confirmations and never imply the app certifies a safe flight.

Provide configurable thresholds/profiles, local settings and clearly marked simulation mode. Simulation cannot be mistaken for real telemetry or readiness.

## Check implementation and required fixes

Implement the existing Pi, watchdog, black-box, network, router, camera, GPS, battery, sensor, EKF and pre-arm observations, plus read-only expected-parameter comparison.

Mandatory fixes from the review:

1. Do not convert failed `vcgencmd` reads into temperature zero or clean throttle flags. Represent unavailable readings explicitly. Decode current and historical flags separately and expose actual evidence.
2. Validate `COMMAND_ACK.result` and source for pre-arm diagnostics. Silence during the old six-second STATUSTEXT window is not proof of passing. Check fresh documented pre-arm status bits and whether required checks are enabled. Handle rejected/unsupported/disabled checks and missing evidence explicitly. Reassemble chunked STATUSTEXT when applicable.
3. Filter all MAVLink data to the selected vehicle/appropriate component, including sensor and diagnostic responses. Validate units and sentinel values and close connections properly.
4. Battery voltage >=5 V only indicated plausible telemetry in the old scripts. Configure actual chemistry/cell count and monitor calibration; do not treat it as charge readiness. Unknown remaining/current/voltage values cannot pass a required check.
5. Assess GPS and EKF against navigation requirements, including home and position validity when required. Do not hardcode universal satellite/HDOP readiness thresholds or treat the attitude bit alone as navigation readiness.
6. Distinguish ICMP reachability, selected route, modem gateway, Internet, Tailscale and actual telemetry transport. LTE is required only by an explicitly selected profile. Measure application round trips and interruptions without calling a ping result a MAVLink quality measurement.
7. Check black-box service AND recent valid samples. Do not assume 14-day retention exists because a legacy status command prints it. Effective cadence includes probe duration plus sleep.
8. Distinguish watchdog configured versus active. Expose missing runtime proof instead of passing from a config file alone.
9. Use a bounded cadence with cached slow probes. Do not repeatedly run the full preflight script, camera enumeration and three inline telemetry probes at high frequency.
10. Make ground address, ZTE gateway, network interfaces, Python executable and FC identity configurable. Verify identities and routes rather than guessing.

Read-only configuration comparison should cover the selected failsafe/RTL/fence expectations. Missing expectations remain UNCONFIGURED/UNKNOWN, not verified. No parameter values are supplied in this snapshot.

Power software checks do not certify the converter, wiring or fast voltage transients. State that limitation in the relevant result.

## Existing video: integrate SRT first

The source already has:
- `x500-video-run`: `rpicam-vid --codec yuv420` piped to ffmpeg/libx264, ultrafast/zerolatency, MPEG-TS over SRT.
- Pi SRT listener on its dynamically resolved Tailscale IP, port 5600.
- `x500-video@.service` and `x500-video high|normal|degraded|stop|status|logs`.
- Profiles: high 640×480/60 FPS/1200 kbps; normal 640×480/30 FPS/650 kbps; degraded 480×360/20 FPS/350 kbps.
- Configured SRT latency parameters 120/180/300 ms respectively, NOT measured end-to-end delay.

Connect the Windows receiver as SRT caller to the Pi. `GROUND_IP` in the old video config is unused by the runner. Do not replace the pipeline with RTSP/MediaMTX unless a concrete compatibility issue justifies it; document any such change.

Evaluate a maintained SRT-capable Windows playback backend and verify codec/protocol support in the packaged build. Qt playback capability alone is not evidence of SRT support. Bundle or document required player/decoder components and licenses. Keep decoding off the UI thread with bounded buffering.

Start with the existing degraded profile or a separately configured lower-load option; retain existing profile names and do not claim requested FPS was measured. Software H.264 encoding is already in the source. Measure CPU/temperature/FPS on actual hardware before increasing load.

Separately display service state, encoder progress, transport state where measurable and freshness of decoded frames. Progress-file mtime alone must not label the desktop view live. Invalidate frozen images visibly, reconnect with bounded retries and keep video failure independent from telemetry. Require video only if the profile says so.

## Security, manual checks and reports

Use verified SSH host keys, configurable key-based access and appropriate Windows credential storage. Do not silently trust unknown hosts. Do not expose private details in Git, logs or reports.

Video launcher uses sudo: inspect actual permissions; do not assume passwordless access. Document an explicitly installed, narrowly scoped deployment option if needed, never broad unrestricted sudo.

Manual checklist: propellers/motors, frame/fasteners, battery mounting/connectors, payload/cables and takeoff area/environment. Reset confirmations for each session.

Export timestamped JSON and readable HTML reports with app/profile versions, available hardware/firmware identity, observations and ages, status, manual confirmations and missing evidence. Diagnostics export is explicit and redacted; the legacy diagnostic command writes files and includes private network/journal details.

## Packaging and meaningful verification

Use PyInstaller for Windows and Inno Setup or an equivalent maintained installer tool. Include dependency configuration, packaging specs, installer script and a Windows GitHub Actions workflow producing downloadable build artifacts. Do not publish a release automatically. Keep settings outside the installation directory and test launching without separately installed Python.

Test false-PASS prevention, missing/invalid throttle reads, pre-arm rejected/unsupported ACKs, absent status text, disabled checks, mixed MAVLink sources, stale/disconnected streams, battery/GPS sentinels, bench/flight policy, report redaction and simulation/live separation. Include mock Pi/MAVLink sources and a video test source for hardware-free development.

The snapshot omits runtime outputs, package versions, venv files, ArduPilot parameter export, router systemd unit, sudoers and retention jobs. Missing from the snapshot does not prove absent on the Pi. Ask one concise bundled question only for essential unavailable configuration, while continuing independent work with mocks. No assumptions that source presence proves a running service.

Implementation sequence: inspect → data model/mock UI → collectors/transports → checks/reports → SRT video → packaging → relevant tests. Continue until the implementation is concrete and usable; report hardware-dependent verification separately.

Deliver code and concise documentation for local development, explicit Pi deployment, first bench connection and installer build. Finish with what works, what was actually tested, unresolved limitations and exact commands. Do not claim real hardware, Windows installation or live video verification without having performed it.
