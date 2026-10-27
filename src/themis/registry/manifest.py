"""ARD-style manifest of every skill, agent, command, MCP server and plugin Themis can see.

The field layout follows the ARD discovery documents as far as they could be determined offline (see
DECISIONS.md: the specification text could not be fetched, so this is a best-effort shape with an
explicit `spec` marker). Secrets never appear: MCP entries carry only transport and redacted summary.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from themis.registry.urn import TYPE_OF_KIND, make_urn
from themis.resources.model import Resource
from themis.sessions.meta import resource_tags

SPEC = "ard-compat/unverified"
MANIFEST_VERSION = 1


def _entry(urn: str, members: list[Resource], tags: list[str]) -> dict[str, Any]:
    first = members[0]
    hashes = sorted({m.content_hash for m in members if m.content_hash})
    entry: dict[str, Any] = {
        "urn": urn,
        "type": TYPE_OF_KIND[first.kind],
        "name": first.name,
        "description": next((m.description for m in members if m.description), None),
        "origin": first.origin,
        "profiles": sorted({m.profile for m in members}),
        "scope": first.scope,
        "tags": tags,
        "content_hash": hashes[0] if len(hashes) == 1 else None,
        "tokens": {"startup_estimate": first.tokens_startup, "full_estimate": first.tokens_full},
    }
    if len(hashes) > 1:  # drifted copies share one URN; list each variant
        entry["variants"] = [
            {"profiles": sorted({m.profile for m in members if m.content_hash == h}), "content_hash": h} for h in hashes
        ]
    version = next((m.extra.get("version") for m in members if m.extra.get("version")), None)
    if version:
        entry["version"] = version
    if first.kind == "mcp":
        entry["transport"] = first.extra.get("transport")
    if first.kind == "plugin":
        entry["marketplace"] = first.extra.get("marketplace")
    return entry


def build_manifest(
    resources: list[Resource], host: str = "local", meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    groups: dict[str, list[Resource]] = defaultdict(list)
    for r in resources:
        urn = make_urn(r, host)
        if urn:
            groups[urn].append(r)
    entries = []
    for urn in sorted(groups):
        members = groups[urn]
        tags = resource_tags(meta, members[0].kind, members[0].name) if meta is not None else []
        entries.append(_entry(urn, members, tags))
    return {
        "spec": SPEC,
        "manifest_version": MANIFEST_VERSION,
        "generator": "themis",
        "host": host,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "count": len(entries),
        "resources": entries,
    }
