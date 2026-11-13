"""Long-term memory: facts the dog has been told, plus a log of who it has seen.
SQLite with full-text search; lives in data/memory.db."""
import re
import sqlite3
import threading
import time

from .config import DATA


class Memory:
    def __init__(self, path=None):
        self.db = sqlite3.connect(str(path or DATA / "memory.db"), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts USING fts5(text, about, created UNINDEXED);
                CREATE TABLE IF NOT EXISTS sightings (name TEXT PRIMARY KEY, last REAL, count INTEGER);
            """)

    def remember(self, text: str, about: str = "") -> str:
        text = text.strip()
        if not text:
            return "nothing to remember"
        with self.lock:
            dup = self.db.execute("SELECT rowid FROM facts WHERE text = ?", (text,)).fetchone()
            if dup:
                return "already knew that"
            self.db.execute("INSERT INTO facts VALUES (?, ?, ?)", (text, about, time.time()))
            self.db.commit()
        return "remembered"

    def recall(self, query: str, limit: int = 5) -> list[str]:
        words = re.findall(r"\w+", query.lower())
        if not words:
            return []
        q = " OR ".join(f'"{w}"' for w in words if len(w) > 2) or f'"{words[0]}"'
        with self.lock:
            rows = self.db.execute(
                "SELECT text FROM facts WHERE facts MATCH ? ORDER BY rank LIMIT ?",
                (q, limit)).fetchall()
        return [r[0] for r in rows]

    def forget(self, query: str) -> int:
        hits = self.recall(query, limit=1)
        if not hits:
            return 0
        with self.lock:
            n = self.db.execute("DELETE FROM facts WHERE text = ?", (hits[0],)).rowcount
            self.db.commit()
        return n

    def recent(self, limit: int = 5) -> list[str]:
        with self.lock:
            rows = self.db.execute(
                "SELECT text FROM facts ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]

    def saw(self, name: str) -> float | None:
        """Log a sighting; returns when this person was previously seen (or None)."""
        now = time.time()
        with self.lock:
            row = self.db.execute("SELECT last FROM sightings WHERE name = ?", (name,)).fetchone()
            self.db.execute(
                "INSERT INTO sightings VALUES (?, ?, 1) ON CONFLICT(name) "
                "DO UPDATE SET last = excluded.last, count = count + 1", (name, now))
            self.db.commit()
        return row[0] if row else None
