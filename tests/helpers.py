from __future__ import annotations

import os
import time
from pathlib import Path

from fixtures.make_fixtures import SID_MAIN
from themis.profiles.discover import discover_profiles
from themis.profiles.model import Profile
from themis.sessions.cache import load_sessions
from themis.sessions.model import SessionSummary


def profiles_by_name() -> dict[str, Profile]:
    return {p.name: p for p in discover_profiles()}


def summary_of(session_id: str = SID_MAIN, profile: str = "alpha") -> SessionSummary:
    found = load_sessions(list(profiles_by_name().values()), rebuild=True)
    return next(s for s in found if s.session_id == session_id and s.profile == profile)


def age_files(profile_dir: Path, seconds: float = 3600) -> None:
    """Backdate every file under the profile so the recent-change guard does not apply."""
    old = time.time() - seconds
    for p in profile_dir.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
