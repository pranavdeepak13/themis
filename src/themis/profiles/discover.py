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


def check_dir(path: Path) -> str | None:
    """Why a profile directory cannot be scanned (it vanished or is unreadable), or None if it can."""
    try:
        if not path.is_dir():
            return "the directory no longer exists"
    except OSError:
        return "the directory cannot be read"
    if not os.access(path, os.R_OK | os.X_OK):
        return "the directory is not readable"
    return None


def _is_profile(path: Path) -> bool:
    try:
        return path.is_dir() and ((path / "projects").is_dir() or (path / "settings.json").is_file())
    except OSError:  # an existing but unreadable directory is still a profile; it is reported, not hidden
        return path.exists() or path.is_symlink()


def usable(profiles: list[Profile]) -> tuple[list[Profile], list[Profile]]:
    """Split into (scannable, problem). Re-checks the disk, so a directory that vanished since discovery is caught."""
    good, bad = [], []
    for p in profiles:
        reason = check_dir(p.path)
        (bad if reason else good).append(p.model_copy(update={"problem": reason}) if reason else p)
    return good, bad


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
            profiles.append(Profile(name=name, path=path, alias=cfg.aliases.get(name), problem=check_dir(path)))
    return profiles
