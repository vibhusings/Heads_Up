"""Messaging: who a message may go to, and the providers that deliver it.

Recipients come only from the contact directory, never from free text. The
simulated provider writes to a labeled local outbox. A live provider that is not
connected reports a failure; nothing here turns a failed send into a success.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx

from .db import SESSION_ID, FoundationRepository
from .models import Contact, Event, OutboxEntry


@dataclass
class SendResult:
    status: Literal["succeeded", "simulated", "failed", "unknown"]
    provider_reference: str | None = None
    error_code: str | None = None
    retry_safe: bool = False


class RecipientError(Exception):
    def __init__(self, message: str, code: str = "INVALID_RECIPIENT"):
        super().__init__(message)
        self.code = code


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


# ---------- Beeper ----------

class BeeperMessaging:
    """Beeper Desktop API on the demo laptop.

    Every message is written to the outbox (labeled live) before it is handed to
    Beeper, so a crash mid-send can be reconciled against the chat's history. A
    request that never reached Beeper is a failure; one that may have reached it
    without a clear answer is unknown, and is never resent.
    """

    name = "beeper"
    mode: Literal["mock", "live"] = "live"

    def __init__(self, repository: FoundationRepository, base_url: str, token: str,
                 timeout_s: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self.repository = repository
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                      headers={"Authorization": f"Bearer {token}"})

    async def close(self) -> None:
        await self.http.aclose()

    # ---- reading ----

    async def info(self) -> dict[str, Any]:
        r = await self.http.get("/v1/info")
        r.raise_for_status()
        return r.json()

    async def list_chats(self, limit: int = 50) -> list[dict[str, Any]]:
        r = await self.http.get("/v1/chats", params={"limit": limit})
        r.raise_for_status()
        return r.json().get("items", [])

    async def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        r = await self.http.get(f"/v1/chats/{quote(chat_id, safe='')}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def read_recent_messages(self, chat_id: str) -> list[dict[str, Any]]:
        r = await self.http.get(f"/v1/chats/{quote(chat_id, safe='')}/messages")
        r.raise_for_status()
        return r.json().get("items", [])

    # ---- sending ----

    async def send_message(self, action_key: str, contact_id: str, chat_id: str, text: str) -> SendResult:
        self._outbox_put(action_key, contact_id, chat_id, text)
        try:
            r = await self.http.post(f"/v1/chats/{quote(chat_id, safe='')}/messages", json={"text": text})
        except (httpx.ConnectError, httpx.ConnectTimeout):  # Beeper not running: nothing was sent
            self._outbox_drop(action_key)
            return SendResult(status="failed", error_code="PROVIDER_UNAVAILABLE", retry_safe=True)
        except httpx.HTTPError:  # sent, but no clear answer
            return SendResult(status="unknown", error_code="OUTCOME_UNKNOWN")
        if r.is_success:
            try:
                data = r.json()
                reference = f"{data.get('chatID', chat_id)}:{data['pendingMessageID']}"
            except (ValueError, KeyError):
                reference = None
            return SendResult(status="succeeded", provider_reference=reference)
        if r.status_code >= 500:
            return SendResult(status="unknown", error_code="OUTCOME_UNKNOWN")
        self._outbox_drop(action_key)
        code = {401: "AUTH_REQUIRED", 403: "AUTH_REQUIRED", 404: "INVALID_RECIPIENT",
                429: "RATE_LIMITED"}.get(r.status_code, "PROVIDER_UNAVAILABLE")
        return SendResult(status="failed", error_code=code, retry_safe=r.status_code == 429)

    async def reconcile(self, action_key: str) -> SendResult | None:
        """After a crash mid-send: look for the message in the chat's recent history."""
        row = self.repository.db.execute(
            "SELECT chat_id, message_text, created_at FROM outbox WHERE action_key = ?", (action_key,)
        ).fetchone()
        if row is None:
            return SendResult(status="failed", error_code="PROVIDER_UNAVAILABLE", retry_safe=True)  # never handed over
        chat_id, text, created_at = row
        since = datetime.fromisoformat(created_at) - timedelta(minutes=1)
        try:
            recent = await self.read_recent_messages(chat_id)
        except httpx.HTTPError:
            return None
        for message in recent:
            if message.get("isSender") and (message.get("text") or "").strip() == text.strip() \
                    and _after(message.get("timestamp"), since):
                return SendResult(status="succeeded", provider_reference=f"{chat_id}:{message.get('id')}")
        return None

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "configured"}

    def _outbox_put(self, action_key: str, contact_id: str, chat_id: str, text: str) -> None:
        with self.repository.transaction():
            self.repository.db.execute(
                "INSERT OR IGNORE INTO outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
                (action_key, SESSION_ID, contact_id, chat_id, text, self.mode,
                 datetime.now(timezone.utc).isoformat()),
            )

    def _outbox_drop(self, action_key: str) -> None:
        with self.repository.transaction():
            self.repository.db.execute("DELETE FROM outbox WHERE action_key = ?", (action_key,))


def _after(timestamp: Any, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")) >= since
    except ValueError:
        return False  # can't tell when it was sent: don't claim it


# ---------- verified contact-to-chat mappings ----------

async def map_contact_chat(repository: FoundationRepository, messaging: MessagingProvider,
                           contact_id: str, chat_id: str) -> Contact:
    """Map a contact to a Beeper chat after checking the chat exists and is one-to-one."""
    if repository.get("contacts", contact_id) is None:
        raise RecipientError(f"{contact_id} is not in the contact directory", "NOT_FOUND")
    if not isinstance(messaging, BeeperMessaging):
        raise RecipientError("set MESSAGING_MODE=live and BEEPER_ACCESS_TOKEN to verify a chat", "AUTH_REQUIRED")
    try:
        chat = await messaging.get_chat(chat_id)
    except httpx.HTTPError as exc:
        raise RecipientError(f"Beeper could not be reached: {exc}", "PROVIDER_UNAVAILABLE") from exc
    if chat is None:
        raise RecipientError(f"Beeper has no chat {chat_id}")
    if chat.get("type") != "single":
        raise RecipientError(f"{chat.get('title', chat_id)} is a group chat; map a one-to-one chat")
    owner = repository.db.execute(
        "SELECT contact_id FROM contact_chats WHERE chat_id = ? AND contact_id != ?", (chat["id"], contact_id)
    ).fetchone()
    if owner is not None:
        raise RecipientError(f"that chat is already mapped to {owner[0]}")
    with repository.transaction():
        repository.db.execute(
            "INSERT INTO contact_chats VALUES (?, ?, ?, ?, ?) ON CONFLICT(contact_id) DO UPDATE SET "
            "chat_id = excluded.chat_id, title = excluded.title, network = excluded.network, "
            "verified_at = excluded.verified_at",
            (contact_id, chat["id"], chat.get("title"), chat.get("network"), datetime.now(timezone.utc).isoformat()),
        )
    apply_chat_mappings(repository)
    return repository.get("contacts", contact_id)


def unmap_contact_chat(repository: FoundationRepository, contact_id: str) -> Contact:
    contact = repository.get("contacts", contact_id)
    if contact is None:
        raise RecipientError(f"{contact_id} is not in the contact directory", "NOT_FOUND")
    with repository.transaction():
        repository.db.execute("DELETE FROM contact_chats WHERE contact_id = ?", (contact_id,))
        repository.put("contacts", contact.model_copy(update={"beeper_chat_id": None}))
    return repository.get("contacts", contact_id)


def apply_chat_mappings(repository: FoundationRepository) -> None:
    """Copy verified chats onto the contacts; run at startup and after a demo reset."""
    rows = repository.db.execute("SELECT contact_id, chat_id FROM contact_chats").fetchall()
    with repository.transaction():
        for contact_id, chat_id in rows:
            contact = repository.get("contacts", contact_id)
            if contact is not None and contact.beeper_chat_id != chat_id:
                repository.put("contacts", contact.model_copy(update={"beeper_chat_id": chat_id}))


# ---------- command line: find chat IDs by hand ----------

async def _cli(argv: list[str] | None = None) -> None:
    from .config import settings

    parser = argparse.ArgumentParser(prog="python -m headsup.messaging", description="Inspect Beeper chats.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="confirm Beeper Desktop API is reachable")
    chats = sub.add_parser("chats", help="list recent chats with their IDs")
    chats.add_argument("--limit", type=int, default=25)
    messages = sub.add_parser("messages", help="show recent messages in one chat")
    messages.add_argument("chat_id")
    args = parser.parse_args(argv)
    if not settings.beeper_token:
        raise SystemExit("BEEPER_ACCESS_TOKEN is not set")

    beeper = BeeperMessaging(None, settings.beeper_url, settings.beeper_token)  # type: ignore[arg-type]
    try:
        if args.command == "check":
            print(await beeper.info())
        elif args.command == "chats":
            for chat in await beeper.list_chats(args.limit):
                print(f"{chat.get('id')}\t{chat.get('type')}\t{chat.get('network')}\t{chat.get('title')}")
        else:
            for m in await beeper.read_recent_messages(args.chat_id):
                who = "me" if m.get("isSender") else m.get("senderName")
                print(f"{m.get('timestamp')}\t{who}\t{m.get('text')}")
    finally:
        await beeper.close()


if __name__ == "__main__":
    asyncio.run(_cli())
