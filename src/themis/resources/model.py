"""Resource model shared by all scanners. Bodies are not stored; read them on demand."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# kinds: skill, command, agent, claude_md, hook, mcp, plugin, marketplace, permission
# origins: user (profile dir), synced (managed copies under skills/synced), plugin, project
KINDS = ["skill", "command", "agent", "claude_md", "hook", "mcp", "plugin", "marketplace", "permission"]


class Resource(BaseModel):
    kind: str
    name: str
    profile: str
    origin: str = "user"
    origin_name: str | None = None  # plugin id or project directory
    path: str | None = None  # main file (SKILL.md for skills) or defining config file
    realpath: str | None = None
    is_symlink: bool = False
    broken: bool = False
    content_hash: str | None = None
    description: str | None = None
    frontmatter: dict[str, str] = Field(default_factory=dict)
    frontmatter_ok: bool = True
    enabled: bool | None = None
    tokens_startup: int = 0  # name + description, estimate
    tokens_full: int = 0  # whole body, estimate
    # filled by scope classification
    scope: str | None = None  # profile-only | shared | global | plugin | project
    link_state: str | None = None  # symlink | copy | local
    drifted: bool = False
    identical_copy: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.profile}|{self.kind}|{self.origin}|{self.origin_name or ''}|{self.name}"


class Inventory(BaseModel):
    profiles: list[str] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)  # unreadable or invalid config files (path only)

    def of_kind(self, *kinds: str) -> list[Resource]:
        return [r for r in self.resources if r.kind in kinds]
