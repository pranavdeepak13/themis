"""Stable `urn:air:` identifiers for resources, following ARD v0.91 section 4.2.

Grammar: urn:air:<publisher>:[<namespace>:]<name>, where the publisher is a domain and the namespace
may span several segments. The resource type is NOT a URN segment in ARD (it is the entry's `type`
media type), so Themis puts the kind into the namespace to keep a skill and a command of the same
name apart:

  urn:air:<publisher>:<kind>:<scope>:<name>
  kind   skill | agent | command | mcp-server | plugin
  scope  "user" | "plugin.<plugin-name>" | "project.<dir-name>-<8 hex of the project path>"
  name   percent-encoded outside [A-Za-z0-9._-]

Profile names and file paths are not part of the URN, so moving, symlinking or sharing a resource
does not change it. Content and version live in the manifest entry.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, unquote

from themis.resources.model import Resource

PREFIX = "urn:air"
DEFAULT_PUBLISHER = "themis.local"  # ARD wants a domain; override with --publisher for a real one
KIND_OF = {"skill": "skill", "agent": "agent", "command": "command", "mcp": "mcp-server", "plugin": "plugin"}
_URN = re.compile(r"^urn:air:(?P<publisher>[^:]+):(?P<namespace>(?:[^:]+:)*)(?P<name>[^:]+)$")
_SAFE = "-._"


def _seg(text: str) -> str:
    return quote(text, safe=_SAFE)


def scope_of(r: Resource) -> str:
    if r.origin == "plugin":
        return "plugin." + _seg((r.origin_name or "unknown").partition("@")[0])
    if r.origin == "project":
        project = r.origin_name or ""
        base = project.rstrip("/").rsplit("/", 1)[-1] or "root"
        return f"project.{_seg(base)}-{hashlib.sha256(project.encode()).hexdigest()[:8]}"
    return "user"  # user and synced


def make_urn(r: Resource, publisher: str = DEFAULT_PUBLISHER) -> str | None:
    kind = KIND_OF.get(r.kind)
    if kind is None:
        return None
    scope = "user" if r.kind == "plugin" else scope_of(r)
    return f"{PREFIX}:{publisher}:{kind}:{scope}:{_seg(r.name)}"


def parse_urn(urn: str) -> dict[str, str] | None:
    m = _URN.match(urn)
    if not m:
        return None
    return {
        "publisher": m["publisher"],
        "namespace": m["namespace"].rstrip(":"),
        "name": unquote(m["name"]),
    }
