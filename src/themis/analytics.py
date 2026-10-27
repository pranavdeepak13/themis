"""Aggregations over session summaries for the Insights screen and reports (pure, no UI)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from themis.config import Config
from themis.sessions.metrics import context_percent, estimate_cost, model_cost
from themis.sessions.model import SessionSummary, Tokens

DIMENSIONS = ("profile", "project", "model", "day", "week", "month")
CONTEXT_BUCKETS = [(0, 25), (25, 50), (50, 75), (75, 90), (90, 101)]


def parse_when(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """'30d', '4w', '3m' (30-day months), '1y' or an ISO date -> the cutoff datetime (UTC)."""
    now = now or datetime.now(UTC)
    t = text.strip().lower()
    if t[:-1].isdigit() and t[-1] in "dwmy":
        n = int(t[:-1])
        return now - timedelta(days={"d": 1, "w": 7, "m": 30, "y": 365}[t[-1]] * n)
    try:
        d = datetime.fromisoformat(t)
    except ValueError as exc:
        raise ValueError(f"Cannot read {text!r}; use 30d, 4w, 3m, 1y or a date like 2026-09-01") from exc
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def within(sessions: Iterable[SessionSummary], since: datetime | None) -> list[SessionSummary]:
    """Sessions that ended on or after `since` (sessions without timestamps are kept)."""
    out = []
    for s in sessions:
        end = parse_when(s.ended_at or s.started_at)
        if since is None or end is None or end >= since:
            out.append(s)
    return out


def bucket_key(s: SessionSummary, dim: str) -> str:
    if dim == "profile":
        return s.profile
    if dim == "project":
        return Path(s.cwd).name if s.cwd else (s.project_dir or "-")
    if dim == "model":
        return (s.last_model or "-").removeprefix("claude-")
    start = parse_when(s.started_at) or parse_when(s.ended_at)
    if start is None:
        return "-"
    if dim == "day":
        return start.strftime("%Y-%m-%d")
    if dim == "week":
        y, w, _ = start.isocalendar()
        return f"{y}-W{w:02d}"
    return start.strftime("%Y-%m")


@dataclass
class Bucket:
    label: str
    tokens: Tokens = field(default_factory=Tokens)
    sessions: int = 0
    cost: float = 0.0
    cost_incomplete: bool = False

    @property
    def total(self) -> int:
        return self.tokens.total


def tokens_by(sessions: Iterable[SessionSummary], dim: str, cfg: Config) -> list[Bucket]:
    """Token totals per bucket. A session counts wholly toward its start day, week or month (and, for
    'model', its tokens are split by model using the per-model buckets)."""
    out: dict[str, Bucket] = {}
    for s in sessions:
        if dim == "model":
            for model, tok in s.model_tokens.items():
                label = model.removeprefix("claude-")
                b = out.setdefault(label, Bucket(label))
                b.tokens.add(tok)
                b.sessions += 1
                cost = model_cost(model, tok, cfg)
                if cost is None:
                    b.cost_incomplete = b.cost_incomplete or tok.total > 0
                else:
                    b.cost += cost
            continue
        key = bucket_key(s, dim)
        b = out.setdefault(key, Bucket(key))
        b.tokens.add(s.tokens)
        b.sessions += 1
        cost, incomplete = estimate_cost(s, cfg)
        b.cost += cost
        b.cost_incomplete = b.cost_incomplete or incomplete
    rows = list(out.values())
    if dim in ("day", "week", "month"):
        return sorted(rows, key=lambda b: b.label)
    return sorted(rows, key=lambda b: -b.total)


def cache_hit_rate(sessions: Iterable[SessionSummary]) -> float | None:
    t = Tokens()
    for s in sessions:
        t.add(s.tokens)
    return t.cache_hit_rate


def total_tokens(sessions: Iterable[SessionSummary]) -> Tokens:
    t = Tokens()
    for s in sessions:
        t.add(s.tokens)
    return t


def total_cost(sessions: Iterable[SessionSummary], cfg: Config) -> tuple[float, bool]:
    cost, incomplete = 0.0, False
    for s in sessions:
        c, inc = estimate_cost(s, cfg)
        cost += c
        incomplete = incomplete or inc
    return cost, incomplete


@dataclass
class ContextStats:
    histogram: list[tuple[str, int]]
    near_limit: list[SessionSummary]  # peak at or above the threshold
    compacted: int
    compactions: int
    sessions: int

    @property
    def compaction_rate(self) -> float:
        return self.compacted / self.sessions if self.sessions else 0.0


def context_stats(sessions: list[SessionSummary], cfg: Config) -> ContextStats:
    """Distribution of the final context percent; near-limit means the peak reached the advisor threshold."""
    used = [s for s in sessions if s.context_peak > 0]
    counts = [0] * len(CONTEXT_BUCKETS)
    for s in used:
        pct = context_percent(s, cfg)
        for i, (lo, hi) in enumerate(CONTEXT_BUCKETS):
            if lo <= pct < hi:
                counts[i] += 1
                break
    labels = [f"{lo}-{min(hi, 100)}%" for lo, hi in CONTEXT_BUCKETS]
    near = [s for s in used if context_percent(s, cfg, peak=True) >= cfg.advisor.high_context_percent]
    near.sort(key=lambda s: -context_percent(s, cfg, peak=True))
    return ContextStats(
        histogram=list(zip(labels, counts, strict=True)),
        near_limit=near,
        compacted=sum(1 for s in used if s.compactions),
        compactions=sum(s.compactions for s in used),
        sessions=len(used),
    )


def top_sessions(sessions: list[SessionSummary], by: str, cfg: Config, n: int = 10) -> list[SessionSummary]:
    key: Callable[[SessionSummary], float] = (
        (lambda s: s.tokens.total) if by == "tokens" else (lambda s: context_percent(s, cfg, peak=True))
    )
    return sorted(sessions, key=key, reverse=True)[:n]


def top_tools(sessions: Iterable[SessionSummary], n: int = 15) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for s in sessions:
        c.update(s.tool_calls)
    return c.most_common(n)


def usage_by_profile(sessions: Iterable[SessionSummary], kind: str) -> dict[str, dict[str, int]]:
    """{name: {profile: calls}} for kind in skills, commands, mcp, subagents."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for s in sessions:
        table = {
            "skills": s.skills,
            "commands": s.commands,
            "mcp": s.mcp_servers,
            "subagents": s.subagent_types,
        }[kind]
        for name, n in table.items():
            out[name][s.profile] = out[name].get(s.profile, 0) + n
    return dict(out)


def bar(value: float, maximum: float, width: int = 28) -> str:
    """Unicode bar for charts that must render in a terminal and in the browser alike."""
    if maximum <= 0 or value <= 0:
        return ""
    cells = value / maximum * width
    full = int(cells)
    part = " ▏▎▍▌▋▊▉"[int((cells - full) * 8)]
    return "█" * full + part.strip()
