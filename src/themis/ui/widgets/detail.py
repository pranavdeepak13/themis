"""Rich rendering of the session detail overview."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from themis.config import Config
from themis.format import human, human_bytes, when
from themis.sessions.metrics import context_percent, context_window, estimate_cost
from themis.sessions.query import Row

BAR_WIDTH = 32
FILES_SHOWN = 40


def bar(pct: float, width: int = BAR_WIDTH) -> Text:
    filled = round(width * min(pct, 100) / 100)
    color = "green" if pct < 60 else "yellow" if pct < 80 else "red"
    text = Text("█" * filled, style=color)
    text.append("░" * (width - filled), style="grey37")
    return text


def _section(title: str) -> Text:
    return Text(f"\n{title}", style="bold cyan")


def _kv(rows: list[tuple[str, RenderableType]]) -> Table:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", no_wrap=True)
    t.add_column()
    for k, v in rows:
        t.add_row(k, v)
    return t


def _counts(data: dict[str, int], limit: int = 12) -> str:
    items = sorted(data.items(), key=lambda kv: -kv[1])
    text = ", ".join(f"{k} ×{n}" for k, n in items[:limit])
    return text + (f", +{len(items) - limit} more" if len(items) > limit else "")


def render_overview(row: Row, cfg: Config, retention_days: int | None = None) -> RenderableType:
    s = row.summary
    t = s.tokens
    window = context_window(s.last_model, s.context_peak, cfg)
    now_pct, peak_pct = context_percent(s, cfg), context_percent(s, cfg, peak=True)
    cost, incomplete = estimate_cost(s, cfg)
    hit = t.cache_hit_rate

    parts: list[RenderableType] = [Text(row.topic, style="bold")]
    if row.renamed and s.topic:
        parts.append(Text(f"original: {s.topic}", style="dim"))
    if row.risk_days_left is not None:
        left = row.risk_days_left
        msg = f"Past the {retention_days}-day limit: Claude Code purges this at its next start." if left < 0 else (
            f"Purged by Claude Code in about {left:.0f} day(s) (cleanupPeriodDays={retention_days})."
        )  # fmt: skip
        parts.append(Text("⚠ " + msg, style="bold yellow"))
    parts.append(
        _kv(
            [
                ("profile", s.profile),
                ("session", s.session_id),
                ("project", s.cwd or s.project_dir),
                ("branch", s.git_branch or "-"),
                ("claude code", f"{s.version or '-'} ({s.entrypoint or '-'})"),
                ("period", f"{when(s.started_at)} → {when(s.ended_at)}  ({(s.duration_s or 0) / 60:.0f} min)"),
                ("tags", ", ".join(row.tags) or "-"),
                ("models", ", ".join(f"{m} ×{n}" for m, n in s.models.items()) or "-"),
                ("messages", f"{s.user_messages} user, {s.assistant_messages} assistant, {s.subagent_count} subagents"),
            ]
        )
    )

    parts.append(_section("Tokens"))
    tok = Table("kind", "tokens", box=None, pad_edge=False)
    for label, n in (
        ("input", t.input),
        ("output", t.output),
        ("cache read", t.cache_read),
        ("cache write", t.cache_creation),
    ):
        tok.add_row(label, Text(f"{n:,}", justify="right"))
    tok.add_row("total", Text(f"{t.total:,}", justify="right", style="bold"))
    parts.append(tok)
    parts.append(
        _kv(
            [
                ("cache hit rate", "-" if hit is None else f"{hit:.0%}"),
                ("subagents used", f"{s.subagent_tokens.total:,} tokens (included in total)"),
                ("cost", f"~${cost:.2f}{'+' if incomplete else ''}  API-equivalent estimate; subscription, not billed"),
            ]
        )
    )

    parts.append(_section("Context"))
    ctx = Text()
    ctx.append_text(bar(now_pct))
    ctx.append(f"  {now_pct:.0f}%  {human(s.context_tokens)} of {human(window)}")
    peak = Text()
    peak.append_text(bar(peak_pct))
    peak.append(f"  {peak_pct:.0f}%  {human(s.context_peak)} peak")
    parts.append(_kv([("now", ctx), ("peak", peak), ("compactions", str(s.compactions))]))

    parts.append(_section(f"Memory written ({len(s.memory_writes)})"))
    if s.memory_writes:
        mem = Table("tool", "path", "when", box=None, pad_edge=False)
        for m in s.memory_writes[:20]:
            mem.add_row(m.tool, m.path, when(m.timestamp))
        parts.append(mem)
        if len(s.memory_writes) > 20:
            parts.append(Text(f"+{len(s.memory_writes) - 20} more", style="dim"))
    else:
        parts.append(Text("none", style="dim"))

    usage = [
        ("tool calls", _counts(s.tool_calls)),
        ("skills", _counts(s.skills)),
        ("commands", _counts(s.commands)),
        ("mcp tools", _counts(s.mcp_tools)),
        ("subagent types", _counts(s.subagent_types)),
    ]
    parts.append(_section("Usage"))
    parts.append(_kv([(k, v or "-") for k, v in usage]))

    parts.append(_section(f"Related files ({len(s.related_files)}, {human_bytes(s.disk_bytes)})"))
    files = Table("kind", "path", "size", box=None, pad_edge=False)
    for f in sorted(s.related_files, key=lambda f: -f.size)[:FILES_SHOWN]:
        files.add_row(f.kind, f.path, Text(human_bytes(f.size), justify="right"))
    parts.append(files)
    if len(s.related_files) > FILES_SHOWN:
        parts.append(Text(f"+{len(s.related_files) - FILES_SHOWN} smaller files not shown", style="dim"))
    if s.parse_errors:
        parts.append(Text(f"\n{s.parse_errors} unparseable line(s) of {s.lines}", style="yellow"))
    return Group(*parts)
