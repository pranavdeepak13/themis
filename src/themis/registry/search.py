"""Local registry search: keyword scoring with field filters, behind a backend interface.

`SearchBackend` is the seam for a later embedding backend; only the keyword backend exists now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class Filters:
    type: str | None = None
    profile: str | None = None
    scope: str | None = None
    origin: str | None = None
    tag: str | None = None


@dataclass(frozen=True)
class Hit:
    score: float
    entry: dict[str, Any]


class SearchBackend(Protocol):
    def search(self, entries: list[dict[str, Any]], query: str, filters: Filters, limit: int) -> list[Hit]: ...


def _passes(e: dict[str, Any], f: Filters) -> bool:
    return (
        (f.type is None or e.get("type") == f.type)
        and (f.profile is None or f.profile in e.get("profiles", []))
        and (f.scope is None or e.get("scope") == f.scope)
        and (f.origin is None or e.get("origin") == f.origin)
        and (f.tag is None or f.tag.lower() in e.get("tags", []))
    )


class KeywordBackend:
    """Every query word must appear somewhere; name hits weigh most, then tags, then description."""

    def search(self, entries: list[dict[str, Any]], query: str, filters: Filters, limit: int) -> list[Hit]:
        words = [w.lower() for w in _WORD.findall(query)]
        hits: list[Hit] = []
        for e in entries:
            if not _passes(e, filters):
                continue
            if not words:
                hits.append(Hit(0.0, e))
                continue
            name = str(e.get("name", "")).lower()
            desc = str(e.get("description") or "").lower()
            tags = " ".join(e.get("tags", []))
            score = 0.0
            for w in words:
                s = (3.0 if w in name else 0.0) + (2.0 if w in tags else 0.0) + (1.0 if w in desc else 0.0)
                if name == w:
                    s += 3.0
                if s == 0:
                    score = 0.0
                    break
                score += s
            if score:
                hits.append(Hit(score, e))
        hits.sort(key=lambda h: (-h.score, h.entry["urn"]))
        return hits[:limit]


def search(
    manifest: dict[str, Any],
    query: str,
    filters: Filters | None = None,
    limit: int = 20,
    backend: SearchBackend | None = None,
) -> list[Hit]:
    return (backend or KeywordBackend()).search(manifest["resources"], query, filters or Filters(), limit)


def inspect(manifest: dict[str, Any], urn: str) -> dict[str, Any] | None:
    return next((e for e in manifest["resources"] if e["urn"] == urn), None)
