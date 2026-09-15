from __future__ import annotations


class BackupError(RuntimeError):
    pass


class BackupPassphraseError(BackupError):
    """The supplied passphrase could not decrypt/authenticate the backup."""


class BackupFormatError(BackupError):
    """The backup is malformed or an unsupported schema version."""
