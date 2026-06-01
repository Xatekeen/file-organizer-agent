"""
storage.py — SQLite-backed persistence layer.
Stores every move for undo support, tracks per-category stats,
and exposes the reflection queries used by the agent's learning loop.
"""

from __future__ import annotations
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager


DB_PATH = Path(__file__).parent.parent / "logs" / "agent.db"


def init_db() -> None:
    """Create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS moves (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL,
                new_path      TEXT NOT NULL,
                category      TEXT NOT NULL,
                confidence    REAL NOT NULL,
                size_bytes    INTEGER,
                creator       TEXT,
                signals       TEXT,
                timestamp     REAL NOT NULL,
                undone        INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS stats (
                category      TEXT PRIMARY KEY,
                file_count    INTEGER DEFAULT 0,
                total_bytes   INTEGER DEFAULT 0,
                confidence_sum REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS agent_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # Seed start time if first run
        conn.execute("""
            INSERT OR IGNORE INTO agent_meta(key, value)
            VALUES ('start_time', ?)
        """, (str(time.time()),))


@contextmanager
def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_move(
    original: Path,
    dest: Path,
    category: str,
    confidence: float,
    size_bytes: int,
    creator: str | None,
    signals: dict,
) -> int:
    """Record a file move and update category stats. Returns move id."""
    import json
    ts = time.time()
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO moves
              (original_path, new_path, category, confidence,
               size_bytes, creator, signals, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(original), str(dest), category, confidence,
              size_bytes, creator, json.dumps(signals), ts))
        move_id = cur.lastrowid

        conn.execute("""
            INSERT INTO stats(category, file_count, total_bytes, confidence_sum)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(category) DO UPDATE SET
                file_count     = file_count + 1,
                total_bytes    = total_bytes + excluded.total_bytes,
                confidence_sum = confidence_sum + excluded.confidence_sum
        """, (category, size_bytes, confidence))

    return move_id


def undo_last(n: int = 1) -> list[dict]:
    """
    Reverse the last n moves by moving files back to their original paths.
    Returns list of {original, restored} dicts for UI feedback.
    """
    import shutil
    results = []
    with _conn() as conn:
        rows = conn.execute("""
            SELECT id, original_path, new_path FROM moves
            WHERE undone = 0
            ORDER BY timestamp DESC
            LIMIT ?
        """, (n,)).fetchall()

        for row in rows:
            src  = Path(row["new_path"])
            dest = Path(row["original_path"])
            try:
                if src.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))
                conn.execute(
                    "UPDATE moves SET undone = 1 WHERE id = ?", (row["id"],)
                )
                results.append({
                    "original": row["original_path"],
                    "restored": str(dest),
                })
            except OSError as e:
                results.append({"error": str(e)})
    return results


def get_stats() -> dict:
    """Return per-category stats and session-level aggregates."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT category, file_count, total_bytes, confidence_sum
            FROM stats ORDER BY file_count DESC
        """).fetchall()

        start_ts = float(
            conn.execute(
                "SELECT value FROM agent_meta WHERE key='start_time'"
            ).fetchone()["value"]
        )

        total_moves = conn.execute(
            "SELECT COUNT(*) as c FROM moves WHERE undone=0"
        ).fetchone()["c"]

        unsorted_count = conn.execute(
            "SELECT COUNT(*) as c FROM moves WHERE category='_unsorted' AND undone=0"
        ).fetchone()["c"]

    categories = {}
    for row in rows:
        avg_conf = (row["confidence_sum"] / row["file_count"]
                    if row["file_count"] > 0 else 0)
        categories[row["category"]] = {
            "count":      row["file_count"],
            "bytes":      row["total_bytes"],
            "avg_conf":   round(avg_conf, 3),
        }

    return {
        "categories":    categories,
        "total_moves":   total_moves,
        "unsorted":      unsorted_count,
        "uptime_secs":   time.time() - start_ts,
        "start_time":    start_ts,
    }


def get_recent_moves(limit: int = 20) -> list[dict]:
    """Fetch the most recent move records for the live log view."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT original_path, new_path, category, confidence,
                   creator, timestamp, undone
            FROM moves
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def reflect() -> list[str]:
    """
    Analyse _unsorted files and suggest new rules.
    Returns human-readable suggestion strings.
    """
    with _conn() as conn:
        rows = conn.execute("""
            SELECT original_path FROM moves
            WHERE category = '_unsorted' AND undone = 0
            ORDER BY timestamp DESC LIMIT 50
        """).fetchall()

    suggestions = []
    ext_counts: dict[str, int] = {}
    for row in rows:
        ext = Path(row["original_path"]).suffix.lower()
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
        if count >= 2:
            suggestions.append(
                f"  Extension '{ext}' appeared {count}× in _unsorted — "
                f"consider adding it to a category rule."
            )
    return suggestions
