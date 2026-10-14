"""SSH session with strict host-key verification (paramiko).

* Unknown host keys are never trusted silently: :class:`HostKeyUnknown` is
  raised with the SHA256 fingerprint so the user can compare it with the one
  printed on the Pi (``ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub``).
* A changed host key raises :class:`HostKeyMismatch` and the connection is
  refused.
* Key-based authentication (file or agent) is preferred; a password is
  supported but never written to disk by this module.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import shlex
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko

log = logging.getLogger(__name__)


class SshError(Exception):
    pass


class HostKeyUnknown(SshError):
    def __init__(self, host: str, port: int, key: paramiko.PKey):
        self.host, self.port, self.key = host, port, key
        self.key_type = key.get_name()
        self.fingerprint = fingerprint_sha256(key)
        super().__init__(f"unknown host key for {host}:{port} ({self.key_type} {self.fingerprint})")


class HostKeyMismatch(SshError):
    def __init__(self, host: str, port: int, fingerprint: str):
        self.host, self.port, self.fingerprint = host, port, fingerprint
        super().__init__(f"HOST KEY CHANGED for {host}:{port} (now {fingerprint})")


class AuthFailed(SshError):
    pass


def fingerprint_sha256(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def host_entry(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


class KnownHosts:
    """App-owned known_hosts file plus (read-only) the user's ~/.ssh/known_hosts."""

    def __init__(self, app_file: Path, system_file: Path | None = None):
        self.app_file = Path(app_file)
        self.system_file = system_file if system_file is not None else Path.home() / ".ssh" / "known_hosts"

    def _load(self) -> paramiko.HostKeys:
        hk = paramiko.HostKeys()
        for f in (self.system_file, self.app_file):
            if f and f.is_file():
                try:
                    hk.load(str(f))
                except Exception as e:  # noqa: BLE001 - unreadable file = no keys from it, never a crash
                    log.warning("cannot read known_hosts %s: %s: %s", f, type(e).__name__, e)
        return hk

    def lookup(self, host: str, port: int) -> dict[str, paramiko.PKey]:
        entry = self._load().lookup(host_entry(host, port))
        return dict(entry) if entry else {}

    def add(self, host: str, port: int, key: paramiko.PKey) -> None:
        app = paramiko.HostKeys()
        if self.app_file.is_file():
            app.load(str(self.app_file))
        app.add(host_entry(host, port), key.get_name(), key)
        self.app_file.parent.mkdir(parents=True, exist_ok=True)
        app.save(str(self.app_file))

    def forget(self, host: str, port: int) -> bool:
        """Remove a host from the app file (system file is never modified)."""
        if not self.app_file.is_file():
            return False
        app = paramiko.HostKeys()
        app.load(str(self.app_file))
        name = host_entry(host, port)
        if name not in app:
            return False
        del app[name]
        app.save(str(self.app_file))
        return True


class _RaiseOnUnknown(paramiko.MissingHostKeyPolicy):
    def __init__(self, port: int):
        self.port = port

    def missing_host_key(self, client, hostname, key):  # noqa: D401
        host = hostname
        if host.startswith("[") and "]:" in host:
            host = host[1:host.index("]:")]
        raise HostKeyUnknown(host, self.port, key)


@dataclass
class SshAuth:
    method: str = "key"            # "key" | "agent" | "password"
    key_path: str = ""             # empty = default keys in ~/.ssh
    passphrase: str | None = None  # from the OS credential store, never from settings.json
    password: str | None = None    # from the OS credential store, never from settings.json


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    duration_s: float


class SshSession:
    def __init__(self, host: str, port: int, username: str, auth: SshAuth, known_hosts: KnownHosts,
                 connect_timeout: float = 10.0):
        self.host, self.port, self.username = host, port, username
        self.auth = auth
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout
        self.client: paramiko.SSHClient | None = None
        self.fingerprint: str | None = None
        self._ping_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    def connect(self) -> None:
        client = paramiko.SSHClient()
        # Load keys into the client's *host* key table (read-only use);
        # we never call client.save_host_keys().
        # A user's ~/.ssh/known_hosts written by Windows OpenSSH may contain lines (or an
        # encoding) paramiko cannot parse; that must never stop the connection attempt.
        if self.known_hosts.system_file and self.known_hosts.system_file.is_file():
            try:
                client.load_system_host_keys(str(self.known_hosts.system_file))
            except Exception as e:  # noqa: BLE001
                log.warning("cannot read %s: %s: %s", self.known_hosts.system_file, type(e).__name__, e)
        if self.known_hosts.app_file.is_file():
            try:
                client.load_host_keys(str(self.known_hosts.app_file))
            except Exception as e:  # noqa: BLE001
                log.warning("cannot read %s: %s: %s", self.known_hosts.app_file, type(e).__name__, e)
        client.set_missing_host_key_policy(_RaiseOnUnknown(self.port))
        kwargs = dict(
            hostname=self.host, port=self.port, username=self.username,
            timeout=self.connect_timeout, banner_timeout=self.connect_timeout,
            auth_timeout=self.connect_timeout, allow_agent=False, look_for_keys=False,
        )
        a = self.auth
        if a.method == "agent":
            kwargs["allow_agent"] = True
        elif a.method == "password":
            if not a.password:
                raise AuthFailed("no password stored for this host")
            kwargs["password"] = a.password
        else:
            if a.key_path:
                kwargs["key_filename"] = a.key_path
            else:
                kwargs["look_for_keys"] = True
            if a.passphrase:
                kwargs["passphrase"] = a.passphrase
            kwargs["allow_agent"] = True
        try:
            client.connect(**kwargs)
        except HostKeyUnknown:
            client.close()
            raise
        except paramiko.BadHostKeyException as e:
            client.close()
            raise HostKeyMismatch(self.host, self.port, fingerprint_sha256(e.key)) from None
        except paramiko.AuthenticationException as e:
            client.close()
            raise AuthFailed(f"authentication failed: {e}") from None
        except (paramiko.SSHException, OSError, EOFError) as e:
            client.close()
            raise SshError(f"{type(e).__name__}: {e}") from None
        transport = client.get_transport()
        if transport is None:
            client.close()
            raise SshError("no transport")
        transport.set_keepalive(5)
        self.fingerprint = fingerprint_sha256(transport.get_remote_server_key())
        self.client = client

    def close(self) -> None:
        if self.client:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
        self.client = None

    @property
    def transport(self) -> paramiko.Transport | None:
        return self.client.get_transport() if self.client else None

    def is_active(self) -> bool:
        t = self.transport
        return bool(t and t.is_active())

    # ------------------------------------------------------------------
    def exec(self, command: str, stdin: bytes | None = None, timeout: float = 30.0,
             cancel: threading.Event | None = None) -> ExecResult:
        t = self.transport
        if not t or not t.is_active():
            raise SshError("not connected")
        t0 = time.monotonic()
        deadline = t0 + timeout
        chan = t.open_session(timeout=min(timeout, 10.0))
        try:
            chan.settimeout(min(timeout, 10.0))
            chan.exec_command(command)
            if stdin:
                chan.sendall(stdin)
            chan.shutdown_write()
            out, err = bytearray(), bytearray()
            while True:
                if cancel is not None and cancel.is_set():
                    raise SshError("cancelled")
                busy = False
                if chan.recv_ready():
                    out += chan.recv(65536)
                    busy = True
                if chan.recv_stderr_ready():
                    err += chan.recv_stderr(65536)
                    busy = True
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(f"remote command timed out after {timeout:.0f}s")
                if len(out) > 8 * 1024 * 1024:
                    raise SshError("remote output too large")
                if not busy:
                    time.sleep(0.02)
            code = chan.recv_exit_status()
            return ExecResult(code, bytes(out), bytes(err), time.monotonic() - t0)
        finally:
            chan.close()

    def open_direct_tcpip(self, host: str, port: int, timeout: float = 10.0) -> paramiko.Channel:
        t = self.transport
        if not t or not t.is_active():
            raise SshError("not connected")
        try:
            return t.open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=timeout)
        except paramiko.ChannelException as e:
            raise SshError(f"SSH port forwarding to {host}:{port} refused ({e}). "
                           "Is the service listening there, and is AllowTcpForwarding enabled?") from None
        except (paramiko.SSHException, OSError, EOFError) as e:
            raise SshError(f"{type(e).__name__}: {e}") from None

    def ping(self, timeout: float = 3.0) -> float | None:
        """Application-level RTT: one SSH global request + reply, in ms.

        Returns None on timeout. Uses the same request OpenSSH's
        ServerAliveInterval uses; the server answers (with "failure"), which is
        all we need for timing.
        """
        t = self.transport
        if not t or not t.is_active():
            return None
        if self._ping_thread and self._ping_thread.is_alive():
            return None  # previous ping still outstanding: counts as a timeout
        result: dict[str, float] = {}

        def _do():
            t0 = time.perf_counter()
            try:
                t.global_request("keepalive@openssh.com", wait=True)
            except Exception:  # noqa: BLE001
                return
            if t.is_active():
                result["ms"] = (time.perf_counter() - t0) * 1000.0

        self._ping_thread = threading.Thread(target=_do, name="ssh-ping", daemon=True)
        self._ping_thread.start()
        self._ping_thread.join(timeout)
        return result.get("ms")


def quote(arg: str) -> str:
    return shlex.quote(arg)


class LocalForwarder:
    """127.0.0.1:<ephemeral> -> (SSH) -> remote host:port. Used for RTSP over SSH."""

    def __init__(self, get_session, remote_host: str, remote_port: int):
        self._get_session = get_session
        self.remote_host, self.remote_port = remote_host, remote_port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.port: int | None = None

    def start(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(4)
        s.settimeout(0.5)
        self._sock = s
        self.port = s.getsockname()[1]
        th = threading.Thread(target=self._accept_loop, name="ssh-forward", daemon=True)
        th.start()
        self._threads.append(th)
        return self.port

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            th = threading.Thread(target=self._pump, args=(client,), name="ssh-forward-pump", daemon=True)
            th.start()

    def _pump(self, client: socket.socket) -> None:
        import select

        try:
            session = self._get_session()
            chan = session.open_direct_tcpip(self.remote_host, self.remote_port)
        except Exception as e:  # noqa: BLE001
            log.warning("forward open failed: %s", e)
            client.close()
            return
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([client, chan], [], [], 0.5)
                if client in r:
                    data = client.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    client.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            chan.close()
            client.close()
