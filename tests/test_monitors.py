"""
Integration-style tests for the monitor classes.
All external calls (Flux API, Discord webhook) are mocked.
"""

import os
import pytest
import tempfile
from unittest.mock import MagicMock, patch, call

from src.config import Config
from src.database import Database
from src.discord_client import DiscordClient
from src.monitors.wallet_monitor import WalletMonitor
from src.monitors.node_monitor import NodeMonitor
from src.monitors.network_monitor import NetworkMonitor


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    d = Database(path)
    yield d
    os.unlink(path)


@pytest.fixture
def config():
    cfg = Config.__new__(Config)
    cfg.wallet = "t1testWallet"
    cfg.wallet_notifications = True
    cfg.node_wallet = "t1nodeWallet"
    cfg.node_wallet_notifications = True
    cfg.network_notifications = True
    cfg.discord_webhook = "https://discord.com/api/webhooks/fake"
    cfg.discord_user = "123456789"
    cfg.db_path = ":memory:"
    cfg.poll_interval = 5
    cfg.daily_hour_utc = 8
    cfg.node_request_timeout = 5
    cfg.api_request_timeout = 30
    cfg.new_apps_block_window = 2880
    return cfg


@pytest.fixture
def discord():
    client = MagicMock(spec=DiscordClient)
    client.build_embed.return_value = {"title": "test", "color": 0}
    client.send_embed.return_value = True
    client.send_embeds.return_value = True
    return client


# ── WalletMonitor ──────────────────────────────────────────────────────────────

class TestWalletMonitor:
    def test_skips_when_disabled(self, config, db, discord):
        config.wallet_notifications = False
        mon = WalletMonitor(config, db, discord)
        with patch("src.monitors.wallet_monitor.get_wallet_balance") as mock_bal:
            mon.check()
            mock_bal.assert_not_called()
            discord.send_embed.assert_not_called()

    def test_initialises_on_first_run(self, config, db, discord):
        mon = WalletMonitor(config, db, discord)
        with (
            patch("src.monitors.wallet_monitor.get_wallet_balance", return_value=500.0),
            patch("src.monitors.wallet_monitor.get_wallet_transactions", return_value=[]),
        ):
            mon.check()
            # First run should NOT send a notification (just initialise state)
            discord.send_embed.assert_not_called()

    def test_sends_notification_on_new_tx(self, config, db, discord):
        mon = WalletMonitor(config, db, discord)

        txs = [{"txid": "tx001", "time": 1700000000, "amount": 10.0}]

        # First run – initialise
        with (
            patch("src.monitors.wallet_monitor.get_wallet_balance", return_value=100.0),
            patch("src.monitors.wallet_monitor.get_wallet_transactions", return_value=txs),
        ):
            mon.check()

        discord.send_embed.assert_not_called()

        # Second run – new transaction arrives
        new_txs = [
            {"txid": "tx002", "time": 1700000060, "amount": 5.0},
            {"txid": "tx001", "time": 1700000000, "amount": 10.0},
        ]
        with (
            patch("src.monitors.wallet_monitor.get_wallet_balance", return_value=105.0),
            patch("src.monitors.wallet_monitor.get_wallet_transactions", return_value=new_txs),
        ):
            mon.check()

        discord.send_embed.assert_called_once()
        call_kwargs = discord.send_embed.call_args
        assert call_kwargs[1].get("mention") is True or call_kwargs[0][1] is True

    def test_no_notification_when_balance_api_fails(self, config, db, discord):
        mon = WalletMonitor(config, db, discord)
        with (
            patch("src.monitors.wallet_monitor.get_wallet_balance", return_value=None),
            patch("src.monitors.wallet_monitor.get_wallet_transactions", return_value=[]),
        ):
            mon.check()
            discord.send_embed.assert_not_called()

    def test_does_not_double_notify_same_tx(self, config, db, discord):
        mon = WalletMonitor(config, db, discord)
        txs = [{"txid": "tx001", "time": 1700000000, "amount": 10.0}]

        # init
        with (
            patch("src.monitors.wallet_monitor.get_wallet_balance", return_value=100.0),
            patch("src.monitors.wallet_monitor.get_wallet_transactions", return_value=txs),
        ):
            mon.check()

        # Mark tx001 as seen; second run should not notify
        with (
            patch("src.monitors.wallet_monitor.get_wallet_balance", return_value=100.0),
            patch("src.monitors.wallet_monitor.get_wallet_transactions", return_value=txs),
        ):
            mon.check()

        discord.send_embed.assert_not_called()


# ── NodeMonitor ────────────────────────────────────────────────────────────────

class TestNodeMonitorDos:
    def _make_node(self, ip="1.1.1.1", tier="CUMULUS", rank=100, addr="t1nodeWallet"):
        return {"ip": ip, "tier": tier, "rank": rank, "payment_address": addr}

    def test_no_alert_when_not_in_dos(self, config, db, discord):
        mon = NodeMonitor(config, db, discord)
        nodes = [self._make_node()]
        dos = [{"payment_address": "t1somebody_else"}]

        with (
            patch("src.monitors.node_monitor.get_nodes_for_address", return_value=nodes),
            patch("src.monitors.node_monitor.get_dos_list", return_value=dos),
            patch("src.monitors.node_monitor.get_node_installed_apps", return_value=[]),
        ):
            mon.check_5min()

        # No DOS embed sent (IP change embed may be sent on first run)
        calls = [str(c) for c in discord.send_embed.call_args_list]
        assert not any("DOS" in c for c in calls)

    def test_alert_when_in_dos(self, config, db, discord):
        mon = NodeMonitor(config, db, discord)
        nodes = [self._make_node(addr="t1nodeWallet")]
        dos = [{"payment_address": "t1nodeWallet"}]

        with (
            patch("src.monitors.node_monitor.get_nodes_for_address", return_value=nodes),
            patch("src.monitors.node_monitor.get_dos_list", return_value=dos),
            patch("src.monitors.node_monitor.get_node_installed_apps", return_value=[]),
        ):
            mon.check_5min()

        # build_embed should have been called with a DOS title
        titles = [c[1].get("title", "") or c[0][0].get("title", "")
                  for c in discord.build_embed.call_args_list]
        assert any("DOS" in t for t in titles)


class TestNodeMonitorIpChanges:
    def _make_node(self, ip, addr="t1nodeWallet"):
        return {"ip": ip, "tier": "CUMULUS", "rank": 1, "payment_address": addr}

    def test_added_ip_notification(self, config, db, discord):
        mon = NodeMonitor(config, db, discord)

        # First run: 1 node
        with (
            patch("src.monitors.node_monitor.get_nodes_for_address", return_value=[self._make_node("1.1.1.1")]),
            patch("src.monitors.node_monitor.get_dos_list", return_value=[]),
            patch("src.monitors.node_monitor.get_node_installed_apps", return_value=[]),
        ):
            mon.check_5min()

        discord.send_embed.reset_mock()
        discord.build_embed.reset_mock()

        # Second run: 2 nodes
        with (
            patch("src.monitors.node_monitor.get_nodes_for_address",
                  return_value=[self._make_node("1.1.1.1"), self._make_node("2.2.2.2")]),
            patch("src.monitors.node_monitor.get_dos_list", return_value=[]),
            patch("src.monitors.node_monitor.get_node_installed_apps", return_value=[]),
        ):
            mon.check_5min()

        titles = [c[1].get("title", "") for c in discord.build_embed.call_args_list]
        assert any("IP Changes" in t for t in titles)

    def test_removed_ip_notification(self, config, db, discord):
        mon = NodeMonitor(config, db, discord)

        with (
            patch("src.monitors.node_monitor.get_nodes_for_address",
                  return_value=[{"ip": "1.1.1.1", "tier": "CUMULUS", "rank": 1, "payment_address": "t1nodeWallet"},
                                 {"ip": "2.2.2.2", "tier": "CUMULUS", "rank": 2, "payment_address": "t1nodeWallet"}]),
            patch("src.monitors.node_monitor.get_dos_list", return_value=[]),
            patch("src.monitors.node_monitor.get_node_installed_apps", return_value=[]),
        ):
            mon.check_5min()

        discord.build_embed.reset_mock()

        with (
            patch("src.monitors.node_monitor.get_nodes_for_address",
                  return_value=[{"ip": "1.1.1.1", "tier": "CUMULUS", "rank": 1, "payment_address": "t1nodeWallet"}]),
            patch("src.monitors.node_monitor.get_dos_list", return_value=[]),
            patch("src.monitors.node_monitor.get_node_installed_apps", return_value=[]),
        ):
            mon.check_5min()

        titles = [c[1].get("title", "") for c in discord.build_embed.call_args_list]
        assert any("IP Changes" in t for t in titles)


class TestNodeMonitorNewApps:
    def _make_node(self, ip="1.1.1.1"):
        return {"ip": ip, "tier": "CUMULUS", "rank": 1, "payment_address": "t1nodeWallet"}

    def test_new_app_notification(self, config, db, discord):
        mon = NodeMonitor(config, db, discord)
        node = self._make_node()

        # First run – initialise with appA
        with (
            patch("src.monitors.node_monitor.get_nodes_for_address", return_value=[node]),
            patch("src.monitors.node_monitor.get_dos_list", return_value=[]),
            patch("src.monitors.node_monitor.get_node_installed_apps",
                  return_value=[{"name": "appA", "cpu": 0.5, "ram": 256}]),
        ):
            mon.check_5min()

        discord.build_embed.reset_mock()

        # Second run – appB added
        with (
            patch("src.monitors.node_monitor.get_nodes_for_address", return_value=[node]),
            patch("src.monitors.node_monitor.get_dos_list", return_value=[]),
            patch("src.monitors.node_monitor.get_node_installed_apps",
                  return_value=[
                      {"name": "appA", "cpu": 0.5, "ram": 256},
                      {"name": "appB", "cpu": 1.0, "ram": 512},
                  ]),
        ):
            mon.check_5min()

        titles = [c[1].get("title", "") for c in discord.build_embed.call_args_list]
        assert any("New App" in t for t in titles)


# ── NetworkMonitor ─────────────────────────────────────────────────────────────

_NET_PATCHES = {
    "src.monitors.network_monitor.get_node_list": [],
    "src.monitors.network_monitor.get_arcane_os_percentage": None,
}


def _net_ctx(counts, block_height, apps, arcane_pct=None):
    """Return a context manager stack patching all network monitor dependencies."""
    from contextlib import ExitStack
    from unittest.mock import patch

    stack = ExitStack()
    stack.enter_context(patch("src.monitors.network_monitor.get_node_count", return_value=counts))
    stack.enter_context(patch("src.monitors.network_monitor.get_arcane_os_percentage", return_value=arcane_pct))
    stack.enter_context(patch("src.monitors.network_monitor.get_block_count", return_value=block_height))
    stack.enter_context(patch("src.monitors.network_monitor.get_global_apps", return_value=apps))
    return stack


class TestNetworkMonitor:
    _COUNTS = {"total": 7906, "cumulus-enabled": 4543, "nimbus-enabled": 1754, "stratus-enabled": 1609}

    def test_skips_when_disabled(self, config, db, discord):
        config.network_notifications = False
        mon = NetworkMonitor(config, db, discord)
        with patch("src.monitors.network_monitor.get_node_count") as mock_count:
            mon.check_daily()
            mock_count.assert_not_called()

    def test_sends_node_counts(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        with _net_ctx(self._COUNTS, 2371164, []):
            mon.check_daily()
        titles = [c[1].get("title", "") for c in discord.build_embed.call_args_list]
        assert any("Node Count" in t for t in titles)

    def test_arcane_os_shown_when_available(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        with _net_ctx(self._COUNTS, 2371164, [], arcane_pct=67.3):
            mon.check_daily()
        all_fields = [f for c in discord.build_embed.call_args_list for f in (c[1].get("fields") or [])]
        field_names = [f["name"] for f in all_fields]
        assert any("Arcane" in n for n in field_names)

    def test_arcane_os_hidden_when_unavailable(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        with _net_ctx(self._COUNTS, 2371164, [], arcane_pct=None):
            mon.check_daily()
        all_fields = [f for c in discord.build_embed.call_args_list for f in (c[1].get("fields") or [])]
        field_names = [f["name"] for f in all_fields]
        assert not any("Arcane" in n for n in field_names)

    def test_sends_new_apps_as_table(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        current_height = 2371164
        apps = [
            {
                "name": "TestApp",
                "height": current_height - 100,
                "instances": 3,
                "owner": "t1owner",
                "repotag": "docker/testapp:latest",
                "cpu": 0.5,
                "ram": 512,
                "hdd": 10,
                "compose": [],
            }
        ]
        with _net_ctx(self._COUNTS, current_height, apps):
            mon.check_daily()

        # App should appear inside a table (description), not as embed title
        descriptions = [c[1].get("description", "") or "" for c in discord.build_embed.call_args_list]
        assert any("TestApp" in d for d in descriptions)

    def test_app_table_contains_type_and_resources(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        current_height = 2371164
        apps = [
            # Docker: repotag does not contain runonflux/orbit
            {"name": "DockerApp", "height": current_height - 50, "instances": 2, "owner": "t1x",
             "repotag": "img:latest", "cpu": 1.0, "ram": 1024, "hdd": 20, "compose": []},
            # Git: compose component has runonflux/orbit repotag
            {"name": "GitApp", "height": current_height - 60, "instances": 1, "owner": "t1y",
             "cpu": 0.5, "ram": 256, "hdd": 5,
             "compose": [{"repotag": "runonflux/orbit:latest", "cpu": 0.5, "ram": 256, "hdd": 5}]},
        ]
        with _net_ctx(self._COUNTS, current_height, apps):
            mon.check_daily()

        descriptions = " ".join(c[1].get("description", "") or "" for c in discord.build_embed.call_args_list)
        assert "DockerApp" in descriptions
        assert "GitApp" in descriptions
        assert " D " in descriptions   # Docker type marker
        assert " G " in descriptions   # Git type marker

    def test_app_table_ram_always_in_gb(self, config, db, discord):
        """RAM should always display in GB (e.g. 0.5G not 512M), CPU to 1dp."""
        mon = NetworkMonitor(config, db, discord)
        current_height = 2371164
        apps = [
            {"name": "SmallApp", "height": current_height - 10, "instances": 1, "owner": "t1x",
             "repotag": "img:latest", "cpu": 0.25, "ram": 512, "hdd": 5, "compose": []},
        ]
        with _net_ctx(self._COUNTS, current_height, apps):
            mon.check_daily()

        descriptions = " ".join(c[1].get("description", "") or "" for c in discord.build_embed.call_args_list)
        assert "0.5G" in descriptions   # 512 MB → 0.5G
        assert "0.2" in descriptions    # 0.25 cpu → 0.2 or 0.3 (rounded 1dp)
        assert "M" not in descriptions.split("SmallApp")[1].split("\n")[0]  # no MB in data rows

    def test_no_new_apps_message(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        old_app = {"name": "OldApp", "height": 1, "owner": "t1x", "compose": []}
        with _net_ctx(self._COUNTS, 2371164, [old_app]):
            mon.check_daily()
        titles = [c[1].get("title", "") for c in discord.build_embed.call_args_list]
        assert any("No new apps" in t or "Daily New Apps" in t for t in titles)

    def test_change_indicators_on_second_run(self, config, db, discord):
        mon = NetworkMonitor(config, db, discord)
        first  = {"total": 7000, "cumulus-enabled": 4000, "nimbus-enabled": 1500, "stratus-enabled": 1500}
        second = {"total": 7100, "cumulus-enabled": 4100, "nimbus-enabled": 1500, "stratus-enabled": 1500}

        with _net_ctx(first, 2371164, []):
            mon.check_daily()

        discord.build_embed.reset_mock()

        with _net_ctx(second, 2371165, []):
            mon.check_daily()

        fields_combined = " ".join(
            str(c[1].get("fields", [])) for c in discord.build_embed.call_args_list
        )
        assert "🟢" in fields_combined or "+100" in fields_combined


# ── _is_git_app unit tests ─────────────────────────────────────────────────────

class TestIsGitApp:
    """Unit tests for the runonflux/orbit Git detection logic."""

    def _run(self, app):
        from src.monitors.network_monitor import _is_git_app
        return _is_git_app(app)

    def test_docker_top_level_repotag(self):
        assert self._run({"repotag": "someimage:latest", "compose": []}) is False

    def test_git_top_level_repotag(self):
        assert self._run({"repotag": "runonflux/orbit:latest", "compose": []}) is True

    def test_git_top_level_case_insensitive(self):
        assert self._run({"repotag": "RunOnFlux/Orbit:dev", "compose": []}) is True

    def test_docker_compose(self):
        app = {"compose": [{"repotag": "nginx:latest"}, {"repotag": "redis:alpine"}]}
        assert self._run(app) is False

    def test_git_compose_any_component(self):
        app = {"compose": [
            {"repotag": "nginx:latest"},
            {"repotag": "runonflux/orbit:latest"},
        ]}
        assert self._run(app) is True

    def test_docker_no_repotag(self):
        assert self._run({"compose": []}) is False

    def test_git_substring_match(self):
        # Fluxtracker uses a simple substring includes() check,
        # so any repotag containing "runonflux/orbit" counts as Git.
        assert self._run({"repotag": "runonflux/orbit:dev"}) is True
        assert self._run({"repotag": "runonflux/orbit:latest"}) is True

    def test_docker_unrelated_runonflux_image(self):
        # A different runonflux image that doesn't contain "orbit"
        assert self._run({"repotag": "runonflux/flux:latest", "compose": []}) is False
