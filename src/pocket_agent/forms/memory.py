"""SQLite memory for form answers. One file under the user data dir."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryEntry:
    key: str
    value: str
    always_use: bool
    sensitive: bool
    source: str


class Memory:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                always_use INTEGER NOT NULL DEFAULT 0,
                sensitive INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS synonyms (
                key TEXT NOT NULL,
                label TEXT NOT NULL,
                UNIQUE(key, label)
            );
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def put(
        self,
        key: str,
        value: str,
        *,
        always_use: bool = False,
        sensitive: bool = False,
        source: str = "answer",
        label: str | None = None,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO entries (key, value, always_use, sensitive, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                always_use=excluded.always_use,
                sensitive=excluded.sensitive,
                source=excluded.source
            """,
            (key, value, int(always_use), int(sensitive), source),
        )
        if label:
            self._db.execute(
                "INSERT OR IGNORE INTO synonyms (key, label) VALUES (?, ?)",
                (key, label.strip().casefold()),
            )
        self._db.commit()

    def get(self, key: str) -> MemoryEntry | None:
        row = self._db.execute(
            "SELECT key, value, always_use, sensitive, source FROM entries WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return MemoryEntry(row[0], row[1], bool(row[2]), bool(row[3]), row[4])

    def has_value(self, key: str) -> bool:
        entry = self.get(key)
        return entry is not None and entry.value.strip() != ""

    def all_entries(self) -> list[MemoryEntry]:
        rows = self._db.execute(
            "SELECT key, value, always_use, sensitive, source FROM entries ORDER BY key"
        ).fetchall()
        return [MemoryEntry(row[0], row[1], bool(row[2]), bool(row[3]), row[4]) for row in rows]

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM entries WHERE key = ?", (key,))
        self._db.execute("DELETE FROM synonyms WHERE key = ?", (key,))
        self._db.commit()

    def keys(self) -> list[str]:
        return [row[0] for row in self._db.execute("SELECT key FROM entries ORDER BY key")]

    def reusable_keys(self) -> set[str]:
        """Keys the filler is allowed to reuse without asking again."""
        found = set()
        for row in self._db.execute(
            "SELECT key, value, always_use, sensitive FROM entries"
        ):
            if not str(row[1]).strip():
                continue
            if row[3] and not row[2]:
                continue
            found.add(row[0])
        return found

    def learned_labels(self) -> dict[str, str]:
        """Normalized label -> key. First write wins."""
        out: dict[str, str] = {}
        for key, label in self._db.execute("SELECT key, label FROM synonyms ORDER BY rowid"):
            out.setdefault(label, key)
        return out

    def remember_label(self, key: str, label: str) -> None:
        if not label.strip():
            return
        self._db.execute(
            "INSERT OR IGNORE INTO synonyms (key, label) VALUES (?, ?)",
            (key, label.strip().casefold()),
        )
        self._db.commit()

    def indexed_values(self, prefix: str) -> dict[int, dict[str, str]]:
        """work.0.title -> {0: {title: ...}}."""
        grouped: dict[int, dict[str, str]] = {}
        needle = prefix + "."
        for key, value in self._db.execute("SELECT key, value FROM entries"):
            if not key.startswith(needle):
                continue
            rest = key[len(needle):]
            index_text, _, field = rest.partition(".")
            if not index_text.isdigit() or not field or not str(value).strip():
                continue
            grouped.setdefault(int(index_text), {})[field] = value
        return grouped
