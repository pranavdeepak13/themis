"""Self-contained HTML rendering of a report (inline CSS, no scripts, no external requests)."""

from __future__ import annotations

from html import escape

from themis.analytics import bar
from themis.format import human
from themis.reports.build import Report
from themis.reports.markdown import COST_NOTE

CSS = """
body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#1b1f24;background:#fff}
@media(prefers-color-scheme:dark){body{color:#e6e6e6;background:#16181d}th{background:#23262d}td,th{border-color:#333}}
h1,h2,h3{line-height:1.2} table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}
td,th{border:1px solid #ccc;padding:.25rem .5rem;text-align:left} th{background:#f0f0f0} td.n{text-align:right}
.sev-high,.sev-error{color:#c0392b;font-weight:600}.sev-medium,.sev-warning{color:#b9770e;font-weight:600}
.sev-low,.sev-info{color:#2471a3} .note{color:#777} code{font-family:ui-monospace,monospace}
"""


def _table(head: list[str], rows: list[list[str]], numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join(
        "<tr>" + "".join(f'<td class="{"n" if i in numeric else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
        for r in rows
    )
    return f"<table><tr>{th}</tr>{body}</table>"


def render_html(r: Report) -> str:
    t = r.tokens
    e = escape
    hit = "-" if r.cache_hit_rate is None else f"{r.cache_hit_rate:.0%}"
    peak = max((b.total for b in r.by_period), default=0)
    parts = [
        f"<h1>Themis report: {e(r.profile or 'all profiles')}</h1>",
        f'<p class="note">Generated {e(r.generated_at)}. Window: {e("since " + r.since if r.since else "all time")}. '
        "Counts and configuration metadata only, no transcript content. Secrets are redacted.</p>",
        "<h2>Usage totals</h2><ul>",
        f"<li>Sessions: {r.sessions}</li>",
        f"<li>Tokens: {human(t.total)} (input {human(t.input)}, output {human(t.output)}, "
        f"cache read {human(t.cache_read)}, cache write {human(t.cache_creation)})</li>",
        f"<li>Cache hit rate: {hit}</li>",
        f"<li>Cost: about ${r.cost:,.2f}{'+' if r.cost_incomplete else ''}. {e(COST_NOTE)}</li></ul>",
        "<h3>By model</h3>",
        _table(
            ["Model", "Tokens", "Sessions", "Est. cost"],
            [[e(b.label), human(b.total), str(b.sessions), f"${b.cost:,.2f}"] for b in r.by_model],
            {1, 2, 3},
        ),
        f"<h3>By {e(r.period)}</h3>",
        _table(
            ["Period", "Tokens", "Sessions", ""],
            [[e(b.label), human(b.total), str(b.sessions), e(bar(b.total, peak, 20))] for b in r.by_period],
            {1, 2},
        ),
        "<h3>Final context distribution</h3><ul>"
        + "".join(f"<li>{e(lb)}: {n}</li>" for lb, n in r.context.histogram)
        + "</ul>",
        "<h2>Top sessions by tokens</h2>",
        _table(
            ["Session", "Profile", "Project", "Model", "Tokens", "Peak context", "Compactions"],
            [
                [
                    e(s.session_id),
                    e(s.profile),
                    e(s.project),
                    e(s.model),
                    human(s.tokens),
                    f"{s.context_peak_percent:.0f}%",
                    str(s.compactions),
                ]
                for s in r.top
            ],
            {4, 5, 6},
        ),
        "<h2>Tool and resource usage</h2>",
        "<p>Top tools: " + e(", ".join(f"{n} ({c})" for n, c in r.top_tools) or "none") + "</p>",
    ]
    for kind in ("skills", "commands", "mcp", "subagents"):
        rows = r.usage.get(kind, [])
        parts.append(f"<h3>{e(kind.capitalize())}</h3>")
        parts.append(
            _table(
                ["Name", "Calls", "By profile"],
                [
                    [e(n), str(total), e(", ".join(f"{p} {c}" for p, c in sorted(per.items())))]
                    for n, total, per in rows
                ],
                {1},
            )
            if rows
            else "<p class='note'>None in this window.</p>"
        )
    parts.append("<h2>Advisor findings</h2><p class='note'>Advisory only; Themis never edits anything.</p>")
    if not r.advice:
        parts.append("<p>No findings.</p>")
    for a in r.advice:
        saved = f", ~{a.tokens_per_session} tokens/session" if a.tokens_per_session else ""
        saved += f", ~{a.tokens_per_month}/month" if a.tokens_per_month else ""
        parts.append(
            f'<h3><span class="sev-{e(a.severity)}">[{e(a.severity)}]</span> {e(a.title)}</h3>'
            f"<p class='note'>Rule {e(a.rule)}, confidence {e(a.confidence)}{e(saved)}. "
            f"Profiles: {e(', '.join(a.profiles))}.</p><p>{e(a.summary)}</p><ul>"
            + "".join(f"<li>{e(x)}</li>" for x in a.evidence[:8])
            + "</ul>"
        )
    parts.append("<h2>Doctor findings</h2>")
    parts.append(
        _table(
            ["Severity", "Profile", "Check", "Finding"],
            [
                [
                    f'<span class="sev-{e(f.severity)}">{e(f.severity)}</span>',
                    e(f.profile or "-"),
                    f"<code>{e(f.code)}</code>",
                    e(f.message),
                ]
                for f in r.findings
            ],
        )
        if r.findings
        else "<p>No findings.</p>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Themis report</title>'
        f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>"
    )
