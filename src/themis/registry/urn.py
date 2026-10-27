"""Stable `urn:air:` identifiers for resources.

Shape: urn:air:<host>:<type>:<namespace>:<name>
  host       configurable authority segment (default "local")
  type       skill | agent | command | mcp-server | plugin
  namespace  "user" for profile-level resources, "plugin.<plugin-name>" for plugin-provided ones,
             "project.<dir-name>-<8 hex of the project path>" for project-level ones
  name       resource name with every character outside [A-Za-z0-9._-] percent-encoded

Profile names and file paths are not part of the URN, so moving a file, symlinking it or sharing it
with another profile does not change it. Content and version live in the manifest, not the URN.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, unquote

from themis.resources.model import Resource

PREFIX = "urn:air"
TYPE_OF_KIND = {"skill": "skill", "agent": "agent", "command": "command", "mcp": "mcp-server", "plugin": "plugin"}
_URN = re.compile(r"^urn:air:(?P<host>[^:]+):(?P<type>[^:]+):(?P<ns>[^:]+):(?P<name>[^:]+)$")
_SAFE = "-._"


def _seg(text: str) -> str:
    return quote(text, safe=_SAFE)


def namespace_of(r: Resource) -> str:
    if r.origin == "plugin":
        return "plugin." + _seg((r.origin_name or "unknown").partition("@")[0])
    if r.origin == "project":
        project = r.origin_name or ""
        base = project.rstrip("/").rsplit("/", 1)[-1] or "root"
        return f"project.{_seg(base)}-{hashlib.sha256(project.encode()).hexdigest()[:8]}"
    return "user"  # user and synced


def make_urn(r: Resource, host: str = "local") -> str | None:
    typ = TYPE_OF_KIND.get(r.kind)
    if typ is None:
        return None
    ns = "user" if r.kind == "plugin" else namespace_of(r)
    return f"{PREFIX}:{_seg(host)}:{typ}:{ns}:{_seg(r.name)}"


def parse_urn(urn: str) -> dict[str, str] | None:
    m = _URN.match(urn)
    return {k: unquote(v) for k, v in m.groupdict().items()} if m else None
