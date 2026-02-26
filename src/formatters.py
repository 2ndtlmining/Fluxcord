"""Formatting helpers for Discord messages."""

from datetime import datetime, timezone


# ── Change indicators ──────────────────────────────────────────────────────────

def _change_str(diff: float, decimals: int, unit: str) -> str:
    if diff > 0:
        return f" 🟢 **(+{diff:.{decimals}f}{unit})**"
    if diff < 0:
        return f" 🔴 **({diff:.{decimals}f}{unit})**"
    return ""


def fmt_change(current: float, previous: float | None, unit: str = "", decimals: int = 4) -> str:
    """Format a float value with an optional +/- change indicator."""
    val = f"{current:.{decimals}f}{unit}"
    if previous is None:
        return val
    return val + _change_str(current - previous, decimals, unit)


def fmt_int_change(current: int, previous: int | None = None, unit: str = "") -> str:
    """Format an integer value with an optional +/- change indicator."""
    val = f"{current:,}{unit}"
    if previous is None:
        return val
    diff = current - previous
    if diff > 0:
        return f"{val} 🟢 **(+{diff:,}{unit})**"
    if diff < 0:
        return f"{val} 🔴 **({diff:,}{unit})**"
    return val


def fmt_pct_change(current: float, previous: float | None, decimals: int = 1) -> str:
    """Format a percentage with change indicator."""
    val = f"{current:.{decimals}f}%"
    if previous is None:
        return val
    diff = current - previous
    if abs(diff) < 0.05:
        return val
    if diff > 0:
        return f"{val} 🟢 **(+{diff:.{decimals}f}%)**"
    return f"{val} 🔴 **({diff:.{decimals}f}%)**"


# ── Time helpers ───────────────────────────────────────────────────────────────

def fmt_time_from_rank(rank: int, block_time_seconds: int = 90) -> str:
    """Estimate time to next reward from rank position.

    Flux block time ≈ 90 seconds. rank=1 means next in queue.
    """
    if rank <= 0:
        return "Unknown"
    total_seconds = rank * block_time_seconds
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hrs = divmod(hours, 24)
        return f"~{days}d {hrs}h"
    if hours > 0:
        return f"~{hours}h {minutes}m"
    return f"~{minutes}m"


def _to_float_ts(ts) -> float:
    """Coerce a timestamp value (int/float/str) to float. Returns 0.0 on failure."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def ts_to_ago(ts) -> str:
    """Human-readable time-ago from a Unix timestamp."""
    ts = _to_float_ts(ts)
    if not ts:
        return "Never"
    now = datetime.now(timezone.utc).timestamp()
    diff = max(0, now - ts)
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h {int((diff % 3600) / 60)}m ago"
    days = int(diff / 86400)
    hours = int((diff % 86400) / 3600)
    return f"{days}d {hours}h ago"


def ts_to_utc(ts) -> str:
    """Format Unix timestamp as UTC string."""
    ts = _to_float_ts(ts)
    if not ts:
        return "N/A"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── TX / address helpers ───────────────────────────────────────────────────────

def shorten_tx(tx_id: str, head: int = 8, tail: int = 8) -> str:
    if len(tx_id) > head + tail + 3:
        return f"{tx_id[:head]}...{tx_id[-tail:]}"
    return tx_id


def explorer_tx_url(tx_id: str) -> str:
    return f"https://explorer.runonflux.io/tx/{tx_id}"


def explorer_addr_url(address: str) -> str:
    return f"https://explorer.runonflux.io/address/{address}"


# ── Misc ───────────────────────────────────────────────────────────────────────

def tier_label(tier: str) -> str:
    labels = {"CUMULUS": "☁️ Cumulus", "NIMBUS": "🌤️ Nimbus", "STRATUS": "⛅ Stratus"}
    return labels.get(tier.upper(), tier.capitalize())


def fmt_resources(cpu: float, ram: int, hdd: int) -> str:
    return f"CPU: **{cpu}** | RAM: **{ram} MB** | SSD: **{hdd} GB**"
