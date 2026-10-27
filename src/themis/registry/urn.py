"""Stable `urn:air:` identifiers for resources, following ARD v0.91 section 4.2.

Grammar: urn:air:<publisher>:[<namespace>:]<name>, where the publisher is a fully qualified domain and the
namespace may span several segments. The schema regex is ^urn:air:[a-zA-Z0-9.-]+(:[a-zA-Z0-9._-]+)+$.
The resource type is NOT a URN segment in ARD (it is the entry's `type` media type), so Themis puts the
kind into the namespace to keep a skill and a command of the same name apart:

  urn:air:<publisher>:<kind>:<scope>:<name>
  kind   skill | agent | command | mcp-server | plugin
  scope  "user" | "plugin.<plugin-name>" | "project.<dir-name>-<8 hex of the project path>"
  name   the resource name; characters outside [A-Za-z0-9._-] become "-" and a ".h<8 hex>" suffix of the
         original name's hash keeps different names apart (the schema allows only [A-Za-z0-9._-] per segment)

Profile names and file paths are not part of the URN, so moving, symlinking or sharing a resource
does not change it. Content and version live in the manifest entry.
"""

from __future__ import annotations

import hashlib
import re

from themis.resources.model import Resource

PREFIX = "urn:air"
DEFAULT_PUBLISHER = "themis.local"  # ARD wants a domain; override with --publisher for a real one
KIND_OF = {"skill": "skill", "agent": "agent", "command": "command", "mcp": "mcp-server", "plugin": "plugin"}
# The ARD entry schema's identifier pattern, with the parts named.
_URN = re.compile(r"^urn:air:(?P<publisher>[a-zA-Z0-9.-]+)(?P<rest>(?::[a-zA-Z0-9._-]+)+)$")
_PUBLISHER = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$")  # an FQDN: at least two labels
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def valid_publisher(publisher: str) -> bool:
    """ARD wants a fully qualified domain; single-label hosts and IP addresses are not accepted."""
    return bool(_PUBLISHER.match(publisher)) and not re.fullmatch(r"[0-9.]+", publisher)


def _seg(text: str, hashed: bool = True) -> str:
    """Make a schema-legal segment. Changed text gets a short hash of the original so names stay distinct."""
    if not _UNSAFE.search(text) and text:
        return text
    clean = _UNSAFE.sub("-", text).strip("-") or "x"
    return f"{clean}.h{hashlib.sha256(text.encode()).hexdigest()[:8]}" if hashed else clean


def scope_of(r: Resource) -> str:
    if r.origin == "plugin":
        return "plugin." + _seg((r.origin_name or "unknown").partition("@")[0], hashed=False)
    if r.origin == "project":
        project = r.origin_name or ""
        base = project.rstrip("/").rsplit("/", 1)[-1] or "root"
        return f"project.{_seg(base, hashed=False)}-{hashlib.sha256(project.encode()).hexdigest()[:8]}"
    return "user"  # user and synced


def make_urn(r: Resource, publisher: str = DEFAULT_PUBLISHER) -> str | None:
    kind = KIND_OF.get(r.kind)
    if kind is None:
        return None
    if not valid_publisher(publisher):
        raise ValueError(f"Publisher {publisher!r} must be a fully qualified domain name (for example example.com).")
    scope = "user" if r.kind == "plugin" else scope_of(r)
    return f"{PREFIX}:{publisher}:{kind}:{scope}:{_seg(r.name)}"


def parse_urn(urn: str) -> dict[str, str] | None:
    m = _URN.match(urn)
    if not m:
        return None
    *namespace, name = m["rest"].lstrip(":").split(":")
    return {"publisher": m["publisher"], "namespace": ":".join(namespace), "name": name}
