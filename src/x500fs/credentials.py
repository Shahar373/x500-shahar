"""Credential storage in the OS credential store (Windows Credential Manager).

Nothing secret is ever written to settings.json, reports or logs. If no
secure backend is available, saving fails loudly - there is no plaintext
fallback.
"""

from __future__ import annotations

import logging
import sys

from . import APP_ID

log = logging.getLogger(__name__)
_initialized = False


class SecretStoreError(RuntimeError):
    pass


def _keyring():
    global _initialized
    import keyring

    if not _initialized:
        _initialized = True
        if sys.platform == "win32":
            # Pin the backend explicitly; entry-point discovery is fragile in
            # frozen (PyInstaller) builds.
            try:
                from keyring.backends.Windows import WinVaultKeyring

                keyring.set_keyring(WinVaultKeyring())
            except Exception as e:  # noqa: BLE001
                log.error("Windows credential store unavailable: %s", e)
    return keyring


def _key(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def password_id(user: str, host: str, port: int) -> str:
    return _key("ssh-password", f"{user}@{host}:{port}")


def passphrase_id(key_path: str) -> str:
    return _key("ssh-key-passphrase", key_path)


def get(ident: str) -> str | None:
    try:
        return _keyring().get_password(APP_ID, ident)
    except Exception as e:  # noqa: BLE001
        log.warning("credential store read failed: %s", type(e).__name__)
        return None


def set(ident: str, secret: str) -> None:  # noqa: A001 - mirrors keyring API
    try:
        kr = _keyring()
        backend = kr.get_keyring()
        if "fail" in type(backend).__module__ or type(backend).__name__ in ("PlaintextKeyring",):
            raise SecretStoreError(f"no secure credential store available ({type(backend).__name__})")
        kr.set_password(APP_ID, ident, secret)
    except SecretStoreError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SecretStoreError(f"could not save credential: {type(e).__name__}") from None


def delete(ident: str) -> None:
    try:
        _keyring().delete_password(APP_ID, ident)
    except Exception:  # noqa: BLE001
        pass


def backend_name() -> str:
    try:
        return type(_keyring().get_keyring()).__name__
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({type(e).__name__})"
