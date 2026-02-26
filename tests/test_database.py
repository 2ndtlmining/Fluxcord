"""Tests for src/database.py"""

import os
import pytest
import tempfile
from src.database import Database


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    d = Database(path)
    yield d
    os.unlink(path)


class TestWalletState:
    def test_get_nonexistent(self, db):
        assert db.get_wallet_state("t1abc") is None

    def test_set_and_get(self, db):
        db.set_wallet_state("t1abc", 1234.5, "tx123")
        state = db.get_wallet_state("t1abc")
        assert state is not None
        assert state["balance"] == pytest.approx(1234.5)
        assert state["last_tx_id"] == "tx123"
        assert state["address"] == "t1abc"

    def test_update(self, db):
        db.set_wallet_state("t1abc", 100.0, "tx1")
        db.set_wallet_state("t1abc", 200.0, "tx2")
        state = db.get_wallet_state("t1abc")
        assert state["balance"] == pytest.approx(200.0)
        assert state["last_tx_id"] == "tx2"


class TestSeenTransactions:
    def test_not_seen(self, db):
        assert db.is_tx_seen("tx999") is False

    def test_mark_and_check(self, db):
        db.mark_tx_seen("tx001", "t1addr")
        assert db.is_tx_seen("tx001") is True

    def test_mark_idempotent(self, db):
        db.mark_tx_seen("tx001", "t1addr")
        db.mark_tx_seen("tx001", "t1addr")  # should not raise
        assert db.is_tx_seen("tx001") is True


class TestNodeIps:
    def _make_node(self, ip, tier="CUMULUS", rank=100):
        return {"ip": ip, "tier": tier, "rank": rank, "payment_address": "t1owner"}

    def test_first_update_all_added(self, db):
        nodes = [self._make_node("1.1.1.1"), self._make_node("2.2.2.2")]
        added, removed = db.update_node_ips("t1owner", nodes)
        assert added == {"1.1.1.1", "2.2.2.2"}
        assert removed == set()

    def test_no_change(self, db):
        nodes = [self._make_node("1.1.1.1")]
        db.update_node_ips("t1owner", nodes)
        added, removed = db.update_node_ips("t1owner", nodes)
        assert added == set()
        assert removed == set()

    def test_node_removed(self, db):
        nodes = [self._make_node("1.1.1.1"), self._make_node("2.2.2.2")]
        db.update_node_ips("t1owner", nodes)
        added, removed = db.update_node_ips("t1owner", [self._make_node("1.1.1.1")])
        assert added == set()
        assert removed == {"2.2.2.2"}

    def test_node_added(self, db):
        db.update_node_ips("t1owner", [self._make_node("1.1.1.1")])
        added, removed = db.update_node_ips(
            "t1owner", [self._make_node("1.1.1.1"), self._make_node("3.3.3.3")]
        )
        assert added == {"3.3.3.3"}
        assert removed == set()

    def test_empty_ip_ignored(self, db):
        nodes = [{"ip": "", "tier": "CUMULUS", "rank": 1, "payment_address": "t1owner"}]
        added, removed = db.update_node_ips("t1owner", nodes)
        assert added == set()


class TestNodeApps:
    def _make_app(self, name):
        return {"name": name, "cpu": 0.5, "ram": 512}

    def test_first_scan_no_new(self, db):
        apps = [self._make_app("appA"), self._make_app("appB")]
        new = db.update_node_apps("1.1.1.1", apps)
        # First run → stored but returned as "new" since nothing was there before
        assert new == {"appA", "appB"}

    def test_subsequent_same_no_new(self, db):
        apps = [self._make_app("appA")]
        db.update_node_apps("1.1.1.1", apps)
        new = db.update_node_apps("1.1.1.1", apps)
        assert new == set()

    def test_new_app_detected(self, db):
        db.update_node_apps("1.1.1.1", [self._make_app("appA")])
        new = db.update_node_apps("1.1.1.1", [self._make_app("appA"), self._make_app("appB")])
        assert new == {"appB"}

    def test_removed_app(self, db):
        db.update_node_apps("1.1.1.1", [self._make_app("appA"), self._make_app("appB")])
        new = db.update_node_apps("1.1.1.1", [self._make_app("appA")])
        assert new == set()  # no new apps, just removal


class TestKvState:
    def test_get_nonexistent(self, db):
        assert db.get_state("test_bucket", "missing_key") is None

    def test_set_and_get_string(self, db):
        db.set_state("test_bucket", "key1", "hello")
        assert db.get_state("test_bucket", "key1") == "hello"

    def test_set_and_get_dict(self, db):
        data = {"total": 100, "cumulus-enabled": 50}
        db.set_state("test_bucket", "counts", data)
        result = db.get_state("test_bucket", "counts")
        assert result == data

    def test_set_and_get_int(self, db):
        db.set_state("test_bucket", "rank", 1234)
        assert db.get_state("test_bucket", "rank") == 1234

    def test_update_existing(self, db):
        db.set_state("test_bucket", "key1", "v1")
        db.set_state("test_bucket", "key1", "v2")
        assert db.get_state("test_bucket", "key1") == "v2"

    def test_different_buckets_isolated(self, db):
        db.set_state("bucket_a", "k", "val_a")
        db.set_state("bucket_b", "k", "val_b")
        assert db.get_state("bucket_a", "k") == "val_a"
        assert db.get_state("bucket_b", "k") == "val_b"
