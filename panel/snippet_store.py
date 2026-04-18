"""
panel/snippet_store.py

User-saved named command snippets.
Stored in ~/.operonix/snippets.json as a list of dicts.
All I/O is synchronous (snippets are small and rarely written).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_SNIPPETS_PATH = Path.home() / ".operonix" / "snippets.json"


@dataclass
class Snippet:
    id: str
    name: str          # short label the user types to expand
    query_text: str    # full command text
    shortcut: str = "" # optional keyboard shortcut (display only, not registered)
    description: str = ""


class SnippetStore:
    """Load, save, and search user-defined command snippets."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _SNIPPETS_PATH
        self._snippets: list[Snippet] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._snippets = [Snippet(**item) for item in raw]
                log.info("snippet_store: loaded %d snippets", len(self._snippets))
        except Exception as exc:  # noqa: BLE001
            log.warning("snippet_store: failed to load — %s", exc)
            self._snippets = []

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps([asdict(s) for s in self._snippets], indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001
            log.error("snippet_store: failed to save — %s", exc)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        name: str,
        query_text: str,
        shortcut: str = "",
        description: str = "",
    ) -> Snippet:
        snippet = Snippet(
            id=str(uuid.uuid4()),
            name=name,
            query_text=query_text,
            shortcut=shortcut,
            description=description,
        )
        self._snippets.append(snippet)
        self._save()
        log.info("snippet_store: added '%s'", name)
        return snippet

    def remove(self, snippet_id: str) -> bool:
        before = len(self._snippets)
        self._snippets = [s for s in self._snippets if s.id != snippet_id]
        if len(self._snippets) < before:
            self._save()
            return True
        return False

    def update(self, snippet_id: str, **kwargs: str) -> bool:
        for snippet in self._snippets:
            if snippet.id == snippet_id:
                for key, value in kwargs.items():
                    if hasattr(snippet, key):
                        setattr(snippet, key, value)
                self._save()
                return True
        return False

    def all(self) -> list[Snippet]:
        return list(self._snippets)

    def find_by_name(self, name: str) -> Snippet | None:
        name_lower = name.lower()
        for snippet in self._snippets:
            if snippet.name.lower() == name_lower:
                return snippet
        return None

    def search(self, query: str) -> list[Snippet]:
        q = query.lower()
        return [
            s for s in self._snippets
            if q in s.name.lower() or q in s.query_text.lower() or q in s.description.lower()
        ]

    def expand(self, text: str) -> str | None:
        """
        If *text* matches a snippet name, return the expanded query.
        Otherwise return None.
        """
        snippet = self.find_by_name(text.strip())
        return snippet.query_text if snippet else None