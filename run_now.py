"""
run_now.py – One-shot test runner for Fluxcord.

Runs all monitor checks immediately then exits (no scheduler loop).
Set env vars before running:

  Windows PowerShell:
    $env:DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
    $env:WALLET="t1..."
    $env:WALLET_NOTIFICATIONS="true"
    $env:NODE_WALLET_NOTIFICATIONS="true"
    $env:NETWORK_NOTIFICATIONS="true"
    $env:DB_PATH="./test.db"
    python run_now.py

  Unix:
    DISCORD_WEBHOOK="..." WALLET="t1..." python run_now.py

Flags (pass as CLI args):
  --wallet      run wallet check only
  --node        run 5-min node check only
  --node-daily  run daily node overview only
  --network     run daily network check only
  --test-dos    simulate a DOS alert for one of your nodes (for testing)
  (no flags = run everything)
"""

import logging
import sys
import os

# Ensure src is importable from project root
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

from src.config import Config
from src.database import Database
from src.discord_client import DiscordClient
from src.flux_api import get_nodes_for_address
from src.monitors.wallet_monitor import WalletMonitor
from src.monitors.node_monitor import NodeMonitor
from src.monitors.network_monitor import NetworkMonitor


def main():
    args = set(sys.argv[1:])
    run_all = not args - {"--test-dos"}  # --test-dos is extra, not a "run all" trigger

    config = Config()
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"[ERROR] {e}")
        sys.exit(1)

    db = Database(config.db_path)
    discord = DiscordClient(config.discord_webhook, config.discord_user)

    wallet_mon  = WalletMonitor(config, db, discord)
    node_mon    = NodeMonitor(config, db, discord)
    network_mon = NetworkMonitor(config, db, discord)

    if run_all or "--wallet" in args:
        print("\n-- Wallet check ------------------------------------------")
        wallet_mon.check()

    if run_all or "--node" in args:
        print("\n-- Node 5-min check --------------------------------------")
        node_mon.check_5min()

    if run_all or "--node-daily" in args:
        print("\n-- Node daily overview -----------------------------------")
        node_mon.check_daily()

    if run_all or "--network" in args:
        print("\n-- Network daily check -----------------------------------")
        network_mon.check_daily()

    if "--test-dos" in args:
        print("\n-- DOS alert test ----------------------------------------")
        _test_dos(config, node_mon)

    print("\nDone.")


def _test_dos(config, node_mon: "NodeMonitor"):
    """Simulate a DOS alert by pretending the first found node is in the DOS list."""
    import src.monitors.node_monitor as nm_mod

    nodes = get_nodes_for_address(config.wallet)
    if not nodes:
        print("[test-dos] No nodes found for wallet, cannot simulate DOS.")
        return

    # Build a fake DOS list using the payment_address of the first node
    target = nodes[0]
    fake_dos = [{"payment_address": target.get("payment_address", "")}]
    print(f"[test-dos] Simulating DOS for node {target.get('ip')} / {target.get('payment_address')}")

    # Patch the reference inside node_monitor's own namespace
    original = nm_mod.get_dos_list
    nm_mod.get_dos_list = lambda: fake_dos
    try:
        node_mon._check_dos(nodes)
    finally:
        nm_mod.get_dos_list = original


if __name__ == "__main__":
    main()
