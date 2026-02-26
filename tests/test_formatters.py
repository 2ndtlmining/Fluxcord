"""Tests for src/formatters.py"""

import pytest
from src.formatters import (
    fmt_change,
    fmt_int_change,
    fmt_pct_change,
    fmt_time_from_rank,
    shorten_tx,
    explorer_tx_url,
    explorer_addr_url,
    tier_label,
    fmt_resources,
    ts_to_ago,
    ts_to_utc,
)


class TestFmtChange:
    def test_no_previous(self):
        result = fmt_change(100.5, None)
        assert "100.5000" in result
        assert "🟢" not in result
        assert "🔴" not in result

    def test_increase(self):
        result = fmt_change(110.0, 100.0, " FLUX")
        assert "110.0000" in result
        assert "🟢" in result
        assert "+10" in result

    def test_decrease(self):
        result = fmt_change(90.0, 100.0, " FLUX")
        assert "90.0000" in result
        assert "🔴" in result
        assert "-10" in result

    def test_no_change(self):
        result = fmt_change(100.0, 100.0)
        assert "100.0000" in result
        assert "🟢" not in result
        assert "🔴" not in result

    def test_custom_decimals(self):
        result = fmt_change(1.5, 1.0, decimals=2)
        assert "1.50" in result


class TestFmtIntChange:
    def test_no_previous(self):
        result = fmt_int_change(1000)
        assert "1,000" in result
        assert "🟢" not in result

    def test_increase(self):
        result = fmt_int_change(1243, 1231)
        assert "1,243" in result
        assert "🟢" in result
        assert "+12" in result

    def test_decrease(self):
        result = fmt_int_change(1232, 1243)
        assert "1,232" in result
        assert "🔴" in result
        assert "-11" in result

    def test_zero_change(self):
        result = fmt_int_change(500, 500)
        assert "500" in result
        assert "🟢" not in result
        assert "🔴" not in result

    def test_large_numbers(self):
        result = fmt_int_change(10000, 9000)
        assert "10,000" in result
        assert "+1,000" in result


class TestFmtPctChange:
    def test_increase(self):
        result = fmt_pct_change(55.5, 50.0)
        assert "55.5%" in result
        assert "🟢" in result

    def test_decrease(self):
        result = fmt_pct_change(44.5, 50.0)
        assert "44.5%" in result
        assert "🔴" in result

    def test_negligible_change(self):
        result = fmt_pct_change(50.01, 50.0)
        assert "🟢" not in result


class TestFmtTimeFromRank:
    def test_rank_zero(self):
        assert fmt_time_from_rank(0) == "Unknown"

    def test_minutes(self):
        # rank=10, block_time=90s → 900s → 15min
        result = fmt_time_from_rank(10, block_time_seconds=90)
        assert "15m" in result

    def test_hours(self):
        # rank=80, block_time=90s → 7200s → 2h
        result = fmt_time_from_rank(80, block_time_seconds=90)
        assert "2h" in result

    def test_days(self):
        # rank=1000, block_time=90s → 90000s → 25h → 1d 1h
        result = fmt_time_from_rank(1000, block_time_seconds=90)
        assert "d" in result


class TestShortenTx:
    def test_long_hash(self):
        tx = "a" * 64
        result = shorten_tx(tx)
        assert "..." in result
        assert len(result) < len(tx)

    def test_short_hash(self):
        tx = "abc123"
        assert shorten_tx(tx) == "abc123"

    def test_custom_lengths(self):
        tx = "a" * 64
        result = shorten_tx(tx, head=4, tail=4)
        assert result == "aaaa...aaaa"


class TestUrls:
    def test_explorer_tx_url(self):
        url = explorer_tx_url("abc123")
        assert "explorer.runonflux.io/tx/abc123" in url

    def test_explorer_addr_url(self):
        url = explorer_addr_url("t1abc")
        assert "explorer.runonflux.io/address/t1abc" in url


class TestTierLabel:
    def test_cumulus(self):
        assert "Cumulus" in tier_label("CUMULUS")

    def test_nimbus(self):
        assert "Nimbus" in tier_label("NIMBUS")

    def test_stratus(self):
        assert "Stratus" in tier_label("STRATUS")

    def test_unknown(self):
        result = tier_label("UNKNOWN")
        assert "Unknown" in result


class TestFmtResources:
    def test_output(self):
        result = fmt_resources(0.5, 512, 10)
        assert "0.5" in result
        assert "512" in result
        assert "10" in result
