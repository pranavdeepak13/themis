"""One call that loads everything the resource screens and CLI need."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from themis.config import Config, load_config
from themis.profiles.discover import discover_profiles
from themis.profiles.model import Profile
from themis.resources.doctor import Finding, run_doctor
from themis.resources.model import Inventory
from themis.resources.scan import project_dirs_by_profile, scan_inventory
from themis.resources.usage import UsageIndex
from themis.sessions.cache import Progress, load_sessions
from themis.sessions.meta import load_meta
from themis.sessions.model import SessionSummary


@dataclass
class ResourceData:
    cfg: Config
    profiles: list[Profile]
    sessions: list[SessionSummary]
    inventory: Inventory
    usage: UsageIndex
    meta: dict[str, Any]
    findings: list[Finding]


def load_all(progress: Progress | None = None, rebuild: bool = False) -> ResourceData:
    cfg = load_config()
    profiles = discover_profiles(cfg)
    sessions = load_sessions(profiles, rebuild=rebuild, progress=progress)
    inv = scan_inventory(profiles, project_dirs_by_profile(sessions))
    findings = run_doctor(inv, profiles, sessions, cfg.large_transcript_mb * 1024 * 1024)
    return ResourceData(cfg, profiles, sessions, inv, UsageIndex(sessions), load_meta(), findings)
