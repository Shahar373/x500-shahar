"""A tiny paramiko-based SSH server for integration tests.

Supports public-key/password auth, ``exec`` (runs the command locally with
stdin forwarded - this is how the real collector script gets exercised) and
``direct-tcpip`` forwarding (used for the MAVLink tunnel and RTSP).
"""

from __future__ import annotations

import select
import socket
import subprocess
import threading

import paramiko


class _Server(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner
        self.exec_cmds: dict[int, str] = {}

    def check_auth_password(self, username, password):
        if username == self.owner.user and password == self.owner.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        if username == self.owner.user and self.owner.client_key is not None \
                and key.asbytes() == self.owner.client_key.asbytes():
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "publickey,password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        if self.owner.refuse_forwarding:
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        self.owner.forward_requests.append(destination)
        self.owner._dest_by_chanid[chanid] = destination
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        self.exec_cmds[channel.get_id()] = command.decode() if isinstance(command, bytes) else command
        threading.Thread(target=self.owner._run_exec, args=(channel, self.exec_cmds[channel.get_id()]),
                         daemon=True).start()
        return True


class FakeSshd:
    def __init__(self, host_key: paramiko.PKey | None = None, user: str = "pi", password: str = "pw",
                 client_key: paramiko.PKey | None = None, env: dict | None = None):
        self.host_key = host_key or paramiko.RSAKey.generate(2048)
        self.refuse_forwarding = False
        self._dest_by_chanid: dict[int, tuple] = {}
        self.user, self.password, self.client_key = user, password, client_key
        self.env = env or {}
        self.forward_requests: list = []
        self.commands: list[str] = []
        # (compiled regex, fn(match) -> (exit_code, stdout_bytes, stderr_bytes)) answered without a shell
        self.handlers: list = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.3)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._transports: list[paramiko.Transport] = []

    def start(self):
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        for t in self._transports:
            t.close()
        self._sock.close()

    def kill_connections(self):
        for t in self._transports:
            t.close()
        self._transports.clear()

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.host_key)
            server = _Server(self)
            try:
                t.start_server(server=server)
            except Exception:  # noqa: BLE001
                continue
            self._transports.append(t)
            threading.Thread(target=self._channels, args=(t,), daemon=True).start()

    def _channels(self, t: paramiko.Transport):
        while t.is_active() and not self._stop.is_set():
            chan = t.accept(0.5)
            if chan is None:
                continue
            dest = self._dest_by_chanid.pop(chan.get_id(), None)
            if dest is not None:
                threading.Thread(target=self._forward, args=(chan, dest), daemon=True).start()
            # session channels are handled by check_channel_exec_request

    def _run_exec(self, channel: paramiko.Channel, command: str):
        self.commands.append(command)
        for rx, fn in self.handlers:
            m = rx.fullmatch(command)
            if m:
                # wait for the client's stdin EOF first (as the shell path does), so the
                # exec request is fully acknowledged before the channel closes
                while not channel.eof_received and not channel.closed:
                    if channel.recv_ready():
                        channel.recv(65536)
                    else:
                        select.select([channel], [], [], 0.05)
                code, out, err = fn(m)
                try:
                    channel.sendall(out)
                    channel.sendall_stderr(err)
                    channel.send_exit_status(code)
                    channel.close()
                except (OSError, EOFError):
                    pass
                return
        env = dict(__import__("os").environ)
        env.update(self.env)
        env["SSH_CONNECTION"] = "127.0.0.1 50000 127.0.0.1 22"
        p = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
        stdin = bytearray()
        while True:
            if channel.recv_ready():
                data = channel.recv(65536)
                if not data:
                    break
                stdin += data
            elif channel.eof_received:
                break
            else:
                select.select([channel], [], [], 0.05)
        out, err = p.communicate(bytes(stdin), timeout=60)
        try:
            channel.sendall(out)
            channel.sendall_stderr(err)
            channel.send_exit_status(p.returncode)
            channel.close()
        except (OSError, EOFError):
            pass  # client went away (tests that kill connections)

    def _forward(self, chan: paramiko.Channel, dest):
        try:
            s = socket.create_connection(dest, timeout=5)
        except OSError:
            chan.close()
            return
        try:
            while True:
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
