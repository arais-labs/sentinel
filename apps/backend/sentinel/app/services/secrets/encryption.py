from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_ENVELOPE_PREFIX = "sentinel:v1:"


class SecretDecryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.data_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_ENVELOPE_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    if not value.startswith(_ENVELOPE_PREFIX):
        raise SecretDecryptionError("Unrecognized secret envelope.")
    token = value[len(_ENVELOPE_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise SecretDecryptionError("Secret could not be decrypted.") from exc
