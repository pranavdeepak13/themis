"""Join resources to the per-session usage extracted from transcripts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from themis.resources.model import Resource
from themis.sessions.model import SessionSummary


@dataclass
class UsageStat:
    invocations: int = 0
    sessions: int = 0
    last_used: str | None = None  # end of the latest session that used it (transcript timestamps)
    per_profile: dict[str, int] = field(default_factory=dict)
    last_by_profile: dict[str, str] = field(default_factory=dict)

    def merge(self, other: UsageStat) -> None:
        self.invocations += other.invocations
        self.sessions += other.sessions
        self.last_used = max(filter(None, [self.last_used, other.last_used]), default=None)
        for p, n in other.per_profile.items():
            self.per_profile[p] = self.per_profile.get(p, 0) + n
        for p, when in other.last_by_profile.items():
            self.last_by_profile[p] = max(when, self.last_by_profile.get(p, ""))


class UsageIndex:
    """(kind, raw name as seen in transcripts) -> UsageStat. kinds: skill, command, agent, mcp."""

    def __init__(self, sessions: Iterable[SessionSummary]) -> None:
        self.stats: dict[tuple[str, str], UsageStat] = defaultdict(UsageStat)
        for s in sessions:
            when = s.ended_at or s.started_at
            for kind, table in (
                ("skill", s.skills),
                ("command", {k.lstrip("/"): v for k, v in s.commands.items()}),
                ("agent", s.subagent_types),
                ("mcp", s.mcp_servers),
            ):
                for name, n in table.items():
                    st = self.stats[(kind, name)]
                    st.invocations += n
                    st.sessions += 1
                    st.per_profile[s.profile] = st.per_profile.get(s.profile, 0) + n
                    st.last_used = max(filter(None, [st.last_used, when]), default=None)
                    if when:
                        st.last_by_profile[s.profile] = max(when, st.last_by_profile.get(s.profile, ""))

    @staticmethod
    def _matches(kind: str, seen: str, name: str) -> bool:
        if seen == name:
            return True
        if kind == "mcp":  # plugin servers appear as plugin_<plugin>_<server>
            return seen.endswith("_" + name)
        return seen.endswith(":" + name)  # namespaced skills and commands: plugin:name

    def lookup(self, kind: str, name: str, profile: str | None = None) -> UsageStat:
        total = UsageStat()
        for (k, seen), st in self.stats.items():
            if k == kind and self._matches(kind, seen, name):
                if profile is None:
                    total.merge(st)
                elif profile in st.per_profile:
                    # sessions are not tracked per profile in the shared stat; approximate with 1 per hit profile
                    last = st.last_by_profile.get(profile)
                    total.merge(
                        UsageStat(
                            st.per_profile[profile],
                            0,
                            last,
                            {profile: st.per_profile[profile]},
                            {profile: last} if last else {},
                        )
                    )
        return total

    def of(self, r: Resource) -> UsageStat:
        if r.kind not in ("skill", "command", "agent", "mcp"):
            return UsageStat()
        return self.lookup(r.kind, r.name)
