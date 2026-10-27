"""Streaming full-text search across session transcripts. Snippets are redacted before they leave here."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from themis.profiles.model import Profile
from themis.redact import redact_text
from themis.sessions.model import SessionSummary
from themis.sessions.timeline import iter_blocks, tool_result_text

CONTEXT_CHARS = 70
MAX_HITS_PER_SESSION = 3
MAX_HITS = 200


@dataclass
class SearchHit:
    profile: str
    session_id: str
    role: str  # user | assistant | tool_use | tool_result
    snippet: str
    timestamp: str | None = None


def _texts(o: dict[str, Any]) -> Iterator[tuple[str, str]]:
    msg = o.get("message")
    if not isinstance(msg, dict):
        return
    role = "user" if o.get("type") == "user" else "assistant"
    for b in iter_blocks(msg.get("content")):
        t = b.get("type")
        if t == "text":
            yield role, str(b.get("text", ""))
        elif t == "tool_use":
            yield "tool_use", orjson.dumps(b.get("input", {})).decode()
        elif t == "tool_result":
            yield "tool_result", tool_result_text(b)


def _snippet(text: str, pattern: re.Pattern[str]) -> str | None:
    # Redact the whole text first so a secret cut by the window cannot leak half-masked.
    clean = " ".join(redact_text(text).split())
    m = pattern.search(clean)
    if not m:
        return None
    a, b = max(0, m.start() - CONTEXT_CHARS), min(len(clean), m.end() + CONTEXT_CHARS)
    return ("…" if a else "") + clean[a:b] + ("…" if b < len(clean) else "")


def search_session(
    profile: Profile, summary: SessionSummary, query: str, cancelled: Callable[[], bool]
) -> list[SearchHit]:
    """Hits in one session's main transcript; one line is read at a time."""
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    raw_needle = query.lower().encode() if query.isascii() and not re.search(r'["\\\n\r\t]', query) else None
    hits: list[SearchHit] = []
    path: Path = profile.path / summary.transcript
    try:
        fh = path.open("rb")
    except OSError:
        return hits
    with fh:
        for line in fh:
            if cancelled() or len(hits) >= MAX_HITS_PER_SESSION:
                break
            if raw_needle is not None and raw_needle not in line.lower():
                continue
            try:
                o = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(o, dict) or o.get("type") not in ("user", "assistant") or o.get("isMeta"):
                continue
            for role, text in _texts(o):
                snip = _snippet(text, pattern)
                if snip:
                    hits.append(SearchHit(profile.name, summary.session_id, role, snip, o.get("timestamp")))
                    break
    return hits


def search_sessions(
    profiles: dict[str, Profile],
    sessions: list[SessionSummary],
    query: str,
    cancelled: Callable[[], bool],
    on_hit: Callable[[SearchHit], None],
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Search newest sessions first; stops at MAX_HITS or when cancelled. Returns total hits."""
    query = query.strip()
    if not query:
        return 0
    ordered = sorted(sessions, key=lambda s: s.ended_at or "", reverse=True)
    total = 0
    for i, s in enumerate(ordered, 1):
        if cancelled() or total >= MAX_HITS:
            break
        profile = profiles.get(s.profile)
        if profile:
            for hit in search_session(profile, s, query, cancelled):
                on_hit(hit)
                total += 1
        if progress:
            progress(i, len(ordered))
    return total
