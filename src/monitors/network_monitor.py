"""
Network Monitor – daily sends.

1. Node count by tier with change indicators + Arcane OS %
2. New apps deployed in the last NEW_APPS_BLOCK_WINDOW blocks (compact grid)
"""

import logging

from src.config import Config
from src.database import Database
from src.discord_client import DiscordClient, COLOR_GREEN, COLOR_RED, COLOR_BLUE, COLOR_GRAY
from src.flux_api import get_node_count, get_block_count, get_global_apps, get_arcane_os_percentage, get_network_utilization
from src.formatters import fmt_int_change, fmt_pct_change

logger = logging.getLogger(__name__)

_APPS_PER_TABLE = 25   # rows per code-block table before splitting into next embed
_MAX_APP_EMBEDS = 4    # cap total app embeds to avoid webhook floods


class NetworkMonitor:
    def __init__(self, config: Config, db: Database, discord: DiscordClient):
        self.config = config
        self.db = db
        self.discord = discord

    def check_daily(self):
        if not self.config.network_notifications:
            return
        logger.info("Running daily network check")
        self._send_node_counts()
        self._send_new_apps()
        self._send_utilization()

    # ── Node counts ────────────────────────────────────────────────────────────

    def _send_node_counts(self):
        counts = get_node_count()
        if not counts:
            logger.warning("Could not fetch node counts")
            return

        prev = self.db.get_state("network_daily", "node_counts") or {}

        total   = counts.get("total", 0)
        cumulus = counts.get("cumulus-enabled", 0)
        nimbus  = counts.get("nimbus-enabled", 0)
        stratus = counts.get("stratus-enabled", 0)

        color = COLOR_GREEN
        if prev.get("total") and total < prev["total"]:
            color = COLOR_RED

        fields = [
            {
                "name": "🌐 Total Nodes",
                "value": fmt_int_change(total, prev.get("total")),
                "inline": False,
            },
            {
                "name": "☁️ Cumulus",
                "value": fmt_int_change(cumulus, prev.get("cumulus-enabled")),
                "inline": True,
            },
            {
                "name": "🌤️ Nimbus",
                "value": fmt_int_change(nimbus, prev.get("nimbus-enabled")),
                "inline": True,
            },
            {
                "name": "⛅ Stratus",
                "value": fmt_int_change(stratus, prev.get("stratus-enabled")),
                "inline": True,
            },
        ]

        if total > 0:
            fields.append(
                {
                    "name": "📊 Tier Split",
                    "value": (
                        f"Cumulus **{cumulus / total * 100:.1f}%** · "
                        f"Nimbus **{nimbus / total * 100:.1f}%** · "
                        f"Stratus **{stratus / total * 100:.1f}%**"
                    ),
                    "inline": False,
                }
            )

        # Arcane OS % via stats projection API (covers all nodes, ~250KB)
        prev_arcane = self.db.get_state("network_daily", "arcane_pct")
        arcane_pct = get_arcane_os_percentage()
        if arcane_pct is not None:
            prev_arcane_f = float(prev_arcane) if prev_arcane is not None else None
            fields.append(
                {
                    "name": "🟣 Arcane OS",
                    "value": fmt_pct_change(arcane_pct, prev_arcane_f),
                    "inline": True,
                }
            )
            self.db.set_state("network_daily", "arcane_pct", arcane_pct)

        embed = self.discord.build_embed(
            title="📈 Daily Flux Network – Node Counts",
            color=color,
            fields=fields,
            footer="Fluxcord • Network Monitor",
        )
        self.discord.send_embed(embed)
        self.db.set_state("network_daily", "node_counts", counts)

    # ── New apps ───────────────────────────────────────────────────────────────

    def _send_new_apps(self):
        current_height = get_block_count()
        if not current_height:
            logger.warning("Could not fetch block count")
            return

        apps = get_global_apps()
        if apps is None:
            logger.warning("Could not fetch global apps")
            return

        window = self.config.new_apps_block_window
        min_height = current_height - window
        new_apps = sorted(
            [a for a in apps if a.get("height", 0) >= min_height],
            key=lambda x: x.get("height", 0),
            reverse=True,
        )

        if not new_apps:
            embed = self.discord.build_embed(
                title="📦 Daily New Apps",
                description=f"No new apps deployed in the last **{window:,}** blocks.",
                color=COLOR_GRAY,
                footer="Fluxcord • Network Monitor",
            )
            self.discord.send_embed(embed)
            return

        max_apps = _APPS_PER_TABLE * _MAX_APP_EMBEDS
        shown = new_apps[:max_apps]

        summary = self.discord.build_embed(
            title=f"📦 {len(new_apps)} New App(s) — Last {window:,} Blocks",
            description=(
                f"Block range: **{min_height:,}** → **{current_height:,}**"
                + (f"\n*Showing first {max_apps} of {len(new_apps)}*" if len(new_apps) > max_apps else "")
            ),
            color=COLOR_BLUE,
            footer="Fluxcord • Network Monitor",
        )
        self.discord.send_embed(summary)

        # Build compact table embeds
        embeds = []
        for i in range(0, len(shown), _APPS_PER_TABLE):
            chunk = shown[i : i + _APPS_PER_TABLE]
            table = _build_apps_table(chunk)
            embeds.append(
                self.discord.build_embed(
                    title=f"Apps {i + 1}–{i + len(chunk)}",
                    description=table,
                    color=COLOR_GREEN,
                    footer="Fluxcord • New Apps",
                )
            )

        self.discord.send_embeds(embeds)

    # ── Network utilization ────────────────────────────────────────────────────

    def _send_utilization(self):
        util = get_network_utilization()
        if not util:
            logger.warning("Could not fetch network utilization")
            return

        prev = self.db.get_state("network_daily", "utilization") or {}

        total_cores = util["total_cores"]
        total_ram   = util["total_ram_gb"]
        total_ssd   = util["total_ssd_gb"]
        used_cores  = util["used_cores"]
        used_ram    = util["used_ram_gb"]
        used_ssd    = util["used_ssd_gb"]

        def pct(used, total):
            return used / total * 100 if total > 0 else 0.0

        cpu_pct = pct(used_cores, total_cores)
        ram_pct = pct(used_ram, total_ram)
        ssd_pct = pct(used_ssd, total_ssd)

        prev_cpu_pct = prev.get("cpu_pct")
        prev_ram_pct = prev.get("ram_pct")
        prev_ssd_pct = prev.get("ssd_pct")

        def _util_field(emoji, label, used, total, used_pct, prev_pct, unit="GB"):
            val = f"**{used:,.1f}** / {total:,.1f} {unit}  ({fmt_pct_change(used_pct, prev_pct)})"
            return {"name": f"{emoji} {label}", "value": val, "inline": False}

        fields = [
            _util_field("🖥️", "CPU Cores", used_cores, total_cores, cpu_pct, prev_cpu_pct, unit="cores"),
            _util_field("💾", "RAM",        used_ram,   total_ram,   ram_pct, prev_ram_pct),
            _util_field("💽", "SSD",        used_ssd,   total_ssd,   ssd_pct, prev_ssd_pct),
        ]

        # Pick embed color based on highest utilization
        max_pct = max(cpu_pct, ram_pct, ssd_pct)
        color = COLOR_GREEN if max_pct < 60 else COLOR_RED if max_pct > 85 else COLOR_BLUE

        embed = self.discord.build_embed(
            title="⚡ Daily Flux Network – Resource Utilization",
            color=color,
            fields=fields,
            footer="Fluxcord • Network Monitor",
        )
        self.discord.send_embed(embed)

        self.db.set_state("network_daily", "utilization", {
            "cpu_pct": cpu_pct,
            "ram_pct": ram_pct,
            "ssd_pct": ssd_pct,
        })


# ── Table formatter ────────────────────────────────────────────────────────────

def _is_git_app(app: dict) -> bool:
    """Git app = any component's repotag contains 'runonflux/orbit' (case-insensitive).
    Matches Fluxtracker's determineAppType() logic exactly.
    """
    orbit = "runonflux/orbit"
    # Check compose components first (new spec format)
    for comp in app.get("compose") or []:
        if orbit in (comp.get("repotag") or "").lower():
            return True
    # Check top-level repotag (old single-component spec format)
    return orbit in (app.get("repotag") or "").lower()


def _build_apps_table(apps: list[dict]) -> str:
    """Return a monospace code-block table listing all apps compactly.

    Columns: Name (18) | T (D/G) | Inst | CPU (1dp) | RAM (GB) | SSD (GB) | Block
    RAM and SSD are always shown in GB (rounded to 1dp). CPU rounded to 1dp.
    """
    COL_NAME = 18
    rows = []
    for app in apps:
        name = (app.get("name") or "?")[:COL_NAME].ljust(COL_NAME)

        compose = app.get("compose") or []
        if compose:
            cpu = sum(c.get("cpu", 0) for c in compose)
            ram_mb = sum(c.get("ram", 0) for c in compose)
            hdd_gb = sum(c.get("hdd", 0) for c in compose)
        else:
            cpu = app.get("cpu", 0)
            ram_mb = app.get("ram", 0)
            hdd_gb = app.get("hdd", 0)

        app_type = "G" if _is_git_app(app) else "D"
        inst     = str(app.get("instances", 1))
        cpu_str  = f"{cpu:.1f}"
        ram_str  = f"{ram_mb / 1024:.1f}G"   # always GB
        hdd_str  = f"{hdd_gb}G"
        height   = str(app.get("height", 0))

        rows.append(
            f"{name} {app_type} x{inst:<3} {cpu_str:<5} {ram_str:<7} {hdd_str:<6} {height}"
        )

    header = f"{'Name':<{COL_NAME}} T Inst CPU   RAM     SSD    Block"
    sep    = "─" * len(header)
    body   = "\n".join(rows)
    return f"```\n{header}\n{sep}\n{body}\n```"
