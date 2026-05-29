from __future__ import annotations

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.services.backup.errors import BackupFormatError, BackupPassphraseError

# Envelope = MAGIC(8) | salt(16) | nonce(12) | N,r,p (3x uint32 BE) | AES-GCM(ct+tag)
# The header is cleartext (KDF params are not secret); the payload lives inside the
# AES-GCM ciphertext. The header is bound as associated data.
_MAGIC = b"SNTLBK01"
_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32
_PARAMS_FMT = ">III"
_HEADER_LEN = len(_MAGIC) + _SALT_LEN + _NONCE_LEN + struct.calcsize(_PARAMS_FMT)

# scrypt cost parameters: ~32 MiB working set, interactive-grade.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1

# Upper bounds for KDF params read from an untrusted header. These cap the work
# (and memory ~= 128 * N * r bytes) an attacker can force before authentication,
# turning a potential resource-exhaustion bomb into a clean format error.
_MAX_SCRYPT_N = 2**20
_MAX_SCRYPT_R = 32
_MAX_SCRYPT_P = 16


def _validate_kdf_params(n: int, r: int, p: int) -> None:
    if n < 2 or n > _MAX_SCRYPT_N or (n & (n - 1)) != 0:
        raise BackupFormatError("Backup header has an out-of-range scrypt N parameter.")
    if r < 1 or r > _MAX_SCRYPT_R:
        raise BackupFormatError("Backup header has an out-of-range scrypt r parameter.")
    if p < 1 or p > _MAX_SCRYPT_P:
        raise BackupFormatError("Backup header has an out-of-range scrypt p parameter.")


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_backup(plaintext: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise BackupPassphraseError("A passphrase is required.")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    header = _MAGIC + salt + nonce + struct.pack(_PARAMS_FMT, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    key = _derive_key(passphrase, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
    return header + ciphertext


def decrypt_backup(blob: bytes, passphrase: str) -> bytes:
    if len(blob) < _HEADER_LEN or not blob.startswith(_MAGIC):
        raise BackupFormatError("Not a Sentinel backup.")
    header = blob[:_HEADER_LEN]
    offset = len(_MAGIC)
    salt = header[offset : offset + _SALT_LEN]
    offset += _SALT_LEN
    nonce = header[offset : offset + _NONCE_LEN]
    offset += _NONCE_LEN
    n, r, p = struct.unpack(_PARAMS_FMT, header[offset:])
    _validate_kdf_params(n, r, p)
    key = _derive_key(passphrase, salt, n, r, p)
    try:
        return AESGCM(key).decrypt(nonce, blob[_HEADER_LEN:], header)
    except InvalidTag as exc:
        raise BackupPassphraseError("Incorrect passphrase or corrupted backup.") from exc
