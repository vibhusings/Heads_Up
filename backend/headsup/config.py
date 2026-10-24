"""Runtime settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Models. "main" handles planning, research and diagnosis; "fast" handles
    # high-volume triage, extraction and verification.
    model_main: str = field(default_factory=lambda: os.getenv("HEADSUP_MODEL_MAIN", "claude-opus-5-5"))
    model_fast: str = field(default_factory=lambda: os.getenv("HEADSUP_MODEL_FAST", "claude-haiku-5-5"))

    # Offline mode swaps every model call for a deterministic heuristic, so the
    # whole system runs (and tests pass) without an API key.
    offline: bool = field(
        default_factory=lambda: _env_bool(
            "HEADSUP_OFFLINE", not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        )
    )

    # Budgets, in US dollars.
    daily_budget_usd: float = field(default_factory=lambda: float(os.getenv("HEADSUP_DAILY_BUDGET", "2.00")))
    default_task_budget_usd: float = 0.25

    # Supervisor tuning.
    supervisor_tick_s: float = 1.0
    heartbeat_timeout_s: float = 10.0
    loop_repeat_limit: int = 2  # same action more than this many times = loop
    stale_data_s: float = 120.0
    flight_poll_s: float = 2.0  # watcher tick; simulated feeds are polled every tick
    # Real flight data: per-flight request spacing, to stay inside the API quota.
    flight_real_poll_s: float = field(default_factory=lambda: float(os.getenv("HEADSUP_FLIGHT_POLL_REAL", "600")))
    flight_error_retry_s: float = 60.0

    # Device answers.
    answer_timeout_s: float = field(default_factory=lambda: float(os.getenv("HEADSUP_ANSWER_TIMEOUT", "20")))

    # Storage.
    db_path: str = field(default_factory=lambda: os.getenv("HEADSUP_DB", "headsup.db"))

    # Calendar defaults to deterministic fixtures. Live mode uses user OAuth
    # files kept outside the repository on the designated demo laptop.
    calendar_mode: str = field(default_factory=lambda: os.getenv("CALENDAR_MODE", "mock").lower())
    google_calendar_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CALENDAR_ID", "primary"))
    google_calendar_token_file: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CALENDAR_TOKEN_FILE"))
    google_calendar_write_enabled: bool = field(
        default_factory=lambda: _env_bool("GOOGLE_CALENDAR_WRITE_ENABLED", False)
    )
    google_calendar_freebusy_enabled: bool = field(
        default_factory=lambda: _env_bool("GOOGLE_CALENDAR_FREEBUSY_ENABLED", False)
    )
    pairing_token: str | None = field(default_factory=lambda: os.getenv("HEADSUP_PAIRING_TOKEN"))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            item.strip()
            for item in os.getenv("HEADSUP_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if item.strip()
        )
    )

    # Optional real integrations. Each one falls back to its simulator when unset.
    aviationstack_key: str | None = field(default_factory=lambda: os.getenv("AVIATIONSTACK_KEY"))
    wmata_key: str | None = field(default_factory=lambda: os.getenv("WMATA_KEY"))
    twilio_sid: str | None = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID"))
    twilio_token: str | None = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN"))
    twilio_from: str | None = field(default_factory=lambda: os.getenv("TWILIO_FROM_NUMBER"))
    deepgram_key: str | None = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY"))
    # Public https URL Twilio can reach (e.g. an ngrok tunnel to this server).
    public_url: str | None = field(default_factory=lambda: os.getenv("HEADSUP_PUBLIC_URL"))

    # Day rhythm.
    leave_buffer_min: int = 5  # leave this many minutes before you strictly have to
    receipt_at: str = field(default_factory=lambda: os.getenv("HEADSUP_RECEIPT_AT", "21:30"))
    timekeeper_tick_s: float = 1.0

    # Hold the line for me.
    hold_limit_s: float = field(default_factory=lambda: float(os.getenv("HEADSUP_HOLD_LIMIT", "1200")))  # then ask
    hold_sim_segment_s: float = 1.5  # simulated business: seconds between hold recordings

    @property
    def twilio_ready(self) -> bool:
        return bool(self.twilio_sid and self.twilio_token and self.twilio_from and self.public_url)


settings = Settings()
