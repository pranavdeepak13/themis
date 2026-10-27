"""ARD manifest (`ard.json`) of every skill, agent, command, MCP server and plugin Themis can see.

Follows ARD v0.91 (https://agenticresourcediscovery.org/spec/): a document with an `entries` array;
each entry has `identifier`, `displayName`, `type` (media type) and exactly one of `url` or `data`.
Themis-specific facts (profiles, scope, hashes, token estimates, variants) go under `metadata` as flat scalar
values, as the schema requires.
Secrets never appear: MCP entries carry only redacted data.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any

from themis.registry.queries import representative_queries
from themis.registry.urn import DEFAULT_PUBLISHER, KIND_OF, make_urn
from themis.resources.model import Resource
from themis.sessions.meta import resource_tags

SPEC = "ARD v0.91"
# skill, MCP server and agent-card types are named in the spec; the others have no registered type,
# so they use a clearly non-standard vnd type and carry `data` instead of a file URL.
MEDIA_TYPE = {
    "skill": "application/agent-skills+md",
    "mcp": "application/mcp-server-card+json",
    "agent": "application/vnd.themis.claude-subagent+md",
    "command": "application/vnd.themis.claude-command+md",
    "plugin": "application/vnd.themis.claude-plugin+json",
}


def _locator(first: Resource) -> dict[str, Any]:
    """Exactly one of url / data."""
    if first.kind in ("skill", "agent", "command") and first.path and not first.broken:
        return {"url": Path(first.path).resolve().as_uri()}
    data: dict[str, Any] = {"name": first.name, "kind": first.kind}
    if first.kind == "mcp":
        cfg = first.extra.get("config", {})
        data |= {"transport": first.extra.get("transport"), "config": cfg}  # already redacted
    if first.kind == "plugin":
        data |= {"marketplace": first.extra.get("marketplace"), "version": first.extra.get("version")}
    return {"data": data}


def _entry(urn: str, members: list[Resource], tags: list[str]) -> dict[str, Any]:
    first = members[0]
    hashes = sorted({m.content_hash for m in members if m.content_hash})
    description = next((m.description for m in members if m.description), None)
    # ARD requires metadata values to be scalars (string, number, boolean or null), so lists are comma-joined
    meta: dict[str, Any] = {
        "themis_origin": first.origin,
        "themis_scope": first.scope,
        "themis_profiles": ",".join(sorted({m.profile for m in members})),
        "themis_content_hash": hashes[0] if len(hashes) == 1 else None,
        "themis_tokens_startup": first.tokens_startup,
        "themis_tokens_full": first.tokens_full,
        "themis_queries": "heuristic",
    }
    if len(hashes) > 1:  # drifted copies share one identifier; list each variant as hash=profiles
        meta["themis_variants"] = ";".join(
            f"{h}={','.join(sorted({m.profile for m in members if m.content_hash == h}))}" for h in hashes
        )
    entry: dict[str, Any] = {
        "identifier": urn,
        "displayName": first.name,
        "type": MEDIA_TYPE[first.kind],
        **_locator(first),
        "representativeQueries": representative_queries(first.name, description, first.kind),
        "metadata": meta,
    }
    if description:
        entry["description"] = description
    if tags:
        entry["tags"] = tags
    if version := next((m.extra.get("version") for m in members if m.extra.get("version")), None):
        entry["version"] = str(version)
    return entry


def build_manifest(
    resources: list[Resource], publisher: str = DEFAULT_PUBLISHER, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    groups: dict[str, list[Resource]] = defaultdict(list)
    for r in resources:
        if r.kind in KIND_OF and (urn := make_urn(r, publisher)):
            groups[urn].append(r)
    entries = []
    for urn in sorted(groups):
        members = groups[urn]
        tags = resource_tags(meta, members[0].kind, members[0].name) if meta is not None else []
        entries.append(_entry(urn, members, tags))
    # top-level members other than `entries` are ignored by ARD consumers
    return {
        "entries": entries,
        "themis": {
            "spec": SPEC,
            "generatedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "publisher": publisher,
            "note": "representativeQueries are heuristic (see themis_queries); no trust manifest is published",
        },
    }
