import sqlite3
import json
import logging
import os
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS wallet_state (
                    address     TEXT PRIMARY KEY,
                    balance     REAL,
                    last_tx_id  TEXT,
                    updated_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS seen_transactions (
                    tx_id      TEXT PRIMARY KEY,
                    address    TEXT NOT NULL,
                    seen_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_ips (
                    payment_address TEXT NOT NULL,
                    ip              TEXT NOT NULL,
                    tier            TEXT,
                    rank            INTEGER,
                    first_seen      TEXT,
                    last_seen       TEXT,
                    PRIMARY KEY (payment_address, ip)
                );

                CREATE TABLE IF NOT EXISTS node_apps (
                    node_ip    TEXT NOT NULL,
                    app_name   TEXT NOT NULL,
                    first_seen TEXT,
                    last_seen  TEXT,
                    PRIMARY KEY (node_ip, app_name)
                );

                CREATE TABLE IF NOT EXISTS kv_state (
                    bucket     TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (bucket, key)
                );
            """)
        logger.info("Database initialized at %s", self.db_path)

    # ── Wallet ────────────────────────────────────────────────────────────────

    def get_wallet_state(self, address: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM wallet_state WHERE address = ?", (address,)
            ).fetchone()
            return dict(row) if row else None

    def set_wallet_state(self, address: str, balance: float, last_tx_id: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO wallet_state (address, balance, last_tx_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    balance    = excluded.balance,
                    last_tx_id = excluded.last_tx_id,
                    updated_at = excluded.updated_at
                """,
                (address, balance, last_tx_id, now),
            )

    def is_tx_seen(self, tx_id: str) -> bool:
        with self._conn() as conn:
            return conn.execute(
                "SELECT 1 FROM seen_transactions WHERE tx_id = ?", (tx_id,)
            ).fetchone() is not None

    def mark_tx_seen(self, tx_id: str, address: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_transactions (tx_id, address, seen_at) VALUES (?, ?, ?)",
                (tx_id, address, now),
            )

    # ── Node IPs ──────────────────────────────────────────────────────────────

    def update_node_ips(self, payment_address: str, current_nodes: list[dict]) -> tuple[set, set]:
        """Reconcile stored IPs with current_nodes. Returns (added, removed) sets."""
        current_map = {n["ip"]: n for n in current_nodes if n.get("ip")}
        current_ips = set(current_map)
        now = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ip FROM node_ips WHERE payment_address = ?", (payment_address,)
            ).fetchall()
            stored_ips = {r["ip"] for r in rows}

            added = current_ips - stored_ips
            removed = stored_ips - current_ips

            for ip in added:
                n = current_map[ip]
                conn.execute(
                    """
                    INSERT INTO node_ips (payment_address, ip, tier, rank, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (payment_address, ip, n.get("tier", ""), n.get("rank", 0), now, now),
                )

            for ip in removed:
                conn.execute(
                    "DELETE FROM node_ips WHERE payment_address = ? AND ip = ?",
                    (payment_address, ip),
                )

            for ip in current_ips & stored_ips:
                n = current_map[ip]
                conn.execute(
                    """
                    UPDATE node_ips SET last_seen = ?, rank = ?, tier = ?
                    WHERE payment_address = ? AND ip = ?
                    """,
                    (now, n.get("rank", 0), n.get("tier", ""), payment_address, ip),
                )

        return added, removed

    # ── Node Apps ─────────────────────────────────────────────────────────────

    def update_node_apps(self, node_ip: str, current_apps: list[dict]) -> set[str]:
        """Reconcile stored apps with current. Returns set of newly added app names."""
        current_map = {a["name"]: a for a in current_apps if a.get("name")}
        current_names = set(current_map)
        now = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT app_name FROM node_apps WHERE node_ip = ?", (node_ip,)
            ).fetchall()
            stored_names = {r["app_name"] for r in rows}

            added = current_names - stored_names
            removed = stored_names - current_names

            for name in added:
                conn.execute(
                    "INSERT INTO node_apps (node_ip, app_name, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                    (node_ip, name, now, now),
                )

            for name in removed:
                conn.execute(
                    "DELETE FROM node_apps WHERE node_ip = ? AND app_name = ?",
                    (node_ip, name),
                )

            for name in current_names & stored_names:
                conn.execute(
                    "UPDATE node_apps SET last_seen = ? WHERE node_ip = ? AND app_name = ?",
                    (now, node_ip, name),
                )

        return added

    # ── Generic key-value state ───────────────────────────────────────────────

    def get_state(self, bucket: str, key: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_state WHERE bucket = ? AND key = ?", (bucket, key)
            ).fetchone()
            if row is None:
                return None
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]

    def set_state(self, bucket: str, key: str, value):
        now = datetime.now(timezone.utc).isoformat()
        if not isinstance(value, str):
            value = json.dumps(value)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_state (bucket, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bucket, key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (bucket, key, value, now),
            )
