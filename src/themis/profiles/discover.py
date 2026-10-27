"""Profile discovery by glob, with THEMIS_PROFILES override."""

from __future__ import annotations

import os
from pathlib import Path

from themis.config import Config, load_config
from themis.profiles.model import Profile


def profile_name(path: Path) -> str:
    base = path.name
    if base == ".claude":
        return "default"
    return base.removeprefix(".claude-").lstrip(".") or base


def _is_profile(path: Path) -> bool:
    return path.is_dir() and ((path / "projects").is_dir() or (path / "settings.json").is_file())


def discover_profiles(cfg: Config | None = None) -> list[Profile]:
    cfg = cfg or load_config()
    override = os.environ.get("THEMIS_PROFILES")
    if override:
        candidates = [Path(p).expanduser() for p in override.split(":") if p]
    else:
        home = Path.home()
        candidates = sorted([home / ".claude", *home.glob(".claude-*")])
    profiles = []
    for path in candidates:
        if _is_profile(path):
            name = profile_name(path)
            profiles.append(Profile(name=name, path=path, alias=cfg.aliases.get(name)))
    return profiles
