"""Short-lived phone pairing tokens, minted from the desktop screen and shown as a QR code.

Tokens live in memory only: a backend restart unpairs the phone. Minting a new token
replaces the old one, so at most one phone is paired at a time.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

PAIRING_TTL = timedelta(minutes=60)


class PairingStore:
    def __init__(self, static_token: str | None = None, ttl: timedelta = PAIRING_TTL):
        self.static_token = static_token
        self.ttl = ttl
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def mint(self, now: datetime | None = None) -> tuple[str, datetime]:
        now = now or datetime.now(timezone.utc)
        self._token = secrets.token_urlsafe(24)
        self._expires_at = now + self.ttl
        return self._token, self._expires_at

    def valid(self, token: str | None, now: datetime | None = None) -> bool:
        if not token:
            return False
        if self.static_token and secrets.compare_digest(token, self.static_token):
            return True
        now = now or datetime.now(timezone.utc)
        return bool(
            self._token
            and self._expires_at
            and now < self._expires_at
            and secrets.compare_digest(token, self._token)
        )

    def status(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        if self._expires_at and now < self._expires_at:
            return "paired"
        return "configured" if self.static_token else "disabled"
