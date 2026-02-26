"""Tests for src/flux_api.py (all HTTP calls mocked via responses library)."""

import pytest
import responses as resp_lib
from src import flux_api


BASE = flux_api.FLUX_API_BASE


def _success(data):
    return {"status": "success", "data": data}


class TestGetNodeList:
    @resp_lib.activate
    def test_returns_list(self):
        nodes = [{"ip": "1.2.3.4", "tier": "CUMULUS", "rank": 1, "payment_address": "t1abc"}]
        resp_lib.add(
            resp_lib.GET,
            f"{BASE}/daemon/viewdeterministiczelnodelist",
            json=_success(nodes),
        )
        result = flux_api.get_node_list()
        assert result == nodes

    @resp_lib.activate
    def test_returns_empty_on_error(self):
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/viewdeterministiczelnodelist", status=500)
        assert flux_api.get_node_list() == []

    @resp_lib.activate
    def test_returns_empty_on_non_success_status(self):
        resp_lib.add(
            resp_lib.GET,
            f"{BASE}/daemon/viewdeterministiczelnodelist",
            json={"status": "error", "message": "oops"},
        )
        assert flux_api.get_node_list() == []


class TestGetNodesForAddress:
    @resp_lib.activate
    def test_filters_by_payment_address(self):
        nodes = [
            {"ip": "1.1.1.1", "payment_address": "t1owner", "tier": "CUMULUS", "rank": 1},
            {"ip": "2.2.2.2", "payment_address": "t1other", "tier": "NIMBUS", "rank": 5},
        ]
        resp_lib.add(
            resp_lib.GET,
            f"{BASE}/daemon/viewdeterministiczelnodelist",
            json=_success(nodes),
        )
        result = flux_api.get_nodes_for_address("t1owner")
        assert len(result) == 1
        assert result[0]["ip"] == "1.1.1.1"

    @resp_lib.activate
    def test_no_match_returns_empty(self):
        resp_lib.add(
            resp_lib.GET,
            f"{BASE}/daemon/viewdeterministiczelnodelist",
            json=_success([]),
        )
        assert flux_api.get_nodes_for_address("t1nobody") == []


class TestGetDosList:
    @resp_lib.activate
    def test_returns_dos_entries(self):
        dos = [{"payment_address": "t1bad", "collateral": "COutPoint(abc,0)"}]
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/getdoslist", json=_success(dos))
        result = flux_api.get_dos_list()
        assert result == dos

    @resp_lib.activate
    def test_returns_none_on_failure(self):
        """API failure returns None (distinct from empty list = no DOS nodes)."""
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/getdoslist", status=503)
        assert flux_api.get_dos_list() is None


class TestGetNodeCount:
    @resp_lib.activate
    def test_returns_counts_dict(self):
        counts = {"total": 7000, "cumulus-enabled": 4000, "nimbus-enabled": 1500, "stratus-enabled": 1500}
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/getzelnodecount", json=_success(counts))
        result = flux_api.get_node_count()
        assert result["total"] == 7000

    @resp_lib.activate
    def test_returns_empty_on_failure(self):
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/getzelnodecount", status=500)
        assert flux_api.get_node_count() == {}


class TestGetBlockCount:
    @resp_lib.activate
    def test_returns_integer(self):
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/getblockcount", json=_success(2370000))
        assert flux_api.get_block_count() == 2370000

    @resp_lib.activate
    def test_returns_zero_on_failure(self):
        resp_lib.add(resp_lib.GET, f"{BASE}/daemon/getblockcount", status=500)
        assert flux_api.get_block_count() == 0


class TestGetWalletBalance:
    @resp_lib.activate
    def test_primary_endpoint_converts_satoshis(self):
        # Primary returns satoshis as integer
        resp_lib.add(
            resp_lib.GET,
            f"{BASE}/explorer/balance",
            json=_success(4007200000000),
        )
        balance = flux_api.get_wallet_balance("t1abc")
        assert balance == pytest.approx(40072.0)

    @resp_lib.activate
    def test_fallback_to_insight(self):
        # Primary fails, insight returns balance already in FLUX
        resp_lib.add(resp_lib.GET, f"{BASE}/explorer/balance", json={"status": "error"})
        resp_lib.add(
            resp_lib.GET,
            f"{flux_api.FLUX_EXPLORER_INSIGHT}/addr/t1abc",
            json={"balance": 999.0, "balanceSat": 99900000000},
        )
        balance = flux_api.get_wallet_balance("t1abc")
        assert balance == pytest.approx(999.0)

    @resp_lib.activate
    def test_returns_none_on_both_fail(self):
        resp_lib.add(resp_lib.GET, f"{BASE}/explorer/balance", status=500)
        resp_lib.add(
            resp_lib.GET,
            f"{flux_api.FLUX_EXPLORER_INSIGHT}/addr/t1abc",
            status=500,
        )
        assert flux_api.get_wallet_balance("t1abc") is None


class TestGetWalletTransactions:
    @resp_lib.activate
    def test_insight_addrs_endpoint(self):
        insight_resp = {
            "totalItems": 1,
            "from": 0,
            "to": 1,
            "items": [
                {
                    "txid": "def456",
                    "time": 1700000001,
                    "vout": [
                        {
                            "value": "9.00000000",
                            "scriptPubKey": {"addresses": ["t1abc"]},
                        }
                    ],
                    "vin": [],
                }
            ],
        }
        resp_lib.add(
            resp_lib.GET,
            f"{flux_api.FLUX_EXPLORER_INSIGHT}/addrs/t1abc/txs",
            json=insight_resp,
        )
        result = flux_api.get_wallet_transactions("t1abc")
        assert len(result) == 1
        assert result[0]["txid"] == "def456"
        assert result[0]["amount"] == pytest.approx(9.0)

    @resp_lib.activate
    def test_fallback_to_primary_txids(self):
        # Insight fails, primary returns txids-only list
        resp_lib.add(
            resp_lib.GET,
            f"{flux_api.FLUX_EXPLORER_INSIGHT}/addrs/t1abc/txs",
            status=500,
        )
        resp_lib.add(
            resp_lib.GET,
            f"{BASE}/explorer/transactions",
            json=_success([{"txid": "abc123"}, {"txid": "abc456"}]),
        )
        result = flux_api.get_wallet_transactions("t1abc")
        assert len(result) == 2
        assert result[0]["txid"] == "abc123"

    @resp_lib.activate
    def test_returns_empty_on_failure(self):
        resp_lib.add(
            resp_lib.GET,
            f"{flux_api.FLUX_EXPLORER_INSIGHT}/addrs/t1abc/txs",
            status=500,
        )
        resp_lib.add(resp_lib.GET, f"{BASE}/explorer/transactions", status=500)
        assert flux_api.get_wallet_transactions("t1abc") == []


class TestNodeApis:
    @resp_lib.activate
    def test_get_node_info(self):
        resp_lib.add(
            resp_lib.GET,
            "http://1.2.3.4:16127/flux/info",
            json=_success({"version": "4.21.0", "uptime": 12345}),
        )
        result = flux_api.get_node_info("1.2.3.4")
        assert result["version"] == "4.21.0"

    @resp_lib.activate
    def test_get_node_info_timeout(self):
        import responses as r
        r.add(r.GET, "http://1.2.3.4:16127/flux/info", body=Exception("Connection timeout"))
        result = flux_api.get_node_info("1.2.3.4", timeout=1)
        assert result is None

    @resp_lib.activate
    def test_get_node_benchmark(self):
        bench = {"benchmark": {"status": "ok", "cpu": "8", "ram": "16384", "eps": "1234"}}
        resp_lib.add(
            resp_lib.GET,
            "http://1.2.3.4:16127/benchmark/getinfo",
            json=_success(bench),
        )
        result = flux_api.get_node_benchmark("1.2.3.4")
        assert result["benchmark"]["status"] == "ok"

    @resp_lib.activate
    def test_get_node_installed_apps(self):
        apps = [{"name": "MyApp", "cpu": 1.0, "ram": 512}]
        resp_lib.add(
            resp_lib.GET,
            "http://1.2.3.4:16127/apps/installedapps",
            json=_success(apps),
        )
        result = flux_api.get_node_installed_apps("1.2.3.4")
        assert result == apps

    @resp_lib.activate
    def test_get_node_installed_apps_none_on_fail(self):
        """API failure returns None (distinct from empty list = no apps installed)."""
        resp_lib.add(resp_lib.GET, "http://1.2.3.4:16127/apps/installedapps", status=500)
        assert flux_api.get_node_installed_apps("1.2.3.4") is None
