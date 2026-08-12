from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass

SESSION_COOKIE_NAME = "comiclens_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class AccessGate:
    password: str | None
    signing_key: bytes

    @property
    def enabled(self) -> bool:
        return self.password is not None

    @property
    def password_version(self) -> str:
        if self.password is None:
            return "disabled"
        return hashlib.sha256(self.password.encode("utf-8")).hexdigest()

    def verify_password(self, candidate: str) -> bool:
        if self.password is None:
            return True
        return hmac.compare_digest(
            hashlib.sha256(candidate.encode("utf-8")).digest(),
            hashlib.sha256(self.password.encode("utf-8")).digest(),
        )

    def issue_session(self, *, now: int | None = None) -> str:
        issued_at = int(time.time()) if now is None else now
        payload = {
            "exp": issued_at + SESSION_MAX_AGE_SECONDS,
            "v": self.password_version,
        }
        encoded = self._encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = self._encode(
            hmac.new(self.signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{encoded}.{signature}"

    def validate_session(self, token: str | None, *, now: int | None = None) -> bool:
        if not self.enabled:
            return True
        if not token:
            return False
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = self._encode(
                hmac.new(self.signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return False
            payload = json.loads(self._decode(encoded))
            current_time = int(time.time()) if now is None else now
            return (
                isinstance(payload, dict)
                and payload.get("v") == self.password_version
                and isinstance(payload.get("exp"), int)
                and payload["exp"] > current_time
            )
        except (ValueError, UnicodeError, json.JSONDecodeError, binascii.Error):
            return False

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)


class LoginRateLimiter:
    def __init__(self, *, limit: int = 5, window_seconds: int = 300) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, identity: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        failures = self._failures[identity]
        while failures and timestamp - failures[0] >= self.window_seconds:
            failures.popleft()
        return len(failures) < self.limit

    def record_failure(self, identity: str, *, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else now
        self._failures[identity].append(timestamp)

    def reset(self, identity: str) -> None:
        self._failures.pop(identity, None)
