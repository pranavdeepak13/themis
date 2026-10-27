"""Retention warnings. Claude Code deletes transcripts older than `cleanupPeriodDays` (default 30)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import orjson

from themis.profiles.model import Profile
from themis.sessions.model import SessionSummary

DEFAULT_CLEANUP_DAYS = 30
# Sessions written by a desktop surface are exempt from the sweep (desktopSessionCleanupPeriodDays
# defaults to "no ceiling"), so they are never flagged.
EXEMPT_ENTRYPOINTS = {"claude-desktop"}


@dataclass
class Retention:
    days: int
    explicit: bool  # False when the profile does not set cleanupPeriodDays and the default applies


@dataclass
class AtRisk:
    summary: SessionSummary
    age_days: float
    days_left: float  # negative = already past the limit, purged at the next Claude Code start


def cleanup_days(profile: Profile) -> Retention:
    """Read only the cleanupPeriodDays key from the profile's settings.json."""
    try:
        raw = orjson.loads((profile.path / "settings.json").read_bytes())
        days = raw.get("cleanupPeriodDays") if isinstance(raw, dict) else None
    except (OSError, orjson.JSONDecodeError):
        days = None
    if isinstance(days, int) and not isinstance(days, bool) and days >= 1:
        return Retention(days, True)
    return Retention(DEFAULT_CLEANUP_DAYS, False)


def find_at_risk(
    profiles: list[Profile], sessions: list[SessionSummary], warn_days: int, now: float | None = None
) -> tuple[list[AtRisk], dict[str, Retention]]:
    """Sessions within `warn_days` of purge, going by transcript mtime."""
    now = time.time() if now is None else now
    policy = {p.name: cleanup_days(p) for p in profiles}
    by_name = {p.name: p for p in profiles}
    risky: list[AtRisk] = []
    for s in sessions:
        profile = by_name.get(s.profile)
        if profile is None or s.entrypoint in EXEMPT_ENTRYPOINTS:
            continue
        try:
            mtime = (profile.path / s.transcript).stat().st_mtime
        except OSError:
            continue
        age = (now - mtime) / 86400
        left = policy[s.profile].days - age
        if left <= warn_days:
            risky.append(AtRisk(s, age, left))
    risky.sort(key=lambda r: r.days_left)
    return risky, policy
