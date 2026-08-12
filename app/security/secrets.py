from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.repositories.database import Database


class SecretCipher:
    def __init__(self, path: Path, database: Database) -> None:
        self.path = path
        self._key = self._load_or_create_key(database)
        self._fernet = Fernet(self._key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise RuntimeError(
                "Cannot decrypt server settings; restore the matching secrets.key"
            ) from exc

    def derive_key(self, purpose: str) -> bytes:
        return hashlib.sha256(self._key + b"\0" + purpose.encode("utf-8")).digest()

    def _load_or_create_key(self, database: Database) -> bytes:
        if self.path.exists():
            key = self.path.read_bytes().strip()
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("secrets.key is invalid") from exc
            self._restrict_permissions()
            return key

        encrypted_setting_exists = bool(
            database.scalar("SELECT 1 FROM app_settings WHERE is_secret = 1 LIMIT 1")
        )
        if encrypted_setting_exists:
            raise RuntimeError(
                "comiclens.db contains encrypted settings but secrets.key is missing"
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self.path, flags, 0o600)
        try:
            os.write(descriptor, key + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._restrict_permissions()
        return key

    def _restrict_permissions(self) -> None:
        # Some mounted filesystems do not support POSIX modes. Encryption still works.
        with suppress(OSError):
            self.path.chmod(0o600)
