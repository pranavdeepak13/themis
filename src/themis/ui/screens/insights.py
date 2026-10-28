"""Insights: token dashboards, context behaviour, top sessions, usage, and the optimisation advisor.

Charts are Rich bars in Static widgets plus Textual's Sparkline, so they render the same in a terminal and in
the browser (see DECISIONS). The pane only calls core functions (analytics, advisor, reports)."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Sparkline, Static, TabbedContent, TabPane

from themis import analytics, clipboard
from themis.advisor.model import Advice
from themis.advisor.rules import run_advisor
from themis.format import human
from themis.reports.build import build_report, write_report
from themis.resources.store import ResourceData
from themis.sessions.metrics import context_percent
from themis.ui.screens.resources import LoadingMixin

WINDOWS = [("7d", "7d"), ("30d", "30d"), ("90d", "90d"), ("all", None)]
SEV_STYLE = {"high": "bold reverse", "medium": "bold", "low": "", "info": "dim"}
COST_NOTE = "API-equivalent estimate; all profiles are on a subscription, nothing is billed per token."


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


class InsightsPane(LoadingMixin):
    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.dim = "profile"
        self.window: str | None = "30d"
        self.advice: list[Advice] = []
        self.selected: Advice | None = None
        self.loaded = False

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="i-controls"):
            yield Static("Window:", classes="i-label")
            for label, _value in WINDOWS:
                yield Button(label, id=f"win-{label}", compact=True, classes="i-btn")
        yield Static("Loading…", id="i-status")
        with TabbedContent(id="i-tabs"):
            with TabPane("Tokens", id="i-tokens-tab"), VerticalScroll():
                yield Static("", id="i-summary")
                with Horizontal(id="i-dims"):
                    yield Static("By:", classes="i-label")
                    for d in analytics.DIMENSIONS:
                        yield Button(d, id=f"dim-{d}", compact=True, classes="i-btn")
                yield Sparkline([], id="i-spark")
                yield Static("", id="i-chart")
            with TabPane("Context", id="i-context-tab"), VerticalScroll():
                yield Static("", id="i-context")
            with TabPane("Top", id="i-top-tab"), VerticalScroll():
                yield Static("", id="i-top")
            with TabPane("Usage", id="i-usage-tab"), VerticalScroll():
                yield Static("", id="i-usage")
            with TabPane("Advisor", id="i-advisor-tab"):
                yield Static(
                    "Advisory only: Themis never edits anything. Each finding has a ready-to-paste Claude Code prompt.",
                    id="i-advice-note",
                )
                with Horizontal(id="i-advice-body"):
                    yield DataTable(id="i-advice", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="i-advice-detail"):
                        with Horizontal(id="i-advice-actions"):
                            yield Button(
                                "Copy Claude Code prompt", id="btn-copy-prompt", variant="primary", compact=True
                            )
                            yield Button("Export report (md)", id="btn-export-md", compact=True)
                            yield Button("Export report (html)", id="btn-export-html", compact=True)
                        with VerticalScroll():
                            yield Static("Select a finding.", id="i-advice-text")

    def on_mount(self) -> None:
        self.query_one("#i-advice", DataTable).add_columns(
            "Sev", "Profile", "Finding", "Saves/session", "Saves/month", "Conf"
        )
        self.query_one("#i-spark", Sparkline).display = False
        self._mark_buttons()

    # ------------------------------------------------------------------ data

    def set_data(self, data: ResourceData | None, error: str | None = None) -> None:
        self.data = data
        if data is None:
            self.query_one("#i-status", Static).update(error or "Loading…")
            return
        self.refresh_view()

    def _sessions(self):
        assert self.data is not None
        pool = [s for s in self.data.sessions if self.profile is None or s.profile == self.profile]
        since = analytics.parse_since(self.window) if self.window else None
        return analytics.within(pool, since)

    def refresh_view(self) -> None:
        assert self.data is not None
        sessions = self._sessions()
        scope = self.profile or "all profiles"
        if not sessions:
            self.query_one("#i-status", Static).update(
                f"No sessions for {scope} in this window. Try a longer window or another profile."
            )
        else:
            self.query_one("#i-status", Static).update(
                f"{len(sessions)} session(s) for {scope}, window {self.window or 'all time'}. Token and cost "
                "figures are summaries of local transcripts."
            )
        self._render_tokens(sessions)
        self._render_context(sessions)
        self._render_top(sessions)
        self._render_usage(sessions)
        self._render_advice()
        self.loaded = True

    # ------------------------------------------------------------------ tabs

    def _mark_buttons(self) -> None:
        for d in analytics.DIMENSIONS:
            self.query_one(f"#dim-{d}", Button).variant = "primary" if d == self.dim else "default"
        for label, value in WINDOWS:
            self.query_one(f"#win-{label}", Button).variant = "primary" if value == self.window else "default"

    @on(Button.Pressed, ".i-btn")
    def _pick(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("dim-"):
            self.dim = bid[4:]
        elif bid.startswith("win-"):
            self.window = dict(WINDOWS)[bid[4:]]
        self._mark_buttons()
        if self.data is not None:
            self.refresh_view()

    def _render_tokens(self, sessions) -> None:  # noqa: ANN001
        assert self.data is not None
        cfg = self.data.cfg
        t = analytics.total_tokens(sessions)
        cost, incomplete = analytics.total_cost(sessions, cfg)
        summary = Table.grid(padding=(0, 3))
        summary.add_row(
            Text(f"Tokens {human(t.total)}", style="bold"),
            f"in {human(t.input)}  out {human(t.output)}  cache read {human(t.cache_read)}  "
            f"cache write {human(t.cache_creation)}",
        )
        summary.add_row(
            f"Cache hit rate {_pct(t.cache_hit_rate)}",
            Text(f"Cost ~${cost:,.2f}{'+' if incomplete else ''}  ({COST_NOTE})", style="italic"),
        )
        self.query_one("#i-summary", Static).update(summary)
        buckets = analytics.tokens_by(sessions, self.dim, cfg)
        spark = self.query_one("#i-spark", Sparkline)
        time_dim = self.dim in ("day", "week", "month")
        spark.display = time_dim and len(buckets) > 1
        if time_dim:
            spark.data = [b.total for b in buckets]
        peak = max((b.total for b in buckets), default=0)
        table = Table("", "Tokens", "Sessions", "Cache", "Est. cost", "", box=None, pad_edge=False)
        table.columns[0].header = self.dim
        for b in buckets[-60:] if time_dim else buckets[:40]:
            table.add_row(
                b.label,
                Text(human(b.total), justify="right"),
                Text(str(b.sessions), justify="right"),
                Text(_pct(b.tokens.cache_hit_rate), justify="right"),
                Text(f"${b.cost:,.2f}{'+' if b.cost_incomplete else ''}", justify="right"),
                Text(analytics.bar(b.total, peak), style="white"),
            )
        if not buckets:
            table = Text("Nothing to chart.", style="dim")
        self.query_one("#i-chart", Static).update(table)

    def _render_context(self, sessions) -> None:  # noqa: ANN001
        assert self.data is not None
        stats = analytics.context_stats(sessions, self.data.cfg)
        peak = max((n for _, n in stats.histogram), default=0)
        t = Table.grid(padding=(0, 2))
        t.add_row(Text("Final context fill (percent of window)", style="bold"), "")
        for label, n in stats.histogram:
            t.add_row(label, Text(f"{n:>4}  " + analytics.bar(n, peak, 30), style="white"))
        t.add_row("", "")
        t.add_row(
            "Compaction",
            f"{stats.compacted} of {stats.sessions} sessions compacted ({_pct(stats.compaction_rate)}), "
            f"{stats.compactions} compaction(s) in total",
        )
        thr = self.data.cfg.advisor.high_context_percent
        t.add_row(f"Peak >= {thr:.0f}%", f"{len(stats.near_limit)} session(s) ran near the limit")
        for s in stats.near_limit[:10]:
            t.add_row(
                "",
                f"{s.profile}/{s.session_id[:8]}  peak {context_percent(s, self.data.cfg, peak=True):.0f}%  "
                f"compactions {s.compactions}",
            )
        t.add_row("", Text("The window size is inferred (200k unless a session exceeded it).", style="dim"))
        self.query_one("#i-context", Static).update(t)

    def _render_top(self, sessions) -> None:  # noqa: ANN001
        assert self.data is not None
        cfg = self.data.cfg
        out = Table.grid(padding=(1, 0))
        for title, by in (("Top sessions by tokens", "tokens"), ("Top sessions by peak context", "context")):
            t = Table("Session", "Profile", "Project", "Model", "Tokens", "Peak ctx", title=title, title_justify="left")
            for s in analytics.top_sessions(sessions, by, cfg, 10):
                t.add_row(
                    s.session_id[:8],
                    s.profile,
                    analytics.bucket_key(s, "project"),
                    (s.last_model or "-").removeprefix("claude-"),
                    Text(human(s.tokens.total), justify="right"),
                    Text(f"{context_percent(s, cfg, peak=True):.0f}%", justify="right"),
                )
            out.add_row(t)
        tools = analytics.top_tools(sessions, 15)
        peak = max((n for _, n in tools), default=0)
        tt = Table("Tool", "Calls", "", title="Top tools", title_justify="left")
        for name, n in tools:
            tt.add_row(name, Text(human(n), justify="right"), Text(analytics.bar(n, peak, 24), style="white"))
        out.add_row(tt)
        self.query_one("#i-top", Static).update(out)

    def _render_usage(self, sessions) -> None:  # noqa: ANN001
        out = Table.grid(padding=(1, 0))
        for title, kind in (
            ("Skills", "skills"),
            ("Slash commands", "commands"),
            ("MCP servers", "mcp"),
            ("Subagent types", "subagents"),
        ):
            rows = sorted(
                ((n, sum(per.values()), per) for n, per in analytics.usage_by_profile(sessions, kind).items()),
                key=lambda r: -r[1],
            )[:15]
            t = Table("Name", "Calls", "By profile", title=title, title_justify="left")
            for n, total, per in rows:
                t.add_row(n, Text(str(total), justify="right"), ", ".join(f"{p} {c}" for p, c in sorted(per.items())))
            if not rows:
                t.add_row(Text("none in this window", style="dim"), "", "")
            out.add_row(t)
        self.query_one("#i-usage", Static).update(out)

    # ------------------------------------------------------------------ advisor

    def _render_advice(self) -> None:
        assert self.data is not None
        self.advice = run_advisor(self.data, profile=self.profile)
        table = self.query_one("#i-advice", DataTable)
        table.clear()
        for i, a in enumerate(self.advice):
            table.add_row(
                Text(a.severity, style=SEV_STYLE.get(a.severity, "")),
                ",".join(a.profiles)[:24],
                a.title[:70],
                Text(str(a.tokens_per_session or "-"), justify="right"),
                Text(str(a.tokens_per_month or "-"), justify="right"),
                a.confidence,
                key=str(i),
            )
        if not self.advice:
            self.query_one("#i-advice-text", Static).update("No findings. Nothing stands out as wasteful.")
            self.selected = None
        elif self.selected is None or self.selected.id not in {a.id for a in self.advice}:
            self._show_advice(self.advice[0])

    @on(DataTable.RowHighlighted, "#i-advice")
    def _advice_highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value is not None and int(event.row_key.value) < len(self.advice):
            self._show_advice(self.advice[int(event.row_key.value)])

    def _show_advice(self, a: Advice) -> None:
        self.selected = a
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim", no_wrap=True)
        t.add_column(overflow="fold")
        t.add_row("finding", Text(a.title, style="bold"))
        t.add_row("rule", f"{a.rule}   severity {a.severity}   confidence {a.confidence}")
        t.add_row("profiles", ", ".join(a.profiles))
        t.add_row("summary", a.summary)
        if a.tokens_per_session:
            t.add_row("saves", f"~{a.tokens_per_session} tokens/session, ~{a.tokens_per_month or '-'}/month (estimate)")
        t.add_row("evidence", "\n".join(a.evidence[:15]) or "-")
        t.add_row("files", "\n".join(a.files[:10]) or "-")
        self.query_one("#i-advice-text", Static).update(t)

    @on(Button.Pressed, "#btn-copy-prompt")
    def copy_prompt(self) -> None:
        if self.selected is None:
            self.notify("Select a finding first.", severity="warning")
            return
        text = self.selected.prompt
        ok = clipboard.copy_text(text)
        try:
            self.app.copy_to_clipboard(text)  # terminal route (OSC 52); the only route in browser mode
            ok = True
        except Exception:  # noqa: BLE001 - clipboard support varies by terminal
            pass
        self.notify("Prompt copied." if ok else "Could not reach the clipboard; use `themis advise --prompts`.")

    @on(Button.Pressed, "#btn-export-md")
    def export_md(self) -> None:
        self._export("md")

    @on(Button.Pressed, "#btn-export-html")
    def export_html(self) -> None:
        self._export("html")

    def _export(self, fmt: str) -> None:
        if self.data is None:
            return
        report = build_report(self.data, self.profile, self.window)
        try:
            path = write_report(report, fmt)
        except OSError as exc:
            self.notify(f"Could not write the report: {exc.__class__.__name__}", severity="error")
            return
        self.notify(f"Report written: {path}", timeout=10)
