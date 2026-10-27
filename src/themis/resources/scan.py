"""Build the full resource inventory for a set of profiles (read-only)."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from themis.profiles.model import Profile
from themis.resources import claude_md, hooks, mcp, plugins
from themis.resources.commands import scan_markdown
from themis.resources.jsonio import read_json
from themis.resources.model import Inventory, Resource
from themis.resources.scopes import classify
from themis.resources.settings import scan_permissions
from themis.resources.skills import scan_skills
from themis.sessions.model import SessionSummary


def project_dirs_by_profile(sessions: Iterable[SessionSummary]) -> dict[str, list[Path]]:
    """Distinct existing working directories seen in each profile's sessions."""
    out: dict[str, dict[str, Path]] = {}
    for s in sessions:
        if s.cwd and os.path.isdir(s.cwd):
            out.setdefault(s.profile, {})[s.cwd] = Path(s.cwd)
    return {p: sorted(d.values()) for p, d in out.items()}


def _plugin_contents(res: Resource, profile: str) -> tuple[list[Resource], int]:
    root = Path(res.extra["install_path"])
    pid = res.name
    found: list[Resource] = [
        *scan_skills(root / "skills", profile, "plugin", pid),
        *scan_markdown(root / "commands", "command", profile, "plugin", pid),
        *scan_markdown(root / "agents", "agent", profile, "plugin", pid),
        *hooks.scan_plugin_hooks(root, profile, pid),
        *mcp.scan_mcp_json(root / ".mcp.json", profile, "plugin", pid),
        *mcp.scan_plugin_json_mcp(root, profile, pid),
    ]
    seen: set[str] = set()
    uniq = []
    for r in found:  # a server can be declared in both .mcp.json and plugin.json
        if r.kind == "mcp" and r.name in seen:
            continue
        if r.kind == "mcp":
            seen.add(r.name)
        r.enabled = res.enabled
        uniq.append(r)
    return uniq, sum(r.tokens_startup for r in uniq if r.kind == "skill")


def _project_resources(project: Path, profile: str, profile_paths: set[str]) -> list[Resource]:
    out: list[Resource] = []
    dot = project / ".claude"
    uses_dot = os.path.realpath(dot) not in profile_paths  # ~/.claude is the default profile, not a project
    origin = str(project)
    out += claude_md.scan_project_claude_md(project, profile)
    out += mcp.scan_mcp_json(project / ".mcp.json", profile, "project", origin)
    if uses_dot:
        out += scan_skills(dot / "skills", profile, "project", origin)
        out += scan_markdown(dot / "commands", "command", profile, "project", origin)
        out += scan_markdown(dot / "agents", "agent", profile, "project", origin)
        for fn, level in (("settings.json", "project"), ("settings.local.json", "local")):
            out += hooks.scan_settings_hooks(dot / fn, profile, level, "project", origin)
    return out


def scan_inventory(
    profiles: list[Profile], projects: dict[str, list[Path]] | None = None, classify_scopes: bool = True
) -> Inventory:
    inv = Inventory(profiles=[p.name for p in profiles])
    projects = projects or {}
    profile_paths = {os.path.realpath(p.path) for p in profiles}
    for p in profiles:
        d, name = p.path, p.name
        settings, err = read_json(d / "settings.json")
        if err:
            inv.errors.append(f"{d / 'settings.json'}: {err}")
        settings = settings if isinstance(settings, dict) else {}
        overrides = settings.get("skillOverrides") if isinstance(settings.get("skillOverrides"), dict) else {}

        local = [
            *scan_skills(d / "skills", name),
            *scan_markdown(d / "commands", "command", name),
            *scan_markdown(d / "agents", "agent", name),
            *claude_md.scan_user_claude_md(d, name),
            *hooks.scan_settings_hooks(d / "settings.json", name, "user"),
            *scan_permissions(settings, name, str(d / "settings.json")),
        ]
        for r in local:
            if r.kind == "skill" and overrides.get(r.name) == "off":
                r.enabled = False
        inv.resources += local

        markets, errs = plugins.scan_marketplaces(d, name, settings)
        inv.errors += errs
        plugs, errs = plugins.scan_plugins(d, name, settings, markets)
        inv.errors += errs
        for pl in plugs:
            if pl.extra.get("install_exists"):
                contents, skill_tokens = _plugin_contents(pl, name)
                pl.extra["skills"] = sum(1 for c in contents if c.kind == "skill")
                pl.extra["skill_tokens"] = skill_tokens
                inv.resources += contents
        inv.resources += [*markets, *plugs]

        mcps, err = mcp.scan_claude_json(d, name, projects.get(name, []))
        if err:
            inv.errors.append(err)
        inv.resources += mcps
        for project in projects.get(name, []):
            inv.resources += _project_resources(project, name, profile_paths)
    if classify_scopes:
        classify(inv.resources, len(profiles))
    return inv
