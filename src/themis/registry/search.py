"""Local registry search shaped like ARD `POST /search` (v0.91 section 5.3).

Request (ard.openapi.yaml SearchRequest): `query` {`text`, `filter`}, `federation`, `pageSize` (default 10, max 100
per the spec text), `pageToken`. `filter` is {term: [values]}: OR within a term, AND across terms, a bare scalar
counts as one value. Response: `results[]` (each with `identifier`, integer `score` 0-100 and `source` URI) and
`pageToken`; `referrals` is only returned in referral mode, which a single local registry never uses.
Ranking sits behind `SearchBackend`, so an embedding backend can replace keyword scoring later.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from themis.registry.urn import parse_urn

_WORD = re.compile(r"[A-Za-z0-9]+")
DEFAULT_PAGE = 10
MAX_PAGE = 100


def term_values(entry: dict[str, Any], term: str) -> list[str]:
    """Values an entry has for a filter term: top-level fields, `publisher` (from the URN), or a dotted path."""
    if term == "publisher":
        parsed = parse_urn(entry["identifier"])
        return [parsed["publisher"]] if parsed else []
    cur: Any = entry
    for part in term.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    if cur is None:
        return []
    if isinstance(cur, list):
        return [str(x) for x in cur]
    if isinstance(cur, str) and term.startswith("metadata.") and "," in cur:
        return [part.strip() for part in cur.split(",")]  # metadata lists are stored comma-joined
    return [str(cur)]


def passes(entry: dict[str, Any], filt: dict[str, Any]) -> bool:
    for term, wanted in filt.items():
        values = wanted if isinstance(wanted, list) else [wanted]
        have = {v.lower() for v in term_values(entry, term)}
        if not any(str(v).lower() in have for v in values):
            return False
    return True


class SearchBackend(Protocol):
    def rank(self, entries: list[dict[str, Any]], text: str) -> list[tuple[int, dict[str, Any]]]:
        """Score and order entries for the text; entries that do not match are omitted."""
        ...


class KeywordBackend:
    """Every query word must appear somewhere; name hits weigh most, then tags, then description."""

    def rank(self, entries: list[dict[str, Any]], text: str) -> list[tuple[int, dict[str, Any]]]:
        words = [w.lower() for w in _WORD.findall(text)]
        if not words:
            return [(0, e) for e in entries]
        scored: list[tuple[float, dict[str, Any]]] = []
        for e in entries:
            name = str(e.get("displayName", "")).lower()
            desc = str(e.get("description") or "").lower()
            tags = " ".join(e.get("tags", []))
            total = 0.0
            for w in words:
                s = (3.0 if w in name else 0.0) + (2.0 if w in tags else 0.0) + (1.0 if w in desc else 0.0)
                if name == w:
                    s += 3.0
                if s == 0:
                    total = 0.0
                    break
                total += s
            if total:
                scored.append((total, e))
        best = max((s for s, _ in scored), default=1.0)
        return sorted(((round(100 * s / best), e) for s, e in scored), key=lambda t: (-t[0], t[1]["identifier"]))


def search(
    manifest: dict[str, Any],
    text: str = "",
    filter: dict[str, Any] | None = None,  # noqa: A002 - the ARD field name
    page_size: int = DEFAULT_PAGE,
    page_token: str | None = None,
    backend: SearchBackend | None = None,
    source: str = "urn:themis:local",
) -> dict[str, Any]:
    entries = [e for e in manifest["entries"] if passes(e, filter or {})]
    ranked = (backend or KeywordBackend()).rank(entries, text)
    size = max(1, min(page_size, MAX_PAGE))
    start = int(page_token) if page_token and page_token.isdigit() else 0
    page = ranked[start : start + size]
    out: dict[str, Any] = {
        "results": [
            {
                "identifier": e["identifier"],
                "score": int(s),
                "source": source,
                **{k: v for k, v in e.items() if k != "identifier"},
            }
            for s, e in page
        ],
    }
    if start + size < len(ranked):
        out["pageToken"] = str(start + size)
    return out


def inspect(manifest: dict[str, Any], urn: str) -> dict[str, Any] | None:
    return next((e for e in manifest["entries"] if e["identifier"] == urn), None)
