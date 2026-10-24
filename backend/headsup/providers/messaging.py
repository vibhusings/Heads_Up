"""Provider boundary for duplicate-safe message submission."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict


class SendResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    provider_mode: Literal["mock", "live"]
    status: Literal["succeeded", "simulated", "failed", "unknown"]
    provider_reference: str | None = None
    error_code: str | None = None
    retry_safe: bool = False


class MessagingProvider(Protocol):
    mode: str

    def send(self, chat_id: str, text: str, action_id: str) -> SendResult: ...
    def health(self) -> dict[str, Any]: ...


class MockMessagingProvider:
    mode = "mock"

    def __init__(self):
        self.sent: dict[str, SendResult] = {}

    def send(self, chat_id: str, text: str, action_id: str) -> SendResult:
        existing = self.sent.get(action_id)
        if existing is not None:
            return existing
        result = SendResult(
            provider="simulated_outbox",
            provider_mode="mock",
            status="simulated",
            provider_reference=f"outbox:{action_id}",
        )
        self.sent[action_id] = result
        return result

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "ok"}

    def reset(self) -> None:
        self.sent.clear()


class UnavailableMessagingProvider:
    mode = "live"

    def __init__(self, reason: str = "Live messaging is not configured"):
        self.reason = reason

    def send(self, chat_id: str, text: str, action_id: str) -> SendResult:
        return SendResult(
            provider="messaging",
            provider_mode="live",
            status="failed",
            error_code="AUTH_REQUIRED",
            retry_safe=False,
        )

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "unavailable", "error_code": "AUTH_REQUIRED"}
