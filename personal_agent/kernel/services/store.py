"""SQLite store owned by the kernel. Plugins never open their own connections."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from personal_agent.kernel.models import Delivery, Feedback, Item, RankedItem

MIGRATIONS: list[str] = [
    """
    CREATE TABLE items (
        id TEXT PRIMARY KEY,
        plugin TEXT NOT NULL,
        json TEXT NOT NULL,
        first_seen TEXT NOT NULL
    );
    CREATE TABLE rankings (
        item_id TEXT NOT NULL,
        run_at TEXT NOT NULL,
        score REAL NOT NULL,
        reason TEXT NOT NULL
    );
    CREATE TABLE deliveries (
        item_id TEXT NOT NULL,
        output_id TEXT NOT NULL,
        external_ref TEXT,
        delivered_at TEXT NOT NULL,
        PRIMARY KEY (item_id, output_id)
    );
    CREATE TABLE feedback (
        item_id TEXT NOT NULL,
        output_id TEXT NOT NULL,
        signal TEXT NOT NULL,
        at TEXT NOT NULL,
        UNIQUE (item_id, output_id, signal)
    );
    """,
    """
    CREATE TABLE extractions (
        doc_hash TEXT NOT NULL,
        plugin TEXT NOT NULL,
        json TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (doc_hash, plugin)
    );
    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def default_db_path() -> Path:
    base = os.environ.get("PERSONAL_AGENT_DATA_DIR") or user_data_dir(
        "personal-agent", appauthor=False
    )
    return Path(base) / "agent.db"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


class Store:
    def __init__(self, path: Path | str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.conn:
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {i}")

    # --- items & rankings ---------------------------------------------------------------

    def record_items(self, items: Iterable[Item], now: datetime) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO items (id, plugin, json, first_seen) VALUES (?, ?, ?, ?)",
                [(i.id, i.plugin, i.model_dump_json(), _iso(now)) for i in items],
            )

    def record_rankings(self, ranked: Iterable[RankedItem], now: datetime) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO rankings (item_id, run_at, score, reason) VALUES (?, ?, ?, ?)",
                [(r.id, _iso(now), r.score, r.reason) for r in ranked],
            )

    # --- deliveries ---------------------------------------------------------------------

    def delivered_outputs(self, item_ids: Sequence[str]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {i: set() for i in item_ids}
        if not item_ids:
            return result
        placeholders = ",".join("?" * len(item_ids))
        rows = self.conn.execute(
            f"SELECT item_id, output_id FROM deliveries WHERE item_id IN ({placeholders})",
            list(item_ids),
        )
        for row in rows:
            result[row["item_id"]].add(row["output_id"])
        return result

    def record_deliveries(self, deliveries: Iterable[Delivery], now: datetime) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO deliveries (item_id, output_id, external_ref, delivered_at)"
                " VALUES (?, ?, ?, ?)",
                [(d.item_id, d.output_id, d.external_ref, _iso(now)) for d in deliveries],
            )

    def forget_deliveries(self, output_id: str, item_ids: Iterable[str]) -> int:
        """Drop delivery records so the items can be delivered to ``output_id`` again and are
        no longer checked for feedback there (used after ``agent outputs clear``)."""
        with self.conn:
            cur = self.conn.executemany(
                "DELETE FROM deliveries WHERE output_id = ? AND item_id = ?",
                [(output_id, i) for i in item_ids],
            )
        return cur.rowcount

    def count_delivered_since(self, plugin: str, since: datetime) -> int:
        """Distinct items of ``plugin`` delivered to any output since ``since``."""
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT d.item_id) FROM deliveries d JOIN items i ON i.id = d.item_id"
            " WHERE i.plugin = ? AND d.delivered_at >= ?",
            (plugin, _iso(since)),
        ).fetchone()
        return int(row[0])

    def deliveries_since(self, output_id: str, since: datetime) -> list[Delivery]:
        rows = self.conn.execute(
            "SELECT item_id, output_id, external_ref FROM deliveries"
            " WHERE output_id = ? AND delivered_at >= ?",
            (output_id, _iso(since)),
        )
        return [Delivery(**dict(r)) for r in rows]

    # --- feedback -----------------------------------------------------------------------

    def record_feedback(self, feedback: Iterable[Feedback]) -> int:
        """Store feedback signals. Idempotent: a signal equal to the latest one already stored
        for the same (item, output) is ignored; a changed mind (👍 then 👎 then 👍 again) is
        stored with its new time, so the latest signal wins. Returns the number of changes."""
        changed = 0
        with self.conn:
            for f in feedback:
                row = self.conn.execute(
                    "SELECT signal FROM feedback WHERE item_id = ? AND output_id = ?"
                    " ORDER BY at DESC LIMIT 1",
                    (f.item_id, f.output_id),
                ).fetchone()
                if row is not None and row["signal"] == f.signal:
                    continue
                self.conn.execute(
                    "INSERT INTO feedback (item_id, output_id, signal, at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (item_id, output_id, signal) DO UPDATE SET at = excluded.at",
                    (f.item_id, f.output_id, f.signal, _iso(f.at)),
                )
                changed += 1
        return changed

    def find_items(self, id_prefix: str) -> list[Item]:
        """Items whose id starts with ``id_prefix`` (a short id from a digest or calendar)."""
        rows = self.conn.execute(
            "SELECT json FROM items WHERE id LIKE ? ORDER BY first_seen DESC",
            (id_prefix.lower().replace("%", "").replace("_", "") + "%",),
        )
        return [Item(**json.loads(r["json"])) for r in rows]

    def recent_feedback(self, plugin: str, limit: int = 20) -> list[tuple[Item, str]]:
        """Latest signal per item for ``plugin``, newest first."""
        rows = self.conn.execute(
            """
            SELECT i.json AS json, f.signal AS signal
            FROM feedback f JOIN items i ON i.id = f.item_id
            WHERE i.plugin = ?
              AND f.at = (SELECT MAX(at) FROM feedback f2 WHERE f2.item_id = f.item_id)
            GROUP BY f.item_id
            ORDER BY f.at DESC
            LIMIT ?
            """,
            (plugin, limit),
        )
        return [(Item(**json.loads(r["json"])), r["signal"]) for r in rows]

    # --- extraction cache ---------------------------------------------------------------

    def get_extraction(self, doc_hash: str, plugin: str) -> list[dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT json FROM extractions WHERE doc_hash = ? AND plugin = ?", (doc_hash, plugin)
        ).fetchone()
        return None if row is None else list(json.loads(row["json"]))

    def put_extraction(
        self, doc_hash: str, plugin: str, items: list[dict[str, Any]], now: datetime
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO extractions (doc_hash, plugin, json, at)"
                " VALUES (?, ?, ?, ?)",
                (doc_hash, plugin, json.dumps(items), _iso(now)),
            )

    # --- key/value (e.g. ids of containers an output created) ---------------------------

    def get_kv(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_kv(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
