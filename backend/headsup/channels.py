"""The channel ladder: silent, tap, whisper, phone call."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class Channel(IntEnum):
    SILENT = 0
    TAP = 1
    WHISPER = 2
    CALL = 3


def pick_channel(kind: str, urgency: str, ctx: dict[str, Any]) -> Channel:
    """Least intrusive channel that still works for this moment."""
    if kind == "fyi":
        return Channel.TAP
    if urgency == "critical" and not ctx.get("airpods_in"):
        return Channel.CALL
    if ctx.get("in_meeting"):
        return Channel.TAP  # decision pattern, approve with double-tap
    if ctx.get("airpods_in"):
        return Channel.WHISPER
    return Channel.CALL


def escalate(channel: Channel) -> Channel | None:
    return Channel(channel + 1) if channel < Channel.CALL else None
