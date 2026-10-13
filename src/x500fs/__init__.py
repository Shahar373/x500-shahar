"""X500 Flight Station - read-only preflight checks and live video.

This package never sends arm/disarm/mode/motor commands, never writes
parameters and never transmits MAVLink HEARTBEAT messages. See
docs/mavlink-safety.md for the rationale.
"""

__version__ = "0.1.0"

APP_NAME = "X500 Flight Station"
APP_ID = "X500FlightStation"
