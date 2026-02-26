"""
Wallet Monitor – runs every POLL_INTERVAL_MINUTES.

Detects new transactions for the configured wallet address and sends a
Discord notification with: shortened TX ID (linked to explorer), wallet
balance with change indicator, and transaction amount.
"""

import logging
from src.config import Config
from src.database import Database
from src.discord_client import DiscordClient, COLOR_GREEN, COLOR_RED, COLOR_GRAY
from src.flux_api import get_wallet_balance, get_wallet_transactions
from src.formatters import (
    shorten_tx,
    explorer_tx_url,
    explorer_addr_url,
    fmt_change,
    ts_to_utc,
)

logger = logging.getLogger(__name__)


class WalletMonitor:
    def __init__(self, config: Config, db: Database, discord: DiscordClient):
        self.config = config
        self.db = db
        self.discord = discord

    def check(self):
        if not self.config.wallet_notifications or not self.config.wallet:
            return

        address = self.config.wallet
        logger.info("Wallet check: %s", address)

        balance = get_wallet_balance(address)
        transactions = get_wallet_transactions(address, count=20)

        if balance is None:
            logger.warning("Could not fetch balance for %s", address)
            return

        prev_state = self.db.get_wallet_state(address)
        prev_balance = prev_state["balance"] if prev_state else None
        is_first_run = prev_state is None

        # Collect truly new (unseen) transactions
        new_txs = []
        for tx in transactions:
            tx_id = tx.get("txid", "")
            if tx_id and not self.db.is_tx_seen(tx_id):
                new_txs.append(tx)

        # Update wallet state
        latest_tx_id = transactions[0].get("txid", "") if transactions else ""
        self.db.set_wallet_state(address, balance, latest_tx_id)

        # Mark all fetched txs as seen so we don't re-alert
        for tx in transactions:
            tx_id = tx.get("txid", "")
            if tx_id:
                self.db.mark_tx_seen(tx_id, address)

        if is_first_run:
            logger.info("Wallet %s initialised – balance %.4f FLUX", address, balance)
            return

        if not new_txs:
            logger.debug("No new transactions for %s", address)
            return

        # Notify for each new tx (cap at 5 per poll to avoid webhook floods)
        for tx in new_txs[:5]:
            self._notify_tx(address, tx, balance, prev_balance)

    # ── Private ────────────────────────────────────────────────────────────────

    def _notify_tx(
        self,
        address: str,
        tx: dict,
        balance: float,
        prev_balance: float | None,
    ):
        tx_id = tx.get("txid", "")
        amount = tx.get("amount")
        tx_time = tx.get("time", 0)

        short_id = shorten_tx(tx_id)
        tx_url = explorer_tx_url(tx_id)
        addr_url = explorer_addr_url(address)

        balance_str = fmt_change(balance, prev_balance, " FLUX", decimals=4)

        # Choose colour by whether balance went up or down
        color = COLOR_GRAY
        if prev_balance is not None:
            color = COLOR_GREEN if balance >= prev_balance else COLOR_RED

        fields = [
            {
                "name": "🔗 Transaction ID",
                "value": f"[`{short_id}`]({tx_url})",
                "inline": False,
            },
            {
                "name": "💰 Wallet Balance",
                "value": balance_str,
                "inline": True,
            },
        ]

        if amount is not None:
            try:
                amt_f = float(amount)
                amt_str = f"**+{amt_f:.4f}**" if amt_f >= 0 else f"**{amt_f:.4f}**"
                fields.insert(1, {"name": "💵 Amount", "value": f"{amt_str} FLUX", "inline": True})
            except (TypeError, ValueError):
                pass

        if tx_time:
            fields.append({"name": "🕐 Time", "value": ts_to_utc(tx_time), "inline": True})

        fields.append(
            {
                "name": "📍 Address",
                "value": f"[`{address[:20]}…`]({addr_url})",
                "inline": False,
            }
        )

        embed = self.discord.build_embed(
            title="💳 Wallet Activity Detected",
            color=color,
            fields=fields,
            footer="Fluxcord • Wallet Monitor",
        )
        self.discord.send_embed(embed, mention=True)
