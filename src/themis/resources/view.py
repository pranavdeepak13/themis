"""View model for the resource screens: group per-profile entries into one row per resource."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from themis.resources.model import Resource
from themis.resources.usage import UsageIndex, UsageStat
from themis.sessions.meta import resource_tags


@dataclass
class Group:
    kind: str
    name: str
    origin: str
    origin_name: str | None
    members: list[Resource]
    tags: list[str] = field(default_factory=list)
    usage: UsageStat = field(default_factory=UsageStat)

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.origin}|{self.origin_name or ''}|{self.name}"

    @property
    def first(self) -> Resource:
        return self.members[0]

    @property
    def profiles(self) -> list[str]:
        return sorted({m.profile for m in self.members})

    @property
    def scope(self) -> str:
        return self.first.scope or ("" if self.first.kind in ("plugin", "marketplace", "mcp") else self.first.origin)

    @property
    def drifted(self) -> bool:
        return any(m.drifted for m in self.members)

    @property
    def enabled_label(self) -> str:
        states = {m.enabled for m in self.members}
        if states <= {None}:
            return "-"
        if states == {True}:
            return "on"
        if states == {False}:
            return "off"
        return "mixed"

    @property
    def info(self) -> str:
        f = self.first
        if f.kind == "plugin":
            return f"v{f.extra.get('version') or '?'}  {f.extra.get('marketplace') or ''}"
        if f.kind == "mcp":
            return f"{f.extra.get('transport', '')}  {f.description or ''}"
        if f.kind == "hook":
            return f"{f.extra.get('level', '')}  {f.description or ''}"
        if f.kind == "marketplace":
            return f.description or ""
        return f.description or ""

    def haystack(self) -> str:
        f = self.first
        parts = [
            self.name,
            f.description or "",
            self.scope,
            self.origin,
            f.origin_name or "",
            *self.tags,
            *self.profiles,
        ]
        return " ".join(parts).lower()


def build_groups(
    resources: Iterable[Resource],
    kinds: tuple[str, ...],
    usage: UsageIndex,
    meta: dict[str, Any],
    profile: str | None = None,
    text: str = "",
) -> list[Group]:
    buckets: dict[tuple[str, str, str, str], list[Resource]] = defaultdict(list)
    for r in resources:
        if r.kind in kinds and (profile is None or r.profile == profile):
            buckets[(r.kind, r.origin, r.origin_name or "", r.name)].append(r)
    groups = []
    for (kind, origin, origin_name, name), members in buckets.items():
        g = Group(kind, name, origin, origin_name or None, sorted(members, key=lambda m: m.profile))
        g.tags = resource_tags(meta, kind, name)
        g.usage = usage.lookup(kind, name, profile) if profile else usage.of(g.first)
        groups.append(g)
    words = text.lower().split()
    if words:
        groups = [g for g in groups if all(w in g.haystack() for w in words)]
    return sorted(groups, key=lambda g: (g.kind, g.name.lower(), g.origin))
