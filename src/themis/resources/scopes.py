"""Scope classification across profiles.

Two entries of the same kind and name are the same resource when they share a realpath (symlink) or a
content hash (copy). A class that spans 1 profile is profile-only, 2..N-1 shared, all N global.
Plugin and project resources are not classified this way; they are flagged by origin instead.
"""

from __future__ import annotations

from collections import defaultdict

from themis.resources.model import Resource

CLASSIFIED_KINDS = {"skill", "command", "agent"}
CLASSIFIED_ORIGINS = {"user", "synced"}


class _Union:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def join(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def scope_name(n_profiles_in_class: int, total: int) -> str:
    if total > 1 and n_profiles_in_class >= total:
        return "global"
    return "shared" if n_profiles_in_class > 1 else "profile-only"


def classify(resources: list[Resource], total_profiles: int) -> None:
    """Set scope, link_state, drifted and identical_copy on resources in place."""
    groups: dict[tuple[str, str, str], list[Resource]] = defaultdict(list)
    for r in resources:
        if r.kind in CLASSIFIED_KINDS and r.origin in CLASSIFIED_ORIGINS and not r.broken:
            groups[(r.kind, r.origin, r.name)].append(r)
        elif r.origin == "plugin":
            r.scope = "plugin"
        elif r.origin == "project":
            r.scope = "project"
    for members in groups.values():
        u = _Union(len(members))
        by_real: dict[str, int] = {}
        by_hash: dict[str, int] = {}
        for i, r in enumerate(members):
            for table, key in ((by_real, r.realpath), (by_hash, r.content_hash)):
                if key:
                    if key in table:
                        u.join(i, table[key])
                    else:
                        table[key] = i
        classes: dict[int, list[Resource]] = defaultdict(list)
        for i, r in enumerate(members):
            classes[u.find(i)].append(r)
        drifted = len(classes) > 1
        for cls in classes.values():
            profiles = {r.profile for r in cls}
            reals = {r.realpath for r in cls}
            for r in cls:
                r.scope = scope_name(len(profiles), total_profiles)
                r.drifted = drifted
                if r.is_symlink:
                    r.link_state = "symlink"
                elif len(profiles) > 1 or len(reals) > 1:
                    r.link_state = "copy"
                else:
                    r.link_state = "local"
                # identical but not symlinked: another profile holds the same content at another real path
                r.identical_copy = r.link_state == "copy" and not r.is_symlink


def matrix(resources: list[Resource], profiles: list[str]) -> list[dict[str, str]]:
    """Rows of kind/name/origin plus one cell per profile: link, copy, local, drift, plugin, project or ''."""
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in resources:
        if r.kind in ("permission", "hook", "claude_md"):
            continue
        ident = (r.kind, r.origin if r.origin != "project" else "project", r.name)
        row = rows.setdefault(ident, {"kind": r.kind, "name": r.name, "origin": r.origin})
        if r.kind in ("plugin", "marketplace", "mcp"):
            state = "off" if r.enabled is False else "on" if r.enabled else "yes"
        elif r.drifted:
            state = "drift"
        else:
            state = r.link_state or r.origin
        row[r.profile] = state
    return sorted(rows.values(), key=lambda x: (x["kind"], x["name"]))
