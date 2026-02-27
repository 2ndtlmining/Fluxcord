import os
import logging
from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present; no-op in Docker

logger = logging.getLogger(__name__)


class Config:
    def __init__(self):
        self.wallet = os.environ.get("WALLET", "").strip()
        self.wallet_notifications = os.environ.get("WALLET_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")

        self.node_wallet_notifications = os.environ.get("NODE_WALLET_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")

        self.network_notifications = os.environ.get("NETWORK_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")

        self.discord_user = os.environ.get("DISCORD_USER", "").strip()
        self.discord_webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()

        self.db_path = os.environ.get("DB_PATH", "/data/fluxcord.db")
        self.daily_hour_utc = int(os.environ.get("DAILY_HOUR_UTC", "8"))
        self.poll_interval = int(os.environ.get("POLL_INTERVAL_MINUTES", "5"))
        self.node_request_timeout = int(os.environ.get("NODE_REQUEST_TIMEOUT", "10"))
        self.api_request_timeout = int(os.environ.get("API_REQUEST_TIMEOUT", "30"))

        # How many blocks back to scan for new apps (user specified 2880 ≈ 1 day on Flux)
        self.new_apps_block_window = int(os.environ.get("NEW_APPS_BLOCK_WINDOW", "2880"))

    def validate(self) -> list[str]:
        errors = []
        if not self.discord_webhook:
            errors.append("DISCORD_WEBHOOK is required")
        if self.wallet_notifications and not self.wallet:
            errors.append("WALLET is required when WALLET_NOTIFICATIONS=true")
        if self.node_wallet_notifications and not self.wallet:
            errors.append("WALLET is required when NODE_WALLET_NOTIFICATIONS=true")
        if not any([self.wallet_notifications, self.node_wallet_notifications, self.network_notifications]):
            logger.warning(
                "No notification categories enabled. Set at least one of: "
                "WALLET_NOTIFICATIONS, NODE_WALLET_NOTIFICATIONS, NETWORK_NOTIFICATIONS"
            )
        return errors
