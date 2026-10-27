"""Hook scanner: settings.json at user, project and local level, and plugin hooks.json."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from themis.redact import redact_text
from themis.resources.jsonio import read_json
from themis.resources.model import Resource

_PLUGIN_ROOT = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?")


_PROJECT_DIR = re.compile(r"\$\{?CLAUDE_PROJECT_DIR\}?")


def script_path(command: str, plugin_root: Path | None = None, project: Path | None = None) -> str | None:
    """The file a hook command runs, when its first word (or an interpreter's first argument) is a path."""
    if plugin_root is not None:
        command = _PLUGIN_ROOT.sub(str(plugin_root), command)
    if _PROJECT_DIR.search(command):
        if project is None:  # a user-level hook that resolves against whichever project is open: not checkable
            return None
        command = _PROJECT_DIR.sub(str(project), command)
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    for w in words[:3]:
        if w.startswith(("/", "~/", "./")):
            return str(Path(w).expanduser())
        if w.endswith((".sh", ".py", ".js", ".mjs", ".ts")) and "/" in w:
            return w
    return None


def hooks_from_config(
    hooks: Any,
    profile: str,
    level: str,
    source: str,
    origin: str = "user",
    origin_name: str | None = None,
    plugin_root: Path | None = None,
    project: Path | None = None,
) -> list[Resource]:
    out: list[Resource] = []
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        for gi, group in enumerate(groups if isinstance(groups, list) else []):
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher", "") or "*")
            for hi, h in enumerate(group.get("hooks", []) if isinstance(group.get("hooks"), list) else []):
                if not isinstance(h, dict):
                    continue
                command = str(h.get("command") or h.get("prompt") or h.get("url") or "")
                script = script_path(command, plugin_root, project) if h.get("type", "command") == "command" else None
                out.append(
                    Resource(
                        kind="hook",
                        name=f"{event}:{matcher}:{gi}.{hi}",
                        profile=profile,
                        origin=origin,
                        origin_name=origin_name,
                        path=source,
                        description=redact_text(command)[:300],
                        extra={
                            "event": str(event),
                            "matcher": matcher,
                            "type": str(h.get("type", "command")),
                            "level": level,
                            "timeout": h.get("timeout"),
                            "script": script,
                            "script_missing": bool(script) and not Path(script).exists(),
                        },
                    )
                )
    return out


def scan_settings_hooks(
    settings_path: Path, profile: str, level: str, origin: str = "user", origin_name: str | None = None
) -> list[Resource]:
    data, _err = read_json(settings_path)
    if not isinstance(data, dict):
        return []
    project = Path(origin_name) if origin == "project" and origin_name else None
    return hooks_from_config(
        data.get("hooks"), profile, level, str(settings_path), origin, origin_name, project=project
    )


def scan_plugin_hooks(plugin_dir: Path, profile: str, plugin_id: str) -> list[Resource]:
    data, _err = read_json(plugin_dir / "hooks" / "hooks.json")
    if not isinstance(data, dict):
        return []
    hooks = data.get("hooks", data)
    return hooks_from_config(
        hooks, profile, "plugin", str(plugin_dir / "hooks" / "hooks.json"), "plugin", plugin_id, plugin_dir
    )
