"""Filesystem locations used by Themis."""

from __future__ import annotations

import os
from pathlib import Path


def themis_home() -> Path:
    """Themis state directory (`~/.themis`, overridable with THEMIS_HOME)."""
    override = os.environ.get("THEMIS_HOME")
    return Path(override).expanduser() if override else Path.home() / ".themis"


def cache_dir() -> Path:
    return themis_home() / "cache"


def config_path() -> Path:
    return themis_home() / "config.toml"


def audit_log_path() -> Path:
    return themis_home() / "audit.log"
