from __future__ import annotations

import pytest

from app.services.secrets import SecretDecryptionError, decrypt_secret, encrypt_secret
from app.services.secrets.types import EncryptedText


def test_round_trip() -> None:
    plaintext = "ssh-private-key\nwith newlines"
    token = encrypt_secret(plaintext)
    assert token != plaintext
    assert token.startswith("sentinel:v1:")
    assert decrypt_secret(token) == plaintext


def test_distinct_tokens_for_same_value() -> None:
    assert encrypt_secret("same") != encrypt_secret("same")


def test_rejects_unknown_envelope() -> None:
    with pytest.raises(SecretDecryptionError):
        decrypt_secret("plain-value")


def test_rejects_tampered_token() -> None:
    token = encrypt_secret("secret")
    corrupted = token[:-5] + ("AAAAA" if not token.endswith("AAAAA") else "BBBBB")
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(corrupted)


def test_column_type_binds_and_loads() -> None:
    column = EncryptedText()
    stored = column.process_bind_param("hunter2", dialect=None)
    assert stored is not None
    assert stored.startswith("sentinel:v1:")
    assert column.process_result_value(stored, dialect=None) == "hunter2"


def test_column_type_passes_through_none() -> None:
    column = EncryptedText()
    assert column.process_bind_param(None, dialect=None) is None
    assert column.process_result_value(None, dialect=None) is None
