from __future__ import annotations


class BackupError(RuntimeError):
    pass


class BackupPassphraseError(BackupError):
    """The supplied passphrase could not decrypt/authenticate the backup."""


class BackupFormatError(BackupError):
    """The backup is malformed or an unsupported schema version."""


class BackupCompatibilityError(BackupError):
    """The backup's app version is outside this build's restorable range, or the
    instance schema has drifted past the verified head."""
