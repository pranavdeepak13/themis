"""Heuristic `representativeQueries` for ARD entries (2 to 5 sample requests per resource).

ARD says an entry SHOULD carry 2-5 queries a registry can build its semantic index from. Writing good ones
takes understanding, which Themis does not do (no LLM): these are derived deterministically from the
resource's own description by pulling out its "use when ..." clauses, with templated fallbacks. They are
marked `themis_queries: heuristic` in the entry metadata so a consumer knows they are approximations.
"""

from __future__ import annotations

import re

from themis.redact import redact_text

MAX_LEN = 110
_LEAD = re.compile(
    r"^(?:use|invoke|run|load|trigger)(?: this| the)?(?: skill| agent| command| tool)?\s*"
    r"(?:when(?:ever)?|if|for|to)\s+(?:the user |you |someone |users? |a user )?"
    r"(?:(?:wants?|needs?|asks?(?: for)?|says?|mentions?|is|are|requests?|has|have|would like)\s+(?:to\s+)?)?",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"\s*(?:;|,\s*or\s+|\bor\b(?=\s+(?:to|when|if)\b)|\.\s+)\s*", re.IGNORECASE)
_WORDS = re.compile(r"[A-Za-z0-9]+")


_CONJ = re.compile(r"^(?:and|or|but|when(?:ever)?|if|then|also|even if|especially|particularly)\s+", re.IGNORECASE)


def _clip(text: str) -> str:
    text = " ".join(text.split()).strip(" .,:;-")
    while (m := _CONJ.match(text)) is not None:  # "when you catch yourself ..." -> "you catch yourself ..."
        text = text[m.end() :]
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN].rsplit(" ", 1)[0]  # cut at a word boundary, no ellipsis
    return text.strip(" .,:;-")


def _usable(q: str) -> bool:
    """Keep phrases that read like a request: a few words, balanced quotes, not a stray fragment."""
    return len(q.split()) >= 3 and q.count('"') % 2 == 0 and not q.endswith(("...", "(", "/")) and "e.g" not in q


def _humanize(name: str) -> str:
    return " ".join(_WORDS.findall(name.replace("_", " "))).lower() or name


def representative_queries(name: str, description: str | None, kind: str) -> list[str]:
    """2 to 5 distinct sample queries for a resource."""
    out: list[str] = []

    def add(q: str) -> None:
        q = _clip(redact_text(q))
        if _usable(q) and q.lower() not in {x.lower() for x in out} and len(out) < 5:
            out.append(q)

    desc = " ".join((description or "").split())
    for sentence in re.split(r"(?<=[.!?])\s+", desc)[:6]:
        m = _LEAD.match(sentence)
        if m:  # "Use when the user wants to X or Y" -> "X", "Y"
            for part in _SPLIT.split(sentence[m.end() :]):
                add(part)
    if desc and len(out) < 2:
        add(re.split(r"(?<=[.!?])\s+", desc)[0])  # the first sentence of the description
    label = _humanize(name)
    noun = {"mcp": "MCP server", "plugin": "plugin"}.get(kind, kind)
    for fallback in (f"use the {label} {noun}", f"help me with {label}", f"which {noun} handles {label}"):
        if len(out) >= 2:
            break
        add(fallback)
    return out[:5]
