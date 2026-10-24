"""Messaging: who a message may go to, and the providers that deliver it.

Recipients come only from the contact directory, never from free text. The
simulated provider writes to a labeled local outbox. A live provider that is not
connected reports a failure; nothing here turns a failed send into a success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from .db import SESSION_ID, FoundationRepository
from .models import Contact, Event, OutboxEntry


@dataclass
class SendResult:
    status: Literal["succeeded", "simulated", "failed", "unknown"]
    provider_reference: str | None = None
    error_code: str | None = None
    retry_safe: bool = False


class RecipientError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.code = "INVALID_RECIPIENT"


def resolve_recipient(contact_id: str | None, contacts: list[Contact], event: Event | None, mode: str) -> str:
    """Return the chat to send to, or raise when the recipient is unknown or ambiguous."""
    if not contact_id:
        raise RecipientError("message has no recipient")
    contact = next((item for item in contacts if item.id == contact_id), None)
    if contact is None:
        raise RecipientError(f"{contact_id} is not in the contact directory")
    if event is not None and contact_id not in event.contact_ids:
        raise RecipientError(f"{contact.display_name} is not part of {event.title}")
    if mode == "live":
        if not contact.beeper_chat_id:
            raise RecipientError(f"{contact.display_name} has no verified chat")
        if sum(item.beeper_chat_id == contact.beeper_chat_id for item in contacts) > 1:
            raise RecipientError(f"chat for {contact.display_name} is mapped to more than one contact")
        return contact.beeper_chat_id
    return contact.beeper_chat_id or f"simulated:{contact.id}"


class MessagingProvider(Protocol):
    name: str
    mode: Literal["mock", "live"]

    async def send_message(self, action_key: str, contact_id: str, chat_id: str, text: str) -> SendResult: ...
    async def reconcile(self, action_key: str) -> SendResult | None: ...
    def health(self) -> dict[str, Any]: ...


class SimulatedMessaging:
    """Writes each message once to the local outbox, keyed by its action key."""

    name = "simulated_outbox"
    mode: Literal["mock", "live"] = "mock"

    def __init__(self, repository: FoundationRepository):
        self.repository = repository

    async def send_message(self, action_key: str, contact_id: str, chat_id: str, text: str) -> SendResult:
        with self.repository.transaction():
            self.repository.db.execute(
                "INSERT OR IGNORE INTO outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
                (action_key, SESSION_ID, contact_id, chat_id, text, self.mode,
                 datetime.now(timezone.utc).isoformat()),
            )
        return SendResult(status="simulated", provider_reference=f"outbox:{action_key}")

    async def reconcile(self, action_key: str) -> SendResult | None:
        row = self.repository.db.execute("SELECT 1 FROM outbox WHERE action_key = ?", (action_key,)).fetchone()
        return SendResult(status="simulated", provider_reference=f"outbox:{action_key}") if row else None

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "ok"}


class UnavailableMessaging:
    """Live messaging selected but not connected: every send fails, visibly."""

    name = "beeper"
    mode: Literal["mock", "live"] = "live"

    def __init__(self, reason: str = "Beeper is not connected"):
        self.reason = reason

    async def send_message(self, action_key: str, contact_id: str, chat_id: str, text: str) -> SendResult:
        return SendResult(status="failed", error_code="AUTH_REQUIRED")

    async def reconcile(self, action_key: str) -> SendResult | None:
        return None

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "unavailable", "error_code": "AUTH_REQUIRED"}


def outbox(repository: FoundationRepository) -> list[OutboxEntry]:
    rows = repository.db.execute(
        "SELECT action_key, contact_id, chat_id, message_text, provider_mode, created_at "
        "FROM outbox WHERE session_id = ? ORDER BY rowid", (SESSION_ID,)
    ).fetchall()
    keys = ("action_key", "contact_id", "chat_id", "message_text", "provider_mode", "created_at")
    return [OutboxEntry.model_validate(dict(zip(keys, row))) for row in rows]
