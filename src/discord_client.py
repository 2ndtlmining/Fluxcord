"""
Discord webhook client.

Sends rich embeds via a webhook URL. All public methods return True on success.
"""

import logging
import threading
import time
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Standard embed colours
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C
COLOR_YELLOW = 0xF1C40F
COLOR_BLUE = 0x3498DB
COLOR_GRAY = 0x95A5A6
COLOR_PURPLE = 0x9B59B6


class DiscordClient:
    def __init__(self, webhook_url: str, user_id: str = ""):
        self.webhook_url = webhook_url
        self.user_id = user_id
        self._lock = threading.Lock()   # serialise concurrent send_embed calls

    # ── Public API ──────────────────────────────────────────────────────────────

    def send_embed(self, embed: dict, mention: bool = False) -> bool:
        return self.send_embeds([embed], mention=mention)

    def send_embeds(self, embeds: list[dict], mention: bool = False) -> bool:
        """Send up to 10 embeds per Discord message. Batches automatically."""
        if not embeds:
            return True
        success = True
        for i in range(0, len(embeds), 10):
            batch = embeds[i : i + 10]
            payload: dict = {"embeds": batch}
            if mention and self.user_id and i == 0:
                payload["content"] = f"<@{self.user_id}>"
            if not self._post(payload):
                success = False
            # Respect Discord rate limit (5 req/2s per webhook)
            if i + 10 < len(embeds):
                time.sleep(0.6)
        return success

    # ── Builder ────────────────────────────────────────────────────────────────

    def build_embed(
        self,
        title: str,
        description: str = "",
        color: int = COLOR_BLUE,
        fields: list[dict] | None = None,
        footer: str = "Fluxcord",
    ) -> dict:
        embed: dict = {
            "title": title,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if description:
            embed["description"] = description
        if fields:
            embed["fields"] = fields
        if footer:
            embed["footer"] = {"text": footer}
        return embed

    # ── Internal ───────────────────────────────────────────────────────────────

    def _post(self, payload: dict) -> bool:
        with self._lock:
            for attempt in range(4):
                try:
                    resp = requests.post(self.webhook_url, json=payload, timeout=15)
                    if resp.status_code == 429:
                        retry_after = float(resp.json().get("retry_after", 1))
                        logger.warning("Discord rate-limited, sleeping %.1fs", retry_after)
                        time.sleep(retry_after + 0.1)
                        continue
                    resp.raise_for_status()
                    return True
                except requests.RequestException as exc:
                    if attempt < 3:
                        logger.warning("Discord webhook error (attempt %d): %s", attempt + 1, exc)
                        time.sleep(1.0)
                    else:
                        logger.error("Discord webhook error: %s", exc)
            return False
