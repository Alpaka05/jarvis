"""Langzeitgedächtnis auf SQLite-Basis.

Zwei Speicher:
  * facts          – dauerhafte Fakten über den Nutzer und seine Umgebung
                     (Name, Vorlieben, Personen, Geräte-IDs, Gewohnheiten)
  * conversations  – Protokoll aller Nutzer-/Assistenten-Nachrichten, damit
                     Jarvis frühere Gespräche durchsuchen kann

Die Fakten werden dem LLM bei jeder Anfrage im System-Prompt mitgegeben; das
LLM pflegt sie selbst über das memory-Tool (remember / update / forget).
"""
from __future__ import annotations

import functools
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

CATEGORIES = ("person", "preference", "device", "fact", "routine", "other")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    category   TEXT    NOT NULL DEFAULT 'fact',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _words(query: str) -> List[str]:
    return [w for w in re.split(r"[^\wäöüÄÖÜß]+", query.lower()) if len(w) >= 3]


def _locked(method):
    """Eine Verbindung, mehrere Threads (Hauptschleife, Tool-Threads, auch nach Timeout weiterlaufende):
    sqlite3 erlaubt das nur, wenn sich Aufrufe nicht überschneiden."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MemoryStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @_locked
    def close(self):
        self._conn.close()

    # ── Fakten ───────────────────────────────────────────────────────────────

    @_locked
    def add_fact(self, content: str, category: str = "fact") -> Dict[str, Any]:
        content = " ".join(content.split()).strip()
        if not content:
            raise ValueError("Leerer Inhalt")
        category = category if category in CATEGORIES else "other"
        existing = self._conn.execute(
            "SELECT * FROM facts WHERE lower(content) = lower(?)", (content,)
        ).fetchone()
        if existing:
            return dict(existing) | {"duplicate": True}
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO facts (content, category, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (content, category, now, now),
        )
        self._conn.commit()
        return {"id": cur.lastrowid, "content": content, "category": category, "created_at": now, "updated_at": now}

    @_locked
    def update_fact(self, fact_id: int, content: str) -> bool:
        content = " ".join(content.split()).strip()
        cur = self._conn.execute(
            "UPDATE facts SET content = ?, updated_at = ? WHERE id = ?", (content, _now(), fact_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    @_locked
    def delete_fact(self, fact_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._conn.commit()
        return cur.rowcount > 0

    @_locked
    def list_facts(self, limit: int = 200, category: Optional[str] = None) -> List[Dict[str, Any]]:
        if category:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY id LIMIT ?", (category, limit)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM facts ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def count_facts(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    @_locked
    def search_facts(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Einfache Wortsuche: Treffer werden nach Anzahl passender Wörter sortiert."""
        words = _words(query)
        if not words:
            return self.list_facts(limit)
        clauses = " OR ".join("lower(content) LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words]
        rows = self._conn.execute(f"SELECT * FROM facts WHERE {clauses}", params).fetchall()
        scored = []
        for r in rows:
            text = r["content"].lower()
            score = sum(1 for w in words if w in text)
            scored.append((score, r["id"], dict(r)))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [d for _, _, d in scored[:limit]]

    @_locked
    def facts_for_prompt(self, limit: int = 50) -> str:
        # Bei mehr Fakten als Platz die zuletzt gespeicherten/geänderten nehmen – sonst fielen
        # neue Fakten und Korrekturen still heraus. Ausgabe nach id, damit der Prompt stabil bleibt.
        rows = self._conn.execute(
            "SELECT * FROM facts ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        facts = sorted((dict(r) for r in rows), key=lambda f: f["id"])
        if not facts:
            return ""
        return "\n".join(f"- [#{f['id']}|{f['category']}] {f['content']}" for f in facts)

    # ── Gesprächsprotokoll ───────────────────────────────────────────────────

    @_locked
    def log_message(self, session_id: str, role: str, content: str):
        content = content.strip()
        if not content:
            return
        self._conn.execute(
            "INSERT INTO conversations (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content[:4000], _now()),
        )
        self._conn.commit()

    @_locked
    def search_conversations(self, query: str, days: int = 30, limit: int = 15) -> List[Dict[str, Any]]:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        words = _words(query)
        if words:
            clauses = " OR ".join("lower(content) LIKE ?" for _ in words)
            params: List[Any] = [since] + [f"%{w}%" for w in words]
            rows = self._conn.execute(
                f"SELECT * FROM conversations WHERE created_at >= ? AND ({clauses}) ORDER BY created_at DESC LIMIT ?",
                params + [limit * 3],
            ).fetchall()
            scored = []
            for r in rows:
                text = r["content"].lower()
                scored.append((sum(1 for w in words if w in text), r["created_at"], dict(r)))
            # beste Treffer zuerst, bei Gleichstand die neuesten
            scored.sort(key=lambda t: (-t[0], t[1]), reverse=False)
            scored.sort(key=lambda t: t[1], reverse=True)
            scored.sort(key=lambda t: -t[0])
            return [d for _, _, d in scored[:limit]]
        rows = self._conn.execute(
            "SELECT * FROM conversations WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?", (since, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def recent_sessions_summary(self, days: int = 7, limit: int = 30) -> List[Dict[str, Any]]:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self._conn.execute(
            "SELECT * FROM conversations WHERE created_at >= ? AND role = 'user' ORDER BY created_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]
