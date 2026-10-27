"""Doctor: read-only health checks over the inventory and sessions."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from themis.profiles.model import Profile
from themis.resources.model import Inventory
from themis.sessions.model import SessionSummary

LARGE_TRANSCRIPT_BYTES = 100 * 1024 * 1024
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Finding:
    severity: str  # error | warning | info
    code: str
    message: str
    profile: str | None = None
    path: str | None = None


def run_doctor(
    inv: Inventory, profiles: list[Profile], sessions: list[SessionSummary], large_bytes: int = LARGE_TRANSCRIPT_BYTES
) -> list[Finding]:
    out: list[Finding] = []
    add = out.append
    for err in inv.errors:
        path, _, why = err.rpartition(": ")
        add(Finding("error", "config-unreadable", f"{Path(path).name}: {why}", None, path))

    names: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for r in inv.resources:
        if r.broken and r.kind in ("skill", "command", "agent", "claude_md"):
            add(Finding("error", "broken-symlink", f"{r.kind} {r.name!r} is broken or unreadable", r.profile, r.path))
        if r.kind in ("skill", "agent") and r.origin in ("user", "project") and not r.broken and not r.frontmatter_ok:
            add(
                Finding(
                    "warning",
                    "bad-frontmatter",
                    f"{r.kind} {r.name!r} lacks valid name/description frontmatter",
                    r.profile,
                    r.path,
                )
            )
        if r.kind in ("skill", "command", "agent") and r.origin in ("user", "synced", "project"):
            names[(r.profile, r.kind, f"{r.origin}:{r.origin_name or ''}", r.name)].append(r.path or "")
        if r.kind == "plugin" and r.extra.get("marketplace") != "synced":  # "synced" = managed by the desktop app
            if not r.extra.get("marketplace_known"):
                add(
                    Finding(
                        "warning",
                        "plugin-marketplace-missing",
                        f"plugin {r.name!r}: marketplace {r.extra.get('marketplace')!r} is not known",
                        r.profile,
                        r.path,
                    )
                )
            if r.extra.get("installed", True) is False:
                add(
                    Finding(
                        "warning",
                        "plugin-not-installed",
                        f"plugin {r.name!r} is enabled but not installed",
                        r.profile,
                        r.path,
                    )
                )
            elif not r.extra.get("install_exists"):
                add(
                    Finding(
                        "warning",
                        "plugin-files-missing",
                        f"plugin {r.name!r}: install directory is missing",
                        r.profile,
                        r.extra.get("install_path"),
                    )
                )
        if r.kind == "marketplace" and r.extra.get("installed") and not r.extra.get("location_exists"):
            add(
                Finding(
                    "warning",
                    "marketplace-missing",
                    f"marketplace {r.name!r}: install location is missing",
                    r.profile,
                    r.extra.get("install_location"),
                )
            )
        if r.kind == "hook" and r.extra.get("script_missing"):
            add(
                Finding(
                    "error",
                    "hook-script-missing",
                    f"hook {r.extra.get('event')}: script {r.extra.get('script')} does not exist",
                    r.profile,
                    r.path,
                )
            )
    for (profile, kind, _origin, name), paths in names.items():
        if len(paths) > 1:
            add(
                Finding(
                    "warning",
                    "duplicate-name",
                    f"{len(paths)} {kind}s are named {name!r} in the same place",
                    profile,
                    paths[0],
                )
            )

    by_dir: dict[tuple[str, str], list[SessionSummary]] = defaultdict(list)
    for s in sessions:
        by_dir[(s.profile, s.project_dir)].append(s)
        big = next((f.size for f in s.related_files if f.kind == "transcript"), 0)
        if big >= large_bytes:
            add(
                Finding(
                    "info",
                    "large-transcript",
                    f"session {s.session_id[:8]} transcript is {big / 1e6:.0f} MB",
                    s.profile,
                    s.transcript,
                )
            )
    for (profile, project_dir), group in by_dir.items():
        cwd = next((s.cwd for s in group if s.cwd), None)
        if cwd and not os.path.isdir(cwd):
            add(
                Finding(
                    "info",
                    "orphan-project",
                    f"{len(group)} session(s) in {project_dir}: {cwd} no longer exists",
                    profile,
                    project_dir,
                )
            )
    known = {p.name for p in profiles}
    for r in inv.resources:
        if r.profile not in known:
            add(Finding("info", "unknown-profile", f"resource for unknown profile {r.profile}", r.profile))
    out.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.profile or "", f.code, f.message))
    return out
