"""Assemble report data from loaded resources and sessions. Contains no transcript content:
no topics, prompts, snippets or tool output, only counts, ids, project folder names and config metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from themis import analytics, audit
from themis.advisor.model import Advice
from themis.advisor.rules import run_advisor
from themis.paths import reports_dir
from themis.redact import redact_secrets, redact_text
from themis.resources.doctor import Finding
from themis.resources.store import ResourceData
from themis.sessions.metrics import context_percent
from themis.sessions.model import SessionSummary, Tokens


@dataclass
class TopSession:
    session_id: str
    profile: str
    project: str
    model: str
    tokens: int
    context_peak_percent: float
    compactions: int


@dataclass
class Report:
    generated_at: str
    profile: str | None
    since: str | None
    sessions: int
    tokens: Tokens
    cache_hit_rate: float | None
    cost: float
    cost_incomplete: bool
    by_model: list[analytics.Bucket]
    by_period: list[analytics.Bucket]
    period: str
    context: analytics.ContextStats
    top: list[TopSession]
    top_tools: list[tuple[str, int]]
    usage: dict[str, list[tuple[str, int, dict[str, int]]]] = field(default_factory=dict)
    advice: list[Advice] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def _usage_rows(sessions: list[SessionSummary], kind: str, n: int = 15) -> list[tuple[str, int, dict[str, int]]]:
    rows = [(name, sum(per.values()), per) for name, per in analytics.usage_by_profile(sessions, kind).items()]
    return sorted(rows, key=lambda r: -r[1])[:n]


def build_report(
    data: ResourceData, profile: str | None = None, since: str | None = None, now: datetime | None = None
) -> Report:
    now = now or datetime.now(UTC)
    cutoff = analytics.parse_since(since, now) if since else None
    sessions = [s for s in data.sessions if profile is None or s.profile == profile]
    sessions = analytics.within(sessions, cutoff)
    cfg = data.cfg
    cost, incomplete = analytics.total_cost(sessions, cfg)
    days = {analytics.bucket_key(s, "day") for s in sessions}
    period = "day" if len(days) <= 45 else "week"
    top = [
        TopSession(
            s.session_id[:8],
            s.profile,
            redact_text(analytics.bucket_key(s, "project")),
            (s.last_model or "-").removeprefix("claude-"),
            s.tokens.total,
            context_percent(s, cfg, peak=True),
            s.compactions,
        )
        for s in analytics.top_sessions(sessions, "tokens", cfg, 10)
    ]
    return Report(
        generated_at=now.isoformat(timespec="seconds"),
        profile=profile,
        since=cutoff.date().isoformat() if cutoff else None,
        sessions=len(sessions),
        tokens=analytics.total_tokens(sessions),
        cache_hit_rate=analytics.cache_hit_rate(sessions),
        cost=cost,
        cost_incomplete=incomplete,
        by_model=analytics.tokens_by(sessions, "model", cfg),
        by_period=analytics.tokens_by(sessions, period, cfg),
        period=period,
        context=analytics.context_stats(sessions, cfg),
        top=top,
        top_tools=analytics.top_tools(sessions),
        usage={k: _usage_rows(sessions, k) for k in ("skills", "commands", "mcp", "subagents")},
        advice=run_advisor(data, now, profile),
        findings=[
            Finding(f.severity, f.code, redact_text(f.message), f.profile, f.path)
            for f in data.findings
            if profile is None or f.profile in (None, profile)
        ],
    )


def render(report: Report, fmt: str) -> str:
    from themis.reports.html import render_html
    from themis.reports.markdown import render_markdown

    text = render_html(report) if fmt == "html" else render_markdown(report)
    return redact_secrets(text)  # free-text items were already passed through redact_text when built


def write_report(report: Report, fmt: str, out: str | None = None) -> str:
    """Write the rendered report under ~/.themis/reports (or to `out`) and return the path."""
    from pathlib import Path

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(out).expanduser() if out else reports_dir() / f"themis-{report.profile or 'all'}-{stamp}.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report, fmt))
    audit.record("report.export", path=str(path), profile=report.profile, format=fmt)
    return str(path)
