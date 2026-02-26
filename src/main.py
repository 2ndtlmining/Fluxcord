"""
Fluxcord – Flux Discord Monitor
Entry point. Sets up scheduling and starts the main loop.
"""

import logging
import os
import sys
import time

import schedule

from src.config import Config
from src.database import Database
from src.discord_client import DiscordClient, COLOR_BLUE, COLOR_RED
from src.monitors.wallet_monitor import WalletMonitor
from src.monitors.node_monitor import NodeMonitor
from src.monitors.network_monitor import NetworkMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("fluxcord")


def main():
    config = Config()

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        sys.exit(1)

    db = Database(config.db_path)
    discord = DiscordClient(config.discord_webhook, config.discord_user)

    wallet_mon = WalletMonitor(config, db, discord)
    node_mon = NodeMonitor(config, db, discord)
    network_mon = NetworkMonitor(config, db, discord)

    # ── Announce startup ────────────────────────────────────────────────────────
    startup_fields = [
        {
            "name": "💳 Wallet Notifications",
            "value": f"{'✅ Enabled' if config.wallet_notifications else '❌ Disabled'}"
                     + (f" – `{config.wallet[:20]}…`" if config.wallet_notifications and config.wallet else ""),
            "inline": False,
        },
        {
            "name": "🖥️ Node Notifications",
            "value": f"{'✅ Enabled' if config.node_wallet_notifications else '❌ Disabled'}"
                     + (f" – `{config.wallet[:20]}…`" if config.node_wallet_notifications and config.wallet else ""),
            "inline": False,
        },
        {
            "name": "📈 Network Notifications",
            "value": "✅ Enabled" if config.network_notifications else "❌ Disabled",
            "inline": False,
        },
        {
            "name": "⏱️ Poll Interval",
            "value": f"Every **{config.poll_interval}** minutes",
            "inline": True,
        },
        {
            "name": "📅 Daily Report",
            "value": f"**{config.daily_hour_utc:02d}:00 UTC**",
            "inline": True,
        },
    ]

    discord.send_embed(
        discord.build_embed(
            title="🚀 Fluxcord Started",
            description="Flux Discord Monitor is now active.",
            color=COLOR_BLUE,
            fields=startup_fields,
            footer="Fluxcord",
        )
    )

    # ── Schedule recurring tasks ────────────────────────────────────────────────
    schedule.every(config.poll_interval).minutes.do(_safe(wallet_mon.check))
    schedule.every(config.poll_interval).minutes.do(_safe(node_mon.check_5min))

    daily_time = f"{config.daily_hour_utc:02d}:00"
    schedule.every().day.at(daily_time).do(_safe(node_mon.check_daily))
    schedule.every().day.at(daily_time).do(_safe(network_mon.check_daily))

    logger.info(
        "Scheduler configured. Poll: every %d min | Daily: %s UTC",
        config.poll_interval,
        daily_time,
    )

    # ── Run initial checks immediately ─────────────────────────────────────────
    logger.info("Running initial checks…")
    _safe(wallet_mon.check)()
    _safe(node_mon.check_5min)()
    _safe(node_mon.check_daily)()
    _safe(network_mon.check_daily)()

    # ── Main loop ───────────────────────────────────────────────────────────────
    logger.info("Entering main scheduler loop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


def _safe(fn):
    """Wrap a monitor function so an uncaught exception won't kill the process."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("Unhandled error in %s: %s", fn.__qualname__, exc)
    return wrapper


if __name__ == "__main__":
    main()
