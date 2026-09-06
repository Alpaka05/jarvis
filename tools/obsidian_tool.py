"""Obsidian-Vault: Notizen durchsuchen, lesen, anlegen und ergänzen.

Ein Vault ist ein Ordner mit Markdown-Dateien. Das Tool arbeitet direkt auf dem
Dateisystem, Obsidian muss dafür nicht laufen. Schreibend gibt es nur Anlegen und
Anhängen; bestehende Notizen werden nie überschrieben oder gelöscht.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional

from config import config
from tools.base import BaseTool, ToolResult

SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules", ".vscode"}
SKIP_DIR_SUFFIXES = (".dSYM",)
MAX_READ_CHARS = 12000
MAX_SEARCH_HITS = 15


class ObsidianTool(BaseTool):
    name = "obsidian"
    description = (
        "Zugriff auf die persönlichen Notizen des Nutzers in Obsidian (Markdown-Vault). "
        "search durchsucht alle Notizen im Volltext (auch für Fragen wie 'Was steht in meinen Notizen zu X?'), "
        "read liest eine Notiz vollständig, list zeigt Ordner/Notizen (optional die zuletzt geänderten), "
        "create legt eine neue Notiz an, append hängt Text an eine bestehende Notiz an, "
        "daily öffnet oder erstellt die heutige Tagesnotiz (YYYY-MM-DD.md). "
        "Notizen werden über ihren Namen (ohne .md) oder relativen Pfad angesprochen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read", "list", "create", "append", "daily"],
                "description": "Gewünschte Aktion.",
            },
            "query": {"type": "string", "description": "Suchbegriffe (search), Groß-/Kleinschreibung egal."},
            "note": {
                "type": "string",
                "description": "Name oder relativer Pfad der Notiz (read/append/create), z.B. 'HomeLab' oder 'Ethik/Vorlesung 3'.",
            },
            "folder": {
                "type": "string",
                "description": "Ordner relativ zum Vault (list: nur diesen Ordner; create: Notiz dort anlegen).",
            },
            "content": {"type": "string", "description": "Markdown-Inhalt (create) bzw. anzuhängender Text (append/daily)."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optionale Tags für das Frontmatter (create).",
            },
            "recent": {
                "type": "integer",
                "description": "list: nur die N zuletzt geänderten Notizen des ganzen Vaults zeigen.",
            },
        },
        "required": ["action"],
    }

    def __init__(self, vault: Optional[str | Path] = None):
        raw = vault if vault is not None else config.OBSIDIAN_VAULT
        self.vault: Optional[Path] = Path(raw).expanduser().resolve() if raw else None

    # ── Bestätigung ──────────────────────────────────────────────────────────

    def confirmation_prompt(self, **kwargs) -> Optional[str]:
        action = kwargs.get("action")
        text = (kwargs.get("content") or "").strip()
        preview = text[:300] + ("…" if len(text) > 300 else "")
        if action == "create":
            where = kwargs.get("folder") or "Vault-Wurzel"
            return f"Neue Obsidian-Notiz '{kwargs.get('note', '?')}' in {where} anlegen\nInhalt: {preview}"
        if action == "append":
            return f"An Obsidian-Notiz '{kwargs.get('note', '?')}' anhängen:\n{preview}"
        if action == "daily" and text:
            return f"An die heutige Tagesnotiz anhängen:\n{preview}"
        return None

    # ── Hilfsfunktionen ──────────────────────────────────────────────────────

    def _check(self) -> Optional[ToolResult]:
        if self.vault is None:
            return ToolResult.fail("Kein Obsidian-Vault konfiguriert (OBSIDIAN_VAULT in .env setzen).")
        if not self.vault.is_dir():
            return ToolResult.fail(f"Obsidian-Vault nicht gefunden: {self.vault}")
        return None

    def _notes(self, folder: Optional[Path] = None) -> List[Path]:
        root = folder or self.vault
        result: List[Path] = []
        for path in sorted(root.rglob("*.md")):
            rel_parts = path.relative_to(self.vault).parts[:-1]
            if any(p in SKIP_DIRS or p.endswith(SKIP_DIR_SUFFIXES) for p in rel_parts):
                continue
            result.append(path)
        return result

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.vault).with_suffix("").as_posix()

    def _safe_path(self, rel: str, must_exist: bool) -> Path | ToolResult:
        """Wandelt einen relativen Pfad/Namen in einen Pfad im Vault um (kein Ausbruch aus dem Vault)."""
        rel = rel.strip().strip("/").replace("\\", "/")
        if not rel:
            return ToolResult.fail("Bitte einen Notiznamen angeben ('note').")
        if not rel.lower().endswith(".md"):
            rel += ".md"
        path = (self.vault / rel).resolve()
        if self.vault not in path.parents:
            return ToolResult.fail("Pfad liegt außerhalb des Vaults.")
        if must_exist and not path.is_file():
            match = self._find_by_name(Path(rel).stem)
            if match is None:
                return ToolResult.fail(f"Notiz '{rel[:-3]}' nicht gefunden.")
            return match
        return path

    def _find_by_name(self, name: str) -> Optional[Path]:
        """Sucht eine Notiz nur anhand des Dateinamens, wie Obsidian bei [[Links]]."""
        lowered = name.lower()
        exact = [p for p in self._notes() if p.stem.lower() == lowered]
        if exact:
            return exact[0]
        partial = [p for p in self._notes() if lowered in p.stem.lower()]
        return partial[0] if len(partial) == 1 else None

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def _folder_path(self, folder: Optional[str]) -> Path | ToolResult:
        if not folder:
            return self.vault
        path = (self.vault / folder.strip().strip("/")).resolve()
        if path != self.vault and self.vault not in path.parents:
            return ToolResult.fail("Ordner liegt außerhalb des Vaults.")
        return path

    # ── Aktionen ─────────────────────────────────────────────────────────────

    def execute(self, action: str = "list", **kwargs) -> ToolResult:
        err = self._check()
        if err:
            return err
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return ToolResult.fail(f"Unbekannte Obsidian-Aktion: {action}")
        try:
            return handler(**kwargs)
        except OSError as e:
            return ToolResult.fail(f"Dateizugriff fehlgeschlagen: {e}")

    def _do_search(self, query: str = "", **_) -> ToolResult:
        terms = [t.lower() for t in re.split(r"\s+", (query or "").strip()) if t]
        if not terms:
            return ToolResult.fail("Bitte Suchbegriffe angeben ('query').")
        hits = []
        for path in self._notes():
            text = self._read(path)
            lowered = text.lower()
            name_hit = all(t in path.stem.lower() for t in terms)
            if not name_hit and not all(t in lowered for t in terms):
                continue
            snippets = _snippets(text, terms)
            hits.append({"note": self._rel(path), "snippets": snippets, "name_match": name_hit})
        if not hits:
            return ToolResult.ok(f"Keine Notiz enthält '{query}'.", data=[])
        hits.sort(key=lambda h: (not h["name_match"], h["note"].lower()))
        shown = hits[:MAX_SEARCH_HITS]
        lines = []
        for h in shown:
            lines.append(f"• {h['note']}")
            lines.extend(f"    …{s}…" for s in h["snippets"][:2])
        more = f"\n(+{len(hits) - len(shown)} weitere)" if len(hits) > len(shown) else ""
        return ToolResult.ok(f"{len(hits)} Notiz(en) zu '{query}':\n" + "\n".join(lines) + more, data=hits)

    def _do_read(self, note: str = "", **_) -> ToolResult:
        path = self._safe_path(note, must_exist=True)
        if isinstance(path, ToolResult):
            return path
        text = self._read(path)
        clipped = len(text) > MAX_READ_CHARS
        body = text[:MAX_READ_CHARS] + ("\n\n[… gekürzt]" if clipped else "")
        return ToolResult.ok(f"# {self._rel(path)}\n\n{body}", data={"note": self._rel(path), "content": text})

    def _do_list(self, folder: Optional[str] = None, recent: Optional[int] = None, **_) -> ToolResult:
        if recent:
            notes = sorted(self._notes(), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, int(recent))]
            lines = [f"{date.fromtimestamp(p.stat().st_mtime).isoformat()}  {self._rel(p)}" for p in notes]
            return ToolResult.ok(
                f"Zuletzt geänderte Notizen:\n" + "\n".join(lines), data=[self._rel(p) for p in notes]
            )
        root = self._folder_path(folder)
        if isinstance(root, ToolResult):
            return root
        if not root.is_dir():
            return ToolResult.fail(f"Ordner '{folder}' nicht gefunden.")
        dirs = sorted(
            d.name for d in root.iterdir()
            if d.is_dir() and d.name not in SKIP_DIRS and not d.name.endswith(SKIP_DIR_SUFFIXES)
        )
        notes = sorted(p.stem for p in root.glob("*.md"))
        label = folder.strip("/") if folder else "Vault"
        parts = []
        if dirs:
            parts.append("Ordner: " + ", ".join(f"{d}/" for d in dirs))
        parts.append(f"{len(notes)} Notizen: " + (", ".join(notes) if notes else "–"))
        return ToolResult.ok(f"{label}\n" + "\n".join(parts), data={"folders": dirs, "notes": notes})

    def _do_create(
        self, note: str = "", content: str = "", folder: Optional[str] = None, tags: Optional[Iterable[str]] = None, **_
    ) -> ToolResult:
        root = self._folder_path(folder)
        if isinstance(root, ToolResult):
            return root
        rel = (Path(folder.strip("/")) / note.strip()) if folder else Path(note.strip())
        path = self._safe_path(rel.as_posix(), must_exist=False)
        if isinstance(path, ToolResult):
            return path
        if path.exists():
            return ToolResult.fail(f"Notiz '{self._rel(path)}' existiert bereits. Nutze 'append', um zu ergänzen.")
        text = (content or "").strip()
        tag_list = [t.strip().lstrip("#") for t in (tags or []) if t and t.strip()]
        if tag_list:
            text = "---\ntags:\n" + "".join(f"  - {t}\n" for t in tag_list) + "---\n\n" + text
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        return ToolResult.ok(f"Notiz '{self._rel(path)}' angelegt.", data={"note": self._rel(path)})

    def _do_append(self, note: str = "", content: str = "", **_) -> ToolResult:
        path = self._safe_path(note, must_exist=True)
        if isinstance(path, ToolResult):
            return path
        return self._append(path, content)

    def _append(self, path: Path, content: str) -> ToolResult:
        text = (content or "").strip()
        if not text:
            return ToolResult.fail("Bitte den anzuhängenden Text angeben ('content').")
        existing = self._read(path)
        sep = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(sep + text + "\n")
        return ToolResult.ok(f"An '{self._rel(path)}' angehängt.", data={"note": self._rel(path)})

    def _do_daily(self, content: str = "", **_) -> ToolResult:
        today = date.today().isoformat()
        path = self._find_by_name(today) or (self.vault / f"{today}.md")
        created = False
        if not path.exists():
            path.write_text(f"# {today}\n", encoding="utf-8")
            created = True
        if (content or "").strip():
            res = self._append(path, content)
            if not res.success:
                return res
            prefix = "Tagesnotiz angelegt und ergänzt" if created else "Tagesnotiz ergänzt"
            return ToolResult.ok(f"{prefix}: {self._rel(path)}", data={"note": self._rel(path), "created": created})
        text = self._read(path)
        head = "Tagesnotiz angelegt" if created else "Heutige Tagesnotiz"
        return ToolResult.ok(f"{head} ({self._rel(path)}):\n\n{text[:MAX_READ_CHARS]}", data={"note": self._rel(path), "content": text})


def _snippets(text: str, terms: List[str], width: int = 70, limit: int = 3) -> List[str]:
    """Kurze Textausschnitte rund um die Fundstellen, Zeilenumbrüche geglättet."""
    lowered = text.lower()
    out: List[str] = []
    last_end = -1
    for term in terms:
        start = 0
        while len(out) < limit:
            idx = lowered.find(term, start)
            if idx < 0:
                break
            if idx > last_end:
                a, b = max(0, idx - width), min(len(text), idx + len(term) + width)
                out.append(re.sub(r"\s+", " ", text[a:b]).strip())
                last_end = b
            start = idx + len(term)
    return out
