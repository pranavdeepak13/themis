"""Plugin and marketplace scanner (installed_plugins.json, known_marketplaces.json, enabledPlugins)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from themis.resources.jsonio import read_json
from themis.resources.model import Resource


def _source_label(src: Any) -> str | None:
    if not isinstance(src, dict):
        return None
    kind = src.get("source")
    target = src.get("repo") or src.get("url") or src.get("path")
    return f"{kind}:{target}" if kind and target else (str(kind) if kind else None)


def scan_marketplaces(profile_dir: Path, profile: str, settings: dict[str, Any]) -> tuple[list[Resource], list[str]]:
    file = profile_dir / "plugins" / "known_marketplaces.json"
    known, err = read_json(file)
    errors = [f"{file}: {err}"] if err else []
    known = known if isinstance(known, dict) else {}
    declared = settings.get("extraKnownMarketplaces")
    declared = declared if isinstance(declared, dict) else {}
    out: list[Resource] = []
    for name in sorted({*known, *declared}):
        k = known.get(name) if isinstance(known.get(name), dict) else {}
        d = declared.get(name) if isinstance(declared.get(name), dict) else {}
        src = _source_label(k.get("source") or d.get("source"))
        loc = k.get("installLocation")
        out.append(
            Resource(
                kind="marketplace",
                name=name,
                profile=profile,
                path=str(file),
                description=src,
                extra={
                    "source": src,
                    "install_location": loc,
                    "installed": name in known,
                    "declared": name in declared,
                    "location_exists": bool(loc) and Path(loc).exists(),
                    "last_updated": k.get("lastUpdated"),
                },
            )
        )
    return out, errors


def scan_plugins(
    profile_dir: Path, profile: str, settings: dict[str, Any], marketplaces: list[Resource]
) -> tuple[list[Resource], list[str]]:
    file = profile_dir / "plugins" / "installed_plugins.json"
    data, err = read_json(file)
    errors = [f"{file}: {err}"] if err else []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    plugins = plugins if isinstance(plugins, dict) else {}
    enabled = settings.get("enabledPlugins")
    enabled = enabled if isinstance(enabled, dict) else {}
    known_markets = {m.name for m in marketplaces}
    out: list[Resource] = []
    for pid in [*plugins, *(p for p in enabled if p not in plugins)]:
        entries = plugins.get(pid)
        entry = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
        market = str(pid).partition("@")[2]
        install = entry.get("installPath")
        flag = enabled.get(pid)
        out.append(
            Resource(
                kind="plugin",
                name=str(pid),
                profile=profile,
                path=str(profile_dir / "settings.json"),
                enabled=flag if isinstance(flag, bool) else None,
                extra={
                    "installed": pid in plugins,
                    "version": entry.get("version"),
                    "scope": entry.get("scope"),
                    "install_path": install,
                    "install_exists": bool(install) and Path(install).is_dir(),
                    "marketplace": market,
                    "marketplace_known": market in known_markets,
                    "installed_at": entry.get("installedAt"),
                    "last_updated": entry.get("lastUpdated"),
                },
            )
        )
    return out, errors
