"""MCP server scanner. Reads only `mcpServers`/`disabledMcpServers` from a profile's .claude.json.

Everything else in that file (account, caches, tokens) is dropped at once and never stored (rule 2).
Server definitions are redacted here, at the reader layer: env and headers values are masked.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import orjson

from themis.redact import redact_obj
from themis.resources.jsonio import read_json
from themis.resources.model import Resource


def _resource(
    name: str,
    cfg: Any,
    profile: str,
    origin: str,
    origin_name: str | None,
    source: str,
    enabled: bool | None,
    scope: str,
) -> Resource:
    safe = redact_obj(cfg if isinstance(cfg, dict) else {})
    digest = hashlib.sha256(orjson.dumps(safe, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]
    transport = str(safe.get("type") or ("http" if "url" in safe else "stdio"))
    summary = safe.get("url") or " ".join([str(safe.get("command", "")), *map(str, safe.get("args", []) or [])])
    return Resource(
        kind="mcp",
        name=name,
        profile=profile,
        origin=origin,
        origin_name=origin_name,
        path=source,
        content_hash=digest,
        description=str(summary).strip()[:300] or None,
        enabled=enabled,
        extra={"config": safe, "transport": transport, "config_scope": scope},
    )


def scan_claude_json(profile_dir: Path, profile: str, projects: list[Path]) -> tuple[list[Resource], str | None]:
    """User-scope servers, plus servers stored per project in the same file for the given project dirs."""
    path = profile_dir / ".claude.json"
    data, err = read_json(path)
    if not isinstance(data, dict):
        return [], err and f"{path}: {err}"
    out: list[Resource] = []
    raw_disabled = data.get("disabledMcpServers")
    disabled = {str(x) for x in raw_disabled} if isinstance(raw_disabled, list) else set()
    servers = data.get("mcpServers")
    for name, cfg in servers.items() if isinstance(servers, dict) else []:
        out.append(_resource(str(name), cfg, profile, "user", None, str(path), str(name) not in disabled, "user"))
    proj = data.get("projects")
    if isinstance(proj, dict):
        wanted = {str(p) for p in projects}
        for cwd, entry in proj.items():
            ps = entry.get("mcpServers") if isinstance(entry, dict) else None
            if str(cwd) in wanted and isinstance(ps, dict):
                for name, cfg in ps.items():
                    out.append(_resource(str(name), cfg, profile, "project", str(cwd), str(path), None, "local"))
    return out, None


def scan_mcp_json(file: Path, profile: str, origin: str, origin_name: str | None) -> list[Resource]:
    """A project `.mcp.json` or a plugin's `.mcp.json` (servers either top-level or under mcpServers)."""
    data, _err = read_json(file)
    if not isinstance(data, dict):
        return []
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data
    return [
        _resource(
            str(n), c, profile, origin, origin_name, str(file), None, "project" if origin == "project" else "plugin"
        )
        for n, c in servers.items()
        if isinstance(c, dict)
    ]


def scan_plugin_json_mcp(plugin_dir: Path, profile: str, plugin_id: str) -> list[Resource]:
    for rel in (".claude-plugin/plugin.json", "plugin.json"):
        data, _err = read_json(plugin_dir / rel)
        if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
            return [
                _resource(str(n), c, profile, "plugin", plugin_id, str(plugin_dir / rel), None, "plugin")
                for n, c in data["mcpServers"].items()
                if isinstance(c, dict)
            ]
    return []
