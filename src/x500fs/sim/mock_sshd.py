"""A small in-process SSH server that stands in for the Pi (self-test and development).

Unlike the test helper ``tests/fake_sshd.py`` it never runs a shell: ``exec``
requests are answered by handlers (the collector command gets a simulated
collector document), so it also works inside the packaged Windows app where no
Python or POSIX shell exists. ``direct-tcpip`` channels are forwarded, which is
how the MAVLink tunnel to a mock autopilot is exercised.

The host key is Ed25519 by default, like the Pi's preferred key, so the client's
real signature verification path runs.
"""

from __future__ import annotations

import io
import json
import re
import select
import socket
import threading
import time

import paramiko


def generate_host_key(kind: str = "ed25519") -> paramiko.PKey:
    if kind == "rsa":
        return paramiko.RSAKey.generate(2048)
    if kind == "ecdsa":
        return paramiko.ECDSAKey.generate()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    return paramiko.Ed25519Key.from_private_key(io.StringIO(pem.decode()))


def collector_handler(cmd: str, stdin: bytes) -> tuple[int, bytes, bytes]:
    from .simulator import make_collector_doc

    m = re.search(r"--only\s+(\S+)", cmd)
    doc = make_collector_doc("healthy", "wifi", 1200.0, "mock-boot")
    if m:
        wanted = set(m.group(1).strip("'\"").split(","))
        doc["sections"] = {k: v for k, v in doc["sections"].items() if k in wanted}
    return 0, (json.dumps(doc) + "\n").encode(), b""


class _Server(paramiko.ServerInterface):
    def __init__(self, owner: "MockSshd"):
        self.owner = owner

    def check_auth_password(self, username, password):
        ok = username == self.owner.user and password == self.owner.password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.owner.forward_requests.append(destination)
        self.owner._dest[chanid] = self.owner.forward_map.get(tuple(destination), tuple(destination))
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        cmd = command.decode() if isinstance(command, bytes) else command
        threading.Thread(target=self.owner._exec, args=(channel, cmd), daemon=True).start()
        return True


class MockSshd:
    def __init__(self, user: str = "pilot", password: str = "selftest", host_key: paramiko.PKey | None = None,
                 forward_map: dict | None = None):
        self.user, self.password = user, password
        self.host_key = host_key or generate_host_key()
        self.forward_map = {tuple(k): tuple(v) for k, v in (forward_map or {}).items()}
        self.forward_requests: list = []
        self.commands: list[str] = []
        self.handlers: list = [(re.compile(r".*python.*"), collector_handler)]
        self._dest: dict[int, tuple] = {}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.3)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._transports: list[paramiko.Transport] = []

    def start(self) -> "MockSshd":
        threading.Thread(target=self._accept, name="mock-sshd", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._transports:
            t.close()
        self._sock.close()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.host_key)
            try:
                t.start_server(server=_Server(self))
            except Exception:  # noqa: BLE001
                continue
            self._transports.append(t)
            threading.Thread(target=self._channels, args=(t,), daemon=True).start()

    def _channels(self, t: paramiko.Transport) -> None:
        while t.is_active() and not self._stop.is_set():
            chan = t.accept(0.5)
            if chan is None:
                continue
            dest = self._dest.pop(chan.get_id(), None)
            if dest is not None:
                threading.Thread(target=self._forward, args=(chan, dest), daemon=True).start()

    def _exec(self, channel: paramiko.Channel, cmd: str) -> None:
        self.commands.append(cmd)
        stdin = bytearray()
        deadline = time.monotonic() + 10
        while not channel.eof_received and not channel.closed and time.monotonic() < deadline:
            if channel.recv_ready():
                stdin += channel.recv(65536)
            else:
                select.select([channel], [], [], 0.05)
        code, out, err = 127, b"", f"mock sshd: no handler for {cmd!r}\n".encode()
        for rx, fn in self.handlers:
            if rx.fullmatch(cmd):
                code, out, err = fn(cmd, bytes(stdin))
                break
        try:
            channel.sendall(out)
            channel.sendall_stderr(err)
            channel.send_exit_status(code)
            channel.close()
        except (OSError, EOFError):
            pass

    def _forward(self, chan: paramiko.Channel, dest) -> None:
        try:
            s = socket.create_connection(dest, timeout=5)
        except OSError:
            chan.close()
            return
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([s, chan], [], [], 0.5)
                if s in r:
                    data = s.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    s.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            s.close()
            try:
                chan.close()
            except (OSError, EOFError):
                pass
