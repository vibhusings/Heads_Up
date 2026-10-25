"""Phone push notifications through ntfy (https://ntfy.sh), for alerts that must land mid-call.

During a phone call the HeadsUp app may not be able to speak, so urgent alerts
also go out as a push: the free ntfy app shows the banner and iOS plays its
chime into the call, which is heard in the AirPods. With no NTFY_TOPIC set
nothing leaves the server; the alert is still published for the control room.

ntfy.sh topics are public to anyone who knows the name, so use a long random one.
"""

from __future__ import annotations

import httpx

from .config import Settings
from .events import EventBus


class Notifier:
    def __init__(self, settings: Settings, bus: EventBus, client: httpx.AsyncClient | None = None):
        self.server = settings.ntfy_server.rstrip("/")
        self.topic = settings.ntfy_topic
        self.bus = bus
        self.client = client or httpx.AsyncClient(timeout=5)
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self.topic)

    async def push(self, user: str, title: str, text: str) -> bool:
        """Send one high-priority push. Never raises: a failed push is published, not fatal."""
        data = {"title": title, "text": text, "channel": "ntfy" if self.configured else "none"}
        if not self.configured:
            self.bus.publish("notify.skipped", data | {"reason": "NTFY_TOPIC is not set"}, source="notify", user=user)
            return False
        try:
            response = await self.client.post(f"{self.server}/{self.topic}", content=text.encode(),
                                              headers={"Title": title, "Priority": "high", "Tags": "warning"})
            response.raise_for_status()
        except httpx.HTTPError as err:
            self.bus.publish("notify.failed", data | {"error": type(err).__name__}, source="notify", user=user)
            return False
        self.bus.publish("notify.sent", data, source="notify", user=user)
        return True

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
