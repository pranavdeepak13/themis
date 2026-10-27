"""On-demand reading of a resource's body for the viewer (redacted)."""

from __future__ import annotations

from pathlib import Path

import orjson

from themis.redact import redact_text
from themis.resources.fsutil import parse_frontmatter, read_text
from themis.resources.model import Resource


def read_body(r: Resource, limit: int = 200_000) -> str:
    """Markdown body (frontmatter removed) for skills, commands, agents and CLAUDE.md; JSON for MCP."""
    if r.kind == "mcp":
        return "```json\n" + orjson.dumps(r.extra.get("config", {}), option=orjson.OPT_INDENT_2).decode() + "\n```"
    if r.kind in ("plugin", "marketplace", "hook", "permission") or not r.path:
        return ""
    text = read_text(Path(r.path), limit)
    if text is None:
        return "*Unreadable or broken link.*"
    _fm, body, ok = parse_frontmatter(text)
    return redact_text(body if ok else text)
