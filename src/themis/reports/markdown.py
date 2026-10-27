"""Markdown rendering of a report."""

from __future__ import annotations

from themis.analytics import bar
from themis.format import human
from themis.reports.build import Report

COST_NOTE = "API-equivalent estimate only: all profiles are on a subscription and nothing is billed per token."


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def render_markdown(r: Report) -> str:
    t = r.tokens
    scope = r.profile or "all profiles"
    out = [
        f"# Themis report: {scope}",
        "",
        f"Generated {r.generated_at}. Window: {'since ' + r.since if r.since else 'all time'}. "
        "Contains counts and configuration metadata only, no transcript content. Secrets are redacted.",
        "",
        "## Usage totals",
        "",
        f"- Sessions: {r.sessions}",
        f"- Tokens: {human(t.total)} (input {human(t.input)}, output {human(t.output)}, "
        f"cache read {human(t.cache_read)}, cache write {human(t.cache_creation)})",
        f"- Cache hit rate: {_pct(r.cache_hit_rate)}",
        f"- Cost: about ${r.cost:,.2f}{'+' if r.cost_incomplete else ''}. {COST_NOTE}",
        f"- Sessions that compacted: {r.context.compacted} of {r.context.sessions} "
        f"({r.context.compactions} compactions); {len(r.context.near_limit)} reached the high-context threshold",
        "",
        "### By model",
        "",
        "| Model | Tokens | Sessions | Est. cost |",
        "|---|---:|---:|---:|",
        *[
            f"| {b.label} | {human(b.total)} | {b.sessions} | ${b.cost:,.2f}{'+' if b.cost_incomplete else ''} |"
            for b in r.by_model
        ],
        "",
        f"### By {r.period}",
        "",
        "| Period | Tokens | Sessions |  |",
        "|---|---:|---:|---|",
    ]
    peak = max((b.total for b in r.by_period), default=0)
    out += [f"| {b.label} | {human(b.total)} | {b.sessions} | {bar(b.total, peak, 20)} |" for b in r.by_period]
    out += [
        "",
        "### Final context distribution",
        "",
        *[f"- {label}: {n}" for label, n in r.context.histogram],
        "",
        "## Top sessions by tokens",
        "",
        "| Session | Profile | Project | Model | Tokens | Peak context | Compactions |",
        "|---|---|---|---|---:|---:|---:|",
        *[
            f"| {s.session_id} | {s.profile} | {s.project} | {s.model} | {human(s.tokens)} | "
            f"{s.context_peak_percent:.0f}% | {s.compactions} |"
            for s in r.top
        ],
        "",
        "## Tool and resource usage",
        "",
        "Top tools: " + (", ".join(f"{n} ({c})" for n, c in r.top_tools) or "none"),
    ]
    for kind in ("skills", "commands", "mcp", "subagents"):
        rows = r.usage.get(kind, [])
        out += ["", f"### {kind.capitalize()}", ""]
        out += [f"- {n}: {total} ({', '.join(f'{p} {c}' for p, c in sorted(per.items()))})" for n, total, per in rows]
        if not rows:
            out.append("- none in this window")
    out += ["", "## Advisor findings", "", "Advisory only; Themis never edits anything.", ""]
    if not r.advice:
        out.append("No findings.")
    for a in r.advice:
        saved = f", ~{a.tokens_per_session} tokens/session" if a.tokens_per_session else ""
        saved += f", ~{a.tokens_per_month}/month" if a.tokens_per_month else ""
        out += [
            f"### [{a.severity}] {a.title}",
            "",
            f"Rule {a.rule}, confidence {a.confidence}{saved}. Profiles: {', '.join(a.profiles)}.",
            "",
            a.summary,
            "",
            *[f"- {e}" for e in a.evidence[:8]],
            "",
        ]
    out += ["## Doctor findings", ""]
    if not r.findings:
        out.append("No findings.")
    out += [f"- **{f.severity}** {f.profile or '-'} `{f.code}`: {f.message}" for f in r.findings]
    return "\n".join(out) + "\n"
