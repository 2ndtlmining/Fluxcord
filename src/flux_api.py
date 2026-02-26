"""
Flux API client.

Primary endpoints  : https://api.runonflux.io
Explorer endpoints : https://api.runonflux.io/explorer/* with Insight fallback
Node-local APIs    : http://{ip}:16127/*
"""

import logging
import requests
from typing import Any

logger = logging.getLogger(__name__)

FLUX_API_BASE = "https://api.runonflux.io"
FLUX_EXPLORER_INSIGHT = "https://explorer.runonflux.io/api"
FLUX_NODE_PORT = 16127

_DEFAULT_TIMEOUT = 30
_NODE_TIMEOUT = 10


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _get(url: str, timeout: int = _DEFAULT_TIMEOUT, params: dict | None = None) -> Any:
    """GET a Flux API endpoint. Returns data field on success, None on failure."""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            if body.get("status") == "success":
                return body.get("data")
            logger.warning("API non-success %s: %s", url, body.get("message", body))
            return None
        # Some endpoints return raw arrays/values
        return body
    except requests.Timeout:
        logger.warning("Timeout fetching %s", url)
    except requests.RequestException as exc:
        logger.error("Request error %s: %s", url, exc)
    except Exception as exc:
        logger.error("Unexpected error %s: %s", url, exc)
    return None


def _get_insight(path: str, timeout: int = _DEFAULT_TIMEOUT) -> Any:
    """GET an Insight-style explorer endpoint (no status wrapper)."""
    url = f"{FLUX_EXPLORER_INSIGHT}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        logger.warning("Insight timeout: %s", url)
    except requests.RequestException as exc:
        logger.error("Insight request error %s: %s", url, exc)
    except Exception as exc:
        logger.error("Insight unexpected error %s: %s", url, exc)
    return None


# ── Daemon / network ──────────────────────────────────────────────────────────

def get_node_list() -> list[dict]:
    """Full deterministic node list. Each entry has: ip, tier, rank, payment_address,
    lastpaid, last_paid_height, activesince, amount, etc."""
    data = _get(f"{FLUX_API_BASE}/daemon/viewdeterministiczelnodelist", timeout=60)
    return data if isinstance(data, list) else []


def get_nodes_for_address(payment_address: str) -> list[dict]:
    nodes = get_node_list()
    return [n for n in nodes if n.get("payment_address") == payment_address]


def get_dos_list() -> list[dict] | None:
    """DOS-listed nodes. Each entry: collateral, payment_address, added_height, eligible_in.
    Returns None on API failure, [] if no nodes are in DOS (network is clean)."""
    data = _get(f"{FLUX_API_BASE}/daemon/getdoslist")
    if data is None:
        return None
    return data if isinstance(data, list) else []


def get_node_count() -> dict:
    """Node counts: total, cumulus-enabled, nimbus-enabled, stratus-enabled, ipv4, ..."""
    data = _get(f"{FLUX_API_BASE}/daemon/getzelnodecount")
    return data if isinstance(data, dict) else {}


def get_block_count() -> int:
    data = _get(f"{FLUX_API_BASE}/daemon/getblockcount")
    try:
        return int(data)
    except (TypeError, ValueError):
        return 0


# ── Apps ──────────────────────────────────────────────────────────────────────

def get_global_apps() -> list[dict]:
    """All global app specifications. Each entry: name, description, repotag, cpu, ram,
    hdd, height, instances, owner, compose, tiered, version."""
    data = _get(f"{FLUX_API_BASE}/apps/globalappsspecifications", timeout=90)
    return data if isinstance(data, list) else []


# ── Wallet / Explorer ─────────────────────────────────────────────────────────

def get_wallet_balance(address: str) -> float | None:
    """Returns balance in FLUX, or None if unavailable.

    Primary API returns satoshis (integer), so we divide by 1e8.
    Insight /addr/{address} returns the balance already in FLUX.
    """
    # Primary: Flux explorer API (returns satoshis as integer)
    data = _get(f"{FLUX_API_BASE}/explorer/balance?address={address}")
    if data is not None:
        try:
            return float(data) / 1e8
        except (TypeError, ValueError):
            pass

    # Fallback: Insight API (balance field is already in FLUX)
    info = _get_insight(f"addr/{address}")
    if info and isinstance(info, dict) and "balance" in info:
        try:
            return float(info["balance"])
        except (TypeError, ValueError):
            pass

    return None


def get_wallet_transactions(address: str, count: int = 10) -> list[dict]:
    """Returns a list of recent transactions for the address.

    Each tx dict contains at minimum: txid, time, amount (net FLUX change for address).
    Uses the Insight /addrs/ endpoint which returns full tx data with vout details.
    Falls back to primary txids-only list if Insight fails.
    """
    # Primary path: Insight /addrs/{address}/txs  (note: plural "addrs")
    info = _get_insight(f"addrs/{address}/txs?from=0&to={count}")
    if info and isinstance(info, dict) and info.get("items"):
        txs = []
        for item in info["items"][:count]:
            tx_id = item.get("txid", "")
            if not tx_id:
                continue
            net = _calc_net_amount(item, address)
            txs.append({"txid": tx_id, "time": item.get("time", 0), "amount": net})
        return txs

    # Fallback: primary API returns txids only (no amounts)
    data = _get(f"{FLUX_API_BASE}/explorer/transactions?address={address}")
    if isinstance(data, list) and data:
        return [{"txid": tx.get("txid", ""), "time": 0, "amount": None} for tx in data[:count]]

    return []


def _calc_net_amount(tx: dict, address: str) -> float:
    """Calculate net FLUX received by address from a full Insight tx object.

    vout values are strings like "9.00000000". vin entries have an "addr" field
    and a "value" field for the spent amount when the tx is fully loaded.
    """
    received = sum(
        float(o.get("value", 0) or 0)
        for o in tx.get("vout", [])
        if address in (o.get("scriptPubKey") or {}).get("addresses", [])
    )
    sent = sum(
        float(i.get("value", 0) or 0)
        for i in tx.get("vin", [])
        if i.get("addr") == address
    )
    return round(received - sent, 8)


# ── Arcane OS ─────────────────────────────────────────────────────────────────

def get_arcane_os_percentage() -> float | None:
    """Return the % of all network nodes running Arcane OS.

    Uses stats.runonflux.io/fluxinfo with a projection so only the
    arcaneVersion field is returned for each node (~250 KB for 7900+ nodes).
    A node counts as Arcane if arcaneVersion is a non-empty string.
    Returns None on failure.
    """
    data = _get(
        "https://stats.runonflux.io/fluxinfo",
        params={"projection": "flux.arcaneVersion"},
        timeout=30,
    )
    if not isinstance(data, list) or not data:
        logger.warning("Could not fetch arcane OS data from stats API")
        return None

    total = len(data)
    arcane = sum(
        1 for entry in data
        if isinstance(entry.get("flux"), dict) and entry["flux"].get("arcaneVersion")
    )
    pct = arcane / total * 100
    logger.info("Arcane OS: %d/%d nodes = %.1f%%", arcane, total, pct)
    return pct


# ── Network utilization (stats.runonflux.io) ──────────────────────────────────

def get_network_utilization() -> dict | None:
    """Return aggregated total vs used resources across all online nodes.

    Fetches two stats projections in sequence:
      • benchmark  → total capacity (cores, ram GB, ssd GB)  [online nodes only]
      • apps.resources → locked by apps (cpus, ram MB, hdd GB)

    Returns dict with keys: total_cores, total_ram_gb, total_ssd_gb,
                            used_cores, used_ram_gb, used_ssd_gb
    or None on failure.
    """
    bench_data = _get(
        "https://stats.runonflux.io/fluxinfo",
        params={"projection": "benchmark"},
        timeout=60,
    )
    res_data = _get(
        "https://stats.runonflux.io/fluxinfo",
        params={"projection": "apps.resources"},
        timeout=60,
    )

    if not isinstance(bench_data, list) or not bench_data:
        logger.warning("Could not fetch benchmark stats for utilization")
        return None

    total_cores = 0.0
    total_ram_gb = 0.0
    total_ssd_gb = 0.0

    for entry in bench_data:
        bm = entry.get("benchmark") or {}
        status = (bm.get("status") or {}).get("status", "")
        if status != "online":
            continue
        bench = bm.get("bench") or {}
        total_cores += float(bench.get("cores", 0) or 0)
        total_ram_gb += float(bench.get("ram", 0) or 0)
        total_ssd_gb += float(bench.get("ssd", 0) or 0)

    used_cores = 0.0
    used_ram_gb = 0.0
    used_ssd_gb = 0.0

    if isinstance(res_data, list):
        for entry in res_data:
            res = (entry.get("apps") or {}).get("resources") or {}
            used_cores += float(res.get("appsCpusLocked", 0) or 0)
            used_ram_gb += float(res.get("appsRamLocked", 0) or 0) / 1024  # MB → GB
            used_ssd_gb += float(res.get("appsHddLocked", 0) or 0)

    logger.info(
        "Network utilization: CPU %.1f/%.1f cores | RAM %.1f/%.1f GB | SSD %.1f/%.1f GB",
        used_cores, total_cores, used_ram_gb, total_ram_gb, used_ssd_gb, total_ssd_gb,
    )

    return {
        "total_cores": total_cores,
        "total_ram_gb": total_ram_gb,
        "total_ssd_gb": total_ssd_gb,
        "used_cores": used_cores,
        "used_ram_gb": used_ram_gb,
        "used_ssd_gb": used_ssd_gb,
    }


def get_node_benchmark_stats() -> dict:
    """Fetch benchmark data for all network nodes via one stats API call.

    Returns dict keyed by bare IP (port stripped):
      { "1.2.3.4": {"cores": 4, "ram_gb": 7.7, "ssd_gb": 220, "status": "online"} }
    """
    data = _get(
        "https://stats.runonflux.io/fluxinfo",
        params={"projection": "benchmark"},
        timeout=60,
    )
    result = {}
    if not isinstance(data, list):
        logger.warning("Could not fetch benchmark stats from stats API")
        return result
    for entry in data:
        bm = entry.get("benchmark") or {}
        bench = bm.get("bench") or {}
        ip_port = bench.get("ipaddress", "")
        bare_ip = ip_port.split(":")[0] if ip_port else ""
        if not bare_ip:
            continue
        result[bare_ip] = {
            "cores": int(bench.get("cores", 0) or 0),
            "ram_gb": float(bench.get("ram", 0) or 0),
            "ssd_gb": int(bench.get("ssd", 0) or 0),
            "status": (bm.get("status") or {}).get("status", "N/A"),
        }
    logger.info("Benchmark stats loaded for %d network nodes", len(result))
    return result


# ── Per-node APIs (direct to node IP:16127) ───────────────────────────────────

def _node_host(ip: str) -> str:
    """Strip any port suffix from node IP (node list may include port, e.g. '1.2.3.4:16147')."""
    return ip.split(":")[0]


def get_node_info(ip: str, timeout: int = _NODE_TIMEOUT) -> dict | None:
    """FluxOS info: version, uptime, network, etc."""
    return _get(f"http://{_node_host(ip)}:{FLUX_NODE_PORT}/flux/info", timeout=timeout)


def get_node_benchmark(ip: str, timeout: int = _NODE_TIMEOUT) -> dict | None:
    """Benchmark results from /benchmark/getinfo (nested structure)."""
    return _get(f"http://{_node_host(ip)}:{FLUX_NODE_PORT}/benchmark/getinfo", timeout=timeout)


def get_node_benchmarks(ip: str, timeout: int = _NODE_TIMEOUT) -> dict | None:
    """Benchmark data from /benchmark/getbenchmarks (flat structure).

    Returns dict with keys: cores, ram(GB), ssd(GB), eps, ddwrite(MB/s),
    download_speed, upload_speed, etc. Returns None on failure.
    """
    data = _get(f"http://{_node_host(ip)}:{FLUX_NODE_PORT}/benchmark/getbenchmarks", timeout=timeout)
    return data if isinstance(data, dict) else None


def get_node_installed_apps(ip: str, timeout: int = _NODE_TIMEOUT) -> list[dict] | None:
    """Apps currently installed on this node.
    Returns None on API failure (node unreachable/timeout), [] if node has no apps."""
    data = _get(f"http://{_node_host(ip)}:{FLUX_NODE_PORT}/apps/installedapps", timeout=timeout)
    if data is None:
        return None
    return data if isinstance(data, list) else []


def get_node_uptime(ip: str, timeout: int = _NODE_TIMEOUT) -> dict | None:
    return _get(f"http://{_node_host(ip)}:{FLUX_NODE_PORT}/flux/systemuptime", timeout=timeout)
