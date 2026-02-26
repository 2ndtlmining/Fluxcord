"""
Node Monitor

5-minute checks:
  • DOS alert if any owned node appears in the DOS list
  • IP added / removed
  • New app deployed on a node (heads-up notification)

Daily checks:
  • Compact grid overview of all nodes: rank+Δ, benchmark, FluxOS version
  • Apps currently installed across all nodes with resource specs
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config import Config
from src.database import Database
from src.discord_client import DiscordClient, COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_BLUE, COLOR_GRAY
from src.flux_api import (
    get_nodes_for_address,
    get_dos_list,
    get_node_installed_apps,
    get_node_benchmarks,
)
from src.formatters import (
    fmt_time_from_rank,
    tier_label,
    fmt_resources,
)

logger = logging.getLogger(__name__)

_MAX_5MIN_WORKERS  = 5
_MAX_DAILY_WORKERS = 20
_NODES_PER_TABLE   = 20   # rows before splitting to a new embed
_MAX_NODE_EMBEDS   = 15   # hard cap to prevent floods
_APPS_PER_TABLE    = 25
_MAX_APP_EMBEDS    = 4


class NodeMonitor:
    def __init__(self, config: Config, db: Database, discord: DiscordClient):
        self.config = config
        self.db = db
        self.discord = discord

    # ── 5-minute checks ────────────────────────────────────────────────────────

    def check_5min(self):
        if not self.config.wallet_notifications or not self.config.wallet:
            return

        nodes = get_nodes_for_address(self.config.wallet)
        if not nodes:
            logger.info("No nodes found for %s", self.config.wallet)
            return

        logger.info("5-min node check: %d node(s) for %s", len(nodes), self.config.wallet)
        self._check_dos(nodes)
        self._check_ip_changes(nodes)
        self._check_new_apps(nodes)

    def _check_dos(self, nodes: list[dict]):
        dos_list = get_dos_list()
        if dos_list is None:
            logger.warning("DOS check: could not fetch DOS list (API unavailable)")
            return
        if not dos_list:
            logger.info("DOS check: 0/%d node(s) in DOS list (network clean)", len(nodes))
            return

        dos_addresses = {e.get("payment_address") for e in dos_list if e.get("payment_address")}
        dos_nodes = [n for n in nodes if n.get("payment_address") in dos_addresses]

        logger.info("DOS check: %d/%d node(s) in DOS list", len(dos_nodes), len(nodes))
        if not dos_nodes:
            return

        all_fields = []
        for node in dos_nodes:
            ip   = node.get("ip", "unknown")
            tier = tier_label(node.get("tier", ""))
            rank = node.get("rank", 0)
            all_fields.append({
                "name":   f"⚠️ {ip.split(':')[0]}",
                "value":  f"Tier: {tier} | Rank: {rank:,}",
                "inline": False,
            })

        desc = (
            "The following node(s) are currently in the Flux DOS list "
            "and may be delisted if not resolved."
        )
        embeds = []
        for i in range(0, len(all_fields), 25):
            chunk = all_fields[i : i + 25]
            embeds.append(
                self.discord.build_embed(
                    title=f"🚨 {len(dos_nodes)} Node(s) in DOS Status!",
                    description=desc,
                    color=COLOR_RED,
                    fields=chunk,
                    footer="Fluxcord • Node Monitor",
                )
            )
        self.discord.send_embeds(embeds, mention=True)

    def _check_ip_changes(self, nodes: list[dict]):
        added, removed = self.db.update_node_ips(self.config.wallet, nodes)
        if not added and not removed:
            return

        net   = len(added) - len(removed)
        color = COLOR_GREEN if net > 0 else COLOR_RED if net < 0 else COLOR_YELLOW

        # Build field list (Discord max 25 per embed — batch if needed)
        all_fields = []
        for ip in sorted(added):
            node = next((n for n in nodes if n.get("ip") == ip), {})
            all_fields.append({
                "name":   "✅ Node Added",
                "value":  f"`{ip.split(':')[0]}` – {tier_label(node.get('tier', ''))}",
                "inline": True,
            })
        for ip in sorted(removed):
            all_fields.append({"name": "❌ Node Removed", "value": f"`{ip.split(':')[0]}`", "inline": True})

        embeds = []
        for i in range(0, len(all_fields), 25):
            chunk = all_fields[i : i + 25]
            embeds.append(
                self.discord.build_embed(
                    title="🔄 Node IP Changes Detected",
                    description=f"+{len(added)} added  ·  -{len(removed)} removed",
                    color=color,
                    fields=chunk,
                    footer="Fluxcord • Node Monitor",
                )
            )
        self.discord.send_embeds(embeds, mention=True)

    def _check_new_apps(self, nodes: list[dict]):
        timeout = self.config.node_request_timeout
        with ThreadPoolExecutor(max_workers=_MAX_5MIN_WORKERS) as ex:
            futures = {ex.submit(self._check_node_apps, node, timeout): node for node in nodes}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:
                    node = futures[fut]
                    logger.error("App check error for %s: %s", node.get("ip"), exc)

    def _check_node_apps(self, node: dict, timeout: int):
        ip = node.get("ip", "")
        if not ip:
            return

        apps = get_node_installed_apps(ip, timeout=timeout)
        if apps is None:
            # Node unreachable – skip; don't clear stored apps
            return

        new_names = self.db.update_node_apps(ip, apps)
        if not new_names:
            return

        app_map = {a.get("name"): a for a in apps if a.get("name")}
        fields = []
        for name in new_names:
            app       = app_map.get(name, {})
            resources = _extract_resources(app)
            fields.append({
                "name":   f"📦 {name}",
                "value":  f"Node: `{ip.split(':')[0]}`\n{resources}",
                "inline": False,
            })

        embed = self.discord.build_embed(
            title=f"🚀 New App(s) on {ip.split(':')[0]}",
            description=f"{tier_label(node.get('tier', ''))} – {len(new_names)} new app(s) deployed",
            color=COLOR_GREEN,
            fields=fields,
            footer="Fluxcord • Node Monitor",
        )
        self.discord.send_embed(embed)

    # ── Daily overview ─────────────────────────────────────────────────────────

    def check_daily(self):
        if not self.config.wallet_notifications or not self.config.wallet:
            return

        nodes = get_nodes_for_address(self.config.wallet)
        if not nodes:
            logger.info("No nodes found for %s (daily)", self.config.wallet)
            return

        logger.info("Daily node overview: %d node(s)", len(nodes))
        timeout = self.config.node_request_timeout

        # Concurrent per-node calls: benchmark data + installed apps
        benchmarks: dict[str, dict] = {}  # bare_ip → flat benchmark dict
        node_apps:  dict[str, list] = {}  # bare_ip → [app_dict, ...]

        with ThreadPoolExecutor(max_workers=_MAX_DAILY_WORKERS) as ex:
            bench_futs = {
                ex.submit(get_node_benchmarks,     node.get("ip", ""), timeout): node
                for node in nodes
            }
            apps_futs = {
                ex.submit(get_node_installed_apps, node.get("ip", ""), timeout): node
                for node in nodes
            }

            for fut in as_completed(bench_futs):
                node    = bench_futs[fut]
                bare_ip = node.get("ip", "").split(":")[0]
                try:
                    benchmarks[bare_ip] = fut.result() or {}
                except Exception as exc:
                    logger.error("Benchmark fetch error %s: %s", bare_ip, exc)
                    benchmarks[bare_ip] = {}

            for fut in as_completed(apps_futs):
                node    = apps_futs[fut]
                bare_ip = node.get("ip", "").split(":")[0]
                try:
                    node_apps[bare_ip] = fut.result() or []
                except Exception as exc:
                    logger.error("Apps fetch error %s: %s", bare_ip, exc)
                    node_apps[bare_ip] = []

        # Send header
        self.discord.send_embed(
            self.discord.build_embed(
                title="📊 Daily Node Overview",
                description=f"You have **{len(nodes)}** node(s) on the Flux network.",
                color=COLOR_BLUE,
                footer="Fluxcord • Daily Report",
            )
        )

        # Compact node grid
        self._send_node_grid(nodes, benchmarks)

        # Apps currently on your nodes
        self._send_node_apps_daily(nodes, node_apps)

    def _send_node_grid(self, nodes: list[dict], benchmarks: dict):
        rows = []
        for node in nodes:
            ip_full = node.get("ip", "unknown")
            bare_ip = ip_full.split(":")[0]
            tier    = node.get("tier", "UNKNOWN")
            rank    = int(node.get("rank", 0))

            # Store rank for next day (no delta shown in grid)
            self.db.set_state("node_daily", f"rank_{bare_ip}", rank)
            next_str = fmt_time_from_rank(rank).replace(" ", "")

            # Benchmark data from /benchmark/getbenchmarks (flat structure)
            bm      = benchmarks.get(bare_ip, {})
            cores   = bm.get("cores", 0)
            ram_gb  = float(bm.get("ram", 0.0))   # already in GB
            ssd_gb  = float(bm.get("ssd", 0.0))   # already in GB
            eps     = float(bm.get("eps", 0.0))
            ddwrite = bm.get("ddwrite")            # None if unavailable

            # EPS delta vs yesterday
            eps_prev_raw = self.db.get_state("node_daily", f"eps_{bare_ip}")
            eps_prev     = float(eps_prev_raw) if eps_prev_raw is not None else None
            self.db.set_state("node_daily", f"eps_{bare_ip}", eps)

            if eps_prev is not None and eps:
                d = int(eps) - int(eps_prev)
                eps_str = f"{int(eps)}({'+' if d >= 0 else ''}{d})"
            else:
                eps_str = str(int(eps)) if eps else "N/A"

            # DWS delta vs yesterday
            dws_prev_raw = self.db.get_state("node_daily", f"dws_{bare_ip}")
            dws_prev     = float(dws_prev_raw) if dws_prev_raw is not None else None
            if ddwrite is not None:
                self.db.set_state("node_daily", f"dws_{bare_ip}", ddwrite)

            if ddwrite is not None:
                if dws_prev is not None:
                    d = ddwrite - dws_prev
                    dws_str = f"{ddwrite:.1f}({'+' if d >= 0 else ''}{d:.1f})"
                else:
                    dws_str = f"{ddwrite:.1f}"
            else:
                dws_str = "N/A"

            tier_abbr = {"CUMULUS": "C", "NIMBUS": "N", "STRATUS": "S"}.get(tier.upper(), "?")

            rows.append((bare_ip, tier_abbr, rank, next_str, cores, ram_gb, ssd_gb, eps_str, dws_str))

        embeds = []
        for i in range(0, len(rows), _NODES_PER_TABLE):
            chunk = rows[i : i + _NODES_PER_TABLE]
            embeds.append(
                self.discord.build_embed(
                    title=f"Nodes {i + 1}–{i + len(chunk)}",
                    description=_build_node_table(chunk),
                    color=COLOR_BLUE,
                    footer="Fluxcord • Node Overview",
                )
            )
        self.discord.send_embeds(embeds[:_MAX_NODE_EMBEDS])

    def _send_node_apps_daily(self, nodes: list[dict], node_apps: dict):
        all_rows = []
        for node in nodes:
            bare_ip = node.get("ip", "").split(":")[0]
            for app in sorted(node_apps.get(bare_ip, []), key=lambda a: a.get("name", "")):
                name    = app.get("name", "?")
                compose = app.get("compose") or []
                if compose:
                    cpu = sum(c.get("cpu", 0) for c in compose)
                    ram = sum(c.get("ram", 0) for c in compose)
                    hdd = sum(c.get("hdd", 0) for c in compose)
                else:
                    cpu = app.get("cpu", 0)
                    ram = app.get("ram", 0)
                    hdd = app.get("hdd", 0)
                all_rows.append((bare_ip, name, cpu, ram, hdd))

        if not all_rows:
            self.discord.send_embed(
                self.discord.build_embed(
                    title="📦 Apps on Your Nodes",
                    description="No apps currently installed on your nodes.",
                    color=COLOR_GRAY,
                    footer="Fluxcord • Node Monitor",
                )
            )
            return

        nodes_with_apps = len({r[0] for r in all_rows})
        self.discord.send_embed(
            self.discord.build_embed(
                title=f"📦 {len(all_rows)} App(s) Across {nodes_with_apps} Node(s)",
                color=COLOR_GREEN,
                footer="Fluxcord • Node Monitor",
            )
        )

        embeds = []
        for i in range(0, len(all_rows), _APPS_PER_TABLE):
            chunk = all_rows[i : i + _APPS_PER_TABLE]
            embeds.append(
                self.discord.build_embed(
                    title=f"Apps {i + 1}–{i + len(chunk)}",
                    description=_build_node_apps_table(chunk),
                    color=COLOR_GREEN,
                    footer="Fluxcord • Node Apps",
                )
            )
        self.discord.send_embeds(embeds[:_MAX_APP_EMBEDS])


# ── Module-level helpers ────────────────────────────────────────────────────────

def _extract_resources(app: dict) -> str:
    compose = app.get("compose") or []
    if compose:
        cpu = sum(c.get("cpu", 0) for c in compose)
        ram = sum(c.get("ram", 0) for c in compose)
        hdd = sum(c.get("hdd", 0) for c in compose)
    else:
        cpu = app.get("cpu", 0)
        ram = app.get("ram", 0)
        hdd = app.get("hdd", 0)
    return fmt_resources(cpu, ram, hdd)


def _fmt_gb(gb: float) -> str:
    """Format a GB float compactly: 7.7→'7.7G', 220→'220G', 2000→'2.0T'."""
    if gb >= 1000:
        return f"{gb / 1000:.1f}T"
    if gb >= 100:
        return f"{int(gb)}G"
    return f"{gb:.1f}G"


def _fmt_mb(mb: int) -> str:
    """Format RAM in MB → 'XG' if ≥ 1024, else 'XM'."""
    if mb >= 1024:
        gb = mb / 1024
        return f"{int(gb)}G" if gb >= 100 else f"{gb:.1f}G"
    return f"{mb}M"


def _build_node_table(rows: list) -> str:
    """Compact monospace grid of node stats.

    Columns: IP | T | Rank | Next | CPU | RAM | SSD | EPS(delta) | DWS(delta)
    rows = [(bare_ip, tier_abbr, rank, next_str, cores, ram_gb, ssd_gb, eps_str, dws_str)]
    Tier abbrev: C=Cumulus, N=Nimbus, S=Stratus
    EPS/DWS include inline deltas from previous day, e.g. 1861(+50) / 192.6(-10.2)
    """
    COL_IP  = 15
    COL_EPS = 12   # enough for "9999(+999)"
    header = (
        f"{'IP':<{COL_IP}} T {'Rank':<6} {'Next':<6} "
        f"{'CPU':<3} {'RAM':<5} {'SSD':<5} {'EPS':<{COL_EPS}} DWS"
    )
    sep   = "-" * (len(header) + 6)
    lines = []
    for bare_ip, tier, rank, nxt, cores, ram_gb, ssd_gb, eps_str, dws_str in rows:
        ip_s   = bare_ip[:COL_IP].ljust(COL_IP)
        rank_s = str(rank)[:6].ljust(6)
        nxt_s  = nxt[:6].ljust(6)
        cpu_s  = str(cores)[:3].ljust(3)
        ram_s  = _fmt_gb(ram_gb)[:5].ljust(5)
        ssd_s  = _fmt_gb(ssd_gb)[:5].ljust(5)
        eps_s  = eps_str[:COL_EPS].ljust(COL_EPS)
        lines.append(
            f"{ip_s} {tier} {rank_s} {nxt_s} {cpu_s} {ram_s} {ssd_s} {eps_s} {dws_str}"
        )
    body = "\n".join(lines)
    return f"```\n{header}\n{sep}\n{body}\n```"


def _build_node_apps_table(rows: list) -> str:
    """Compact monospace table of apps on nodes.

    rows = [(bare_ip, app_name, cpu, ram_mb, hdd_gb)]
    Columns: Node IP | App | CPU | RAM | SSD
    """
    COL_IP  = 15
    COL_APP = 20
    header  = f"{'Node':<{COL_IP}} {'App':<{COL_APP}} {'CPU':<5} {'RAM':<6} SSD"
    sep     = "-" * len(header)
    lines   = []
    for bare_ip, app_name, cpu, ram_mb, hdd_gb in rows:
        ip_s   = bare_ip[:COL_IP].ljust(COL_IP)
        app_s  = app_name[:COL_APP].ljust(COL_APP)
        cpu_s  = f"{cpu:.1f}"[:5].ljust(5)
        ram_s  = _fmt_mb(int(ram_mb))[:6].ljust(6)
        ssd_s  = f"{hdd_gb}G"
        lines.append(f"{ip_s} {app_s} {cpu_s} {ram_s} {ssd_s}")
    body = "\n".join(lines)
    return f"```\n{header}\n{sep}\n{body}\n```"
