"""Stable application contracts shared by the API, engine, and executors."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_aware)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Contact(Contract):
    id: str
    display_name: str
    relationship: str
    email: str | None = None
    beeper_chat_id: str | None = None
    preferred_channel: str
    tone: str
    auto_notify_allowed: bool = False


class Event(Contract):
    id: str
    provider: str
    provider_event_id: str | None = None
    title: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    timezone: str
    mode: Literal["in_person", "remote", "unknown"] = "unknown"
    destination: str | None = None
    contact_ids: list[str] = Field(default_factory=list)
    organizer_self: bool = False
    writable: bool = False
    provider_version: str | None = None
    tentative: bool = False

    @model_validator(mode="after")
    def valid_time_range(self) -> "Event":
        if self.end_at <= self.start_at:
            raise ValueError("event end must be after start")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an IANA timezone") from exc
        return self


class Position(Contract):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0)


class Signal(Contract):
    id: str
    kind: str
    source: str
    source_mode: Literal["mock", "live"]
    observed_at: AwareDatetime
    flight_instance_id: str | None = None
    estimated_arrival_at: AwareDatetime | None = None
    position: Position | None = None
    route_duration_seconds: int | None = Field(default=None, ge=0)


class IncomingMessage(Contract):
    id: str
    chat_id: str
    contact_id: str | None = None
    sender_name: str | None = None
    text: str
    received_at: AwareDatetime
    status: Literal["processed", "unprocessed", "ambiguous"] = "unprocessed"
    error_code: str | None = None


class ScheduleRequest(Contract):
    id: str
    message_id: str
    contact_id: str
    title: str
    requested_start_at: AwareDatetime
    requested_end_at: AwareDatetime
    timezone: str
    status: Literal["proposed", "approved", "rejected"] = "proposed"


class Impact(Contract):
    id: str
    event_id: str
    signal_ids: list[str]
    earliest_arrival_at: AwareDatetime
    late_by_seconds: int = Field(ge=0)
    reason: str
    confidence: str
    evidence_times: dict[str, AwareDatetime] = Field(default_factory=dict)
    status: str


class Action(Contract):
    id: str
    type: str
    contact_id: str | None = None
    event_id: str | None = None
    message_text: str | None = None
    new_start_at: AwareDatetime | None = None
    new_end_at: AwareDatetime | None = None
    event_title: str | None = None
    timezone: str | None = None
    tentative: bool = False
    depends_on_action_ids: list[str] = Field(default_factory=list)
    status: Literal["pending", "executing", "succeeded", "simulated", "failed", "unknown", "skipped"] = "pending"


class Plan(Contract):
    id: str
    version: int = Field(ge=1)
    impact_id: str
    status: Literal[
        "proposed", "approved", "executing", "completed", "partially_failed", "failed", "rejected", "expired"
    ] = "proposed"
    actions: list[Action]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_expiry(self) -> "Plan":
        if self.expires_at <= self.created_at:
            raise ValueError("plan expiry must be after creation")
        return self


class ActionResult(Contract):
    action_id: str
    provider: str
    provider_mode: Literal["mock", "live"]
    status: Literal["succeeded", "simulated", "failed", "unknown", "skipped"]
    provider_reference: str | None = None
    error_code: str | None = None
    completed_at: AwareDatetime | None = None


class OutboxEntry(Contract):
    """One message the executor handed to the messaging provider, labeled by mode."""

    action_key: str
    contact_id: str
    chat_id: str
    message_text: str
    provider_mode: Literal["mock", "live"]
    created_at: AwareDatetime


class Approval(Contract):
    id: str
    plan_id: str
    plan_version: int = Field(ge=1)
    user_id: str
    approved_at: AwareDatetime
    approval_channel: str


class SessionState(Contract):
    id: str
    scenario_id: str
    contacts: list[Contact]
    events: list[Event]
    signals: list[Signal]
    impacts: list[Impact]
    plans: list[Plan]
    actions: list[Action]
    results: list[ActionResult]
    approvals: list[Approval]
    incoming_messages: list[IncomingMessage] = Field(default_factory=list)
    schedule_requests: list[ScheduleRequest] = Field(default_factory=list)


class SignalResult(Contract):
    signal_id: str
    duplicate: bool
    impact_ids: list[str]
    plan_ids: list[str]


class LocationInput(Contract):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float = Field(ge=0)
    captured_at: AwareDatetime
    route_duration_seconds: int | None = Field(default=None, ge=0)


class LocationResult(Contract):
    status: Literal["accepted", "uncertain", "stale"]
    signal: SignalResult | None = None
    error_code: str | None = None


class PlanPatch(Contract):
    expected_version: int = Field(ge=1)
    message_text: str | None = Field(default=None, min_length=1)
    new_start_at: AwareDatetime | None = None
    new_end_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_edit(self) -> "PlanPatch":
        if self.message_text is None and self.new_start_at is None and self.new_end_at is None:
            raise ValueError("at least one edit is required")
        if (self.new_start_at is None) != (self.new_end_at is None):
            raise ValueError("new start and end must be supplied together")
        if self.new_start_at is not None and self.new_end_at <= self.new_start_at:
            raise ValueError("new end must be after new start")
        return self


class PlanCommand(Contract):
    expected_version: int = Field(ge=1)
    user_id: str = "demo_user"
    channel: str = "button"


class ExecutionOutcome(Contract):
    plan: Plan
    results: list[ActionResult]
    duplicate: bool


class Health(Contract):
    status: Literal["ok", "degraded"]
    providers: dict[str, dict[str, Any]]
