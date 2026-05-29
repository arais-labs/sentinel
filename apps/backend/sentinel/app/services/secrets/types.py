from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.services.secrets.encryption import SecretDecryptionError, decrypt_secret, encrypt_secret


@dataclass(frozen=True, slots=True)
class InvalidSecretValue:
    reason: str

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        return ""

    def strip(self) -> str:
        return ""


def is_invalid_secret(value: object) -> bool:
    return isinstance(value, InvalidSecretValue)


class EncryptedText(TypeDecorator):
    """Text column that encrypts on write and decrypts on read."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt_secret(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        try:
            return decrypt_secret(value)
        except SecretDecryptionError as exc:
            return InvalidSecretValue(str(exc))  # type: ignore[return-value]
