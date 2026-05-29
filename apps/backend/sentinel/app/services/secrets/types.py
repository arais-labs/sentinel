from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.services.secrets.encryption import decrypt_secret, encrypt_secret


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
        return decrypt_secret(value)
