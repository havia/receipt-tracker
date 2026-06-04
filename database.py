"""
Database layer — SQLite via Python stdlib.
Stores price history per item per vendor.
"""

import sqlite3
import datetime
from typing import Optional

DB_PATH = "prices.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor      TEXT    NOT NULL,
                item_name   TEXT    NOT NULL,
                item_key    TEXT    NOT NULL,   -- normalized lowercase for matching
                price       REAL    NOT NULL,
                unit        TEXT,               -- e.g. "1L", "500g", "kg"
                recorded_at TEXT    NOT NULL    -- ISO timestamp
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_item_key ON price_records(item_key)
        """)
        conn.commit()


def normalize(name: str) -> str:
    """Normalize item name for cross-vendor matching."""
    # Strip common Hebrew/English suffixes, lowercase, trim
    name = name.lower().strip()
    # Remove store-brand prefixes that differ per vendor
    for prefix in ["שופרסל ", "רמי לוי ", "victory ", "mega ", "yochananof "]:
        name = name.replace(prefix, "")
    return name


def save_items(vendor: str, items: list[dict]):
    """
    items: list of {"name": str, "price": float, "unit": str|None}
    """
    now = datetime.datetime.utcnow().isoformat()
    with get_conn() as conn:
        for item in items:
            conn.execute("""
                INSERT INTO price_records (vendor, item_name, item_key, price, unit, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                vendor,
                item["name"],
                normalize(item["name"]),
                item["price"],
                item.get("unit"),
                now,
            ))
        conn.commit()


def get_price_history() -> dict[str, list[dict]]:
    """
    Returns a dict: item_key → list of {vendor, price, unit, recorded_at}
    Only the most recent record per vendor per item.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT item_key, vendor, price, unit, recorded_at
            FROM price_records p1
            WHERE recorded_at = (
                SELECT MAX(recorded_at)
                FROM price_records p2
                WHERE p2.item_key = p1.item_key AND p2.vendor = p1.vendor
            )
            ORDER BY item_key, vendor
        """).fetchall()

    history: dict[str, list[dict]] = {}
    for row in rows:
        key = row["item_key"]
        history.setdefault(key, []).append({
            "vendor": row["vendor"],
            "price": row["price"],
            "unit": row["unit"],
            "recorded_at": row["recorded_at"],
        })
    return history


def get_item_trend(item_key: str, vendor: str, limit: int = 5) -> list[dict]:
    """Get recent price history for one item at one vendor."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT price, recorded_at FROM price_records
            WHERE item_key = ? AND vendor = ?
            ORDER BY recorded_at DESC LIMIT ?
        """, (item_key, vendor, limit)).fetchall()
    return [{"price": r["price"], "recorded_at": r["recorded_at"]} for r in rows]
