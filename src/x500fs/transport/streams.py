"""Byte streams for MAVLink: TCP, UDP listen, and TCP-over-SSH (direct-tcpip).

None of these open the Pixhawk serial port. They connect to an existing
mavlink-router endpoint, which multiplexes the autopilot link to every client
(Mission Planner included).
"""

from __future__ import annotations

import socket
from typing import Protocol


class ByteStream(Protocol):
    description: str

    def recv(self, n: int) -> bytes | None:
        """Bytes, or None on timeout. Raises ConnectionError when closed."""

    def send(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class TcpStream:
    def __init__(self, host: str, port: int, connect_timeout: float = 5.0, read_timeout: float = 0.2):
        self.description = f"tcp:{host}:{port}"
        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(read_timeout)

    def recv(self, n: int) -> bytes | None:
        try:
            data = self._sock.recv(n)
        except socket.timeout:
            return None
        except OSError as e:
            raise ConnectionError(str(e)) from None
        if not data:
            raise ConnectionError("connection closed by peer")
        return data

    def send(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as e:
            raise ConnectionError(str(e)) from None

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class UdpListenStream:
    """Listen for a mavlink-router UDP *client* endpoint that sends to this PC."""

    def __init__(self, port: int, bind: str = "0.0.0.0", read_timeout: float = 0.2):
        self.description = f"udp-listen:{bind}:{port}"
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((bind, port))
        self._sock.settimeout(read_timeout)
        self._peer = None

    def recv(self, n: int) -> bytes | None:
        try:
            data, addr = self._sock.recvfrom(65535)
        except socket.timeout:
            return None
        except OSError as e:
            raise ConnectionError(str(e)) from None
        self._peer = addr
        return data

    def send(self, data: bytes) -> None:
        if self._peer is None:
            return  # nobody to answer yet
        try:
            self._sock.sendto(data, self._peer)
        except OSError as e:
            raise ConnectionError(str(e)) from None

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class SshChannelStream:
    """TCP to host:port *on the Pi* through the SSH connection (no exposed port)."""

    def __init__(self, session, host: str, port: int, read_timeout: float = 0.2):
        self.description = f"ssh:{session.host} → {host}:{port}"
        self._chan = session.open_direct_tcpip(host, port)
        self._chan.settimeout(read_timeout)

    def recv(self, n: int) -> bytes | None:
        try:
            data = self._chan.recv(n)
        except socket.timeout:
            return None
        except (OSError, EOFError) as e:
            raise ConnectionError(str(e)) from None
        if not data:
            raise ConnectionError("SSH channel closed")
        return data

    def send(self, data: bytes) -> None:
        try:
            self._chan.sendall(data)
        except (OSError, EOFError) as e:
            raise ConnectionError(str(e)) from None

    def close(self) -> None:
        try:
            self._chan.close()
        except Exception:  # noqa: BLE001
            pass
