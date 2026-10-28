"""Sessions section: filterable table, detail pane with timeline, full-text search."""

from __future__ import annotations

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, ProgressBar, Static, TabbedContent, TabPane
from textual.worker import get_current_worker

from themis.config import Config, load_config
from themis.format import human, human_bytes, when_short
from themis.profiles.discover import discover_profiles, usable
from themis.profiles.model import Profile
from themis.sessions import search
from themis.sessions.cache import load_sessions
from themis.sessions.meta import load_meta
from themis.sessions.query import Filters, Row, apply_filters, build_rows, parse_date, sort_rows
from themis.sessions.retention import Retention, find_at_risk
from themis.sessions.timeline import session_timeline
from themis.ui.screens.session_actions import SessionActions
from themis.ui.widgets.detail import render_overview

COLUMNS: list[tuple[str, str, bool]] = [  # key, label, right-aligned
    ("mark", "✓", False),
    ("profile", "Profile", False),
    ("last", "Last active", False),
    ("topic", "Topic", False),
    ("project", "Project", False),
    ("model", "Model", False),
    ("tokens", "Tokens", True),
    ("context", "Ctx%", True),
    ("memory", "Mem", True),
    ("size", "Size", True),
    ("tags", "Tags", False),
]
TOPIC_WIDTH = 28


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


SORT_CYCLE = [k for k, _, _ in COLUMNS if k != "mark"]


class SessionsPane(SessionActions):
    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.cfg: Config = Config()
        self.profiles: dict[str, Profile] = {}
        self.retention: dict[str, Retention] = {}
        self.all_rows: list[Row] = []
        self.view_rows: list[Row] = []
        self.at_risk_rows: list[Row] = []
        self.marked: set[str] = set()
        self.filters = Filters()
        self.sort_col, self.sort_desc = "last", True
        self.selected_key: str | None = None
        self.loaded = False
        self._filter_timer: Timer | None = None
        self._search_hits: list[search.SearchHit] = []

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="filters"):
            yield Input(placeholder="filter text", id="f-text", classes="filter")
            yield Input(placeholder="project", id="f-project", classes="filter")
            yield Input(placeholder="model", id="f-model", classes="filter")
            yield Input(placeholder="tag", id="f-tag", classes="filter")
            yield Input(placeholder="from YYYY-MM-DD", id="f-since", classes="filter")
            yield Input(placeholder="to YYYY-MM-DD", id="f-until", classes="filter")
        with Horizontal(id="statusbar"):
            yield Static("Starting…", id="status")
            yield ProgressBar(total=None, show_eta=False, id="progress")
        with Horizontal(id="banner", classes="hidden"):
            yield Static("", id="banner-text")
            yield Button("Archive all at risk", id="btn-archive-risk", variant="warning")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
                with Vertical(id="search-panel", classes="hidden"):
                    yield Input(placeholder="search session content, Enter to run, Esc to cancel", id="search-input")
                    yield Static("", id="search-status")
                    yield DataTable(id="hits", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail"):
                with Horizontal(id="actions"):
                    yield Button("Resume", id="btn-resume", variant="primary", compact=True)
                    yield Button("Mark", id="btn-mark", compact=True)
                    yield Button("Rename", id="btn-rename", compact=True)
                    yield Button("Tag", id="btn-tag", compact=True)
                    yield Button("Export", id="btn-export", compact=True)
                with Horizontal(id="actions2"):
                    yield Button("Archive", id="btn-archive", compact=True)
                    yield Button("Copy to…", id="btn-copy", compact=True)
                    yield Button("Move to…", id="btn-move", compact=True)
                    yield Button("Delete", id="btn-delete", variant="error", compact=True)
                with Horizontal(id="bulk", classes="hidden"):
                    yield Static("", id="bulk-text")
                    yield Button("Mark all shown", id="btn-mark-all", compact=True)
                    yield Button("Clear", id="btn-clear", compact=True)
                    yield Button("Tag", id="btn-bulk-tag", compact=True)
                    yield Button("Archive", id="btn-bulk-archive", compact=True)
                    yield Button("Delete", id="btn-bulk-delete", variant="error", compact=True)
                with TabbedContent(id="detail-tabs"):
                    with TabPane("Overview", id="tab-overview"), VerticalScroll():
                        yield Static("Select a session.", id="overview")
                    with TabPane("Timeline", id="tab-timeline"):
                        yield Static("", id="timeline-status")
                        yield DataTable(id="timeline", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#progress", ProgressBar).display = True
        self.query_one("#timeline", DataTable).add_columns("#", "Role", "Preview", "In", "Out", "Tools")
        self.query_one("#hits", DataTable).add_columns("Profile", "Session", "Role", "Snippet")
        self.query_one("#table", DataTable).focus()
        self.reload()

    def on_resize(self, event: events.Resize) -> None:
        """Stack the detail pane under the table on narrow terminals."""
        self.set_class(event.size.width < 150, "stacked")

    # ------------------------------------------------------------------ loading

    def reload(self, rebuild: bool = False) -> None:
        self.loaded = False
        self.query_one("#progress", ProgressBar).display = True
        self.query_one("#status", Static).update("Scanning sessions…")
        self._load(rebuild)

    @work(thread=True, exclusive=True, group="load")
    def _load(self, rebuild: bool) -> None:
        call = self.app.call_from_thread
        try:
            cfg = load_config()
            profiles, bad = usable(discover_profiles(cfg))
            for p in bad:
                call(self.app.notify, f"Skipping profile {p.name}: {p.problem}.", severity="warning", timeout=8)
            sessions = load_sessions(profiles, rebuild=rebuild, progress=lambda d, t: call(self._on_progress, d, t))
            meta = load_meta()
            risky, policy = find_at_risk(profiles, sessions, cfg.retention_warn_days)
        except Exception as e:  # shown in the status line; the app stays usable
            call(self._load_failed, f"{type(e).__name__}: {e}")
            return
        call(self._loaded, cfg, profiles, sessions, meta, risky, policy)

    def _on_progress(self, done: int, total: int) -> None:
        self.query_one("#progress", ProgressBar).update(total=total, progress=done)
        self.query_one("#status", Static).update(f"Scanning sessions… {done}/{total}")

    def _load_failed(self, message: str) -> None:
        self.query_one("#progress", ProgressBar).display = False
        self.query_one("#status", Static).update(Text(f"Load failed: {message}", style="bold reverse"))
        self.loaded = True

    def _loaded(self, cfg, profiles, sessions, meta, risky, policy) -> None:  # noqa: ANN001
        self.cfg, self.retention = cfg, policy
        self.profiles = {p.name: p for p in profiles}
        risk = {f"{r.summary.profile}/{r.summary.session_id}": r.days_left for r in risky}
        self.all_rows = build_rows(sessions, cfg, meta, risk)
        self.at_risk_rows = [r for r in self.all_rows if r.risk_days_left is not None]
        self.marked &= {r.key for r in self.all_rows}
        self.query_one("#progress", ProgressBar).display = False
        self.app.set_profiles([p.name for p in profiles])  # type: ignore[attr-defined]
        self._update_banner()
        self.loaded = True
        self.refresh_table()

    def _update_banner(self) -> None:
        banner = self.query_one("#banner")
        banner.set_class(not self.at_risk_rows, "hidden")
        if self.at_risk_rows:
            parts = [
                f"{n} {name} ({self.retention[name].days}d{'' if self.retention[name].explicit else ' default'})"
                for name in sorted(self.retention)
                if (n := sum(1 for r in self.at_risk_rows if r.summary.profile == name))
            ]
            self.query_one("#banner-text", Static).update(
                f"⚠ {len(self.at_risk_rows)} session(s) within {self.cfg.retention_warn_days} days of being purged "
                f"by Claude Code: {', '.join(parts)}"
            )

    # ------------------------------------------------------------------ table

    def set_profile(self, name: str | None) -> None:
        self.filters.profile = name
        self.refresh_table()

    @on(Input.Changed, ".filter")
    def _filter_changed(self) -> None:
        if self._filter_timer:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.2, self._apply_filter_inputs)

    def _apply_filter_inputs(self) -> None:
        f = self.filters
        f.text = self.query_one("#f-text", Input).value.strip()
        f.project = self.query_one("#f-project", Input).value.strip()
        f.model = self.query_one("#f-model", Input).value.strip()
        f.tag = self.query_one("#f-tag", Input).value.strip()
        for fid, attr in (("#f-since", "since"), ("#f-until", "until")):
            box = self.query_one(fid, Input)
            value = parse_date(box.value)
            setattr(f, attr, value)
            box.set_class(bool(box.value.strip()) and value is None, "-invalid")
        self.refresh_table()

    def clear_filters(self) -> None:
        for box in self.query(".filter"):
            box.value = ""  # type: ignore[attr-defined]
        self.filters = Filters(profile=self.filters.profile)

    def refresh_table(self) -> None:
        table = self.query_one("#table", DataTable)
        keep = self.selected_key
        self.view_rows = sort_rows(apply_filters(self.all_rows, self.filters), self.sort_col, self.sort_desc)
        table.clear(columns=True)
        for key, label, right in COLUMNS:
            arrow = (" ▼" if self.sort_desc else " ▲") if key == self.sort_col else ""
            table.add_column(Text(label + arrow, justify="right" if right else "left"), key=key)
        for r in self.view_rows:
            table.add_row(*self._cells(r), key=r.key)
        if self.view_rows:
            idx = next((i for i, r in enumerate(self.view_rows) if r.key == keep), 0)
            table.move_cursor(row=idx)
            self._select(self.view_rows[idx].key)
        else:
            self._select(None)
        self._update_status()

    def _cells(self, r: Row) -> list[Text]:
        s = r.summary
        ctx_style = "bold reverse" if r.context_pct >= 80 else "bold" if r.context_pct >= 60 else ""
        topic = Text(("⚠ " if r.risk_days_left is not None else "") + _clip(r.topic, TOPIC_WIDTH))
        if r.risk_days_left is not None:
            topic.stylize("bold", 0, 1)
        return [
            Text("✓" if r.key in self.marked else " ", style="bold"),
            Text(s.profile),
            Text(when_short(s.ended_at)),
            topic,
            Text(_clip(r.project, 16)),
            Text(_clip(r.model, 12)),
            Text(human(s.tokens.total), justify="right"),
            Text(f"{r.context_pct:.0f}", style=ctx_style, justify="right"),
            Text(str(len(s.memory_writes)), justify="right"),
            Text(human_bytes(s.disk_bytes), justify="right"),
            Text(_clip(", ".join(r.tags), 14), style="dim"),
        ]

    def _update_status(self) -> None:
        shown = sum(r.summary.tokens.total for r in self.view_rows)
        text = f"{len(self.view_rows)} of {len(self.all_rows)} sessions · {human(shown)} tokens"
        if self.filters != Filters(profile=self.filters.profile):
            text += " · filtered"
        if not self.all_rows and self.loaded:
            text = "No sessions found. Check that a Claude Code profile directory exists (THEMIS_PROFILES overrides)."
        elif not self.view_rows and self.loaded:
            text += " · nothing matches the filters"
        self.query_one("#status", Static).update(text)
        bulk = self.query_one("#bulk")
        bulk.set_class(not self.marked, "hidden")
        self.query_one("#bulk-text", Static).update(f"{len(self.marked)} marked")

    @on(DataTable.HeaderSelected, "#table")
    def _header_clicked(self, event: DataTable.HeaderSelected) -> None:
        col = str(event.column_key.value)
        if col == "mark":
            return
        self.sort_desc = (
            not self.sort_desc if col == self.sort_col else col not in ("profile", "topic", "project", "model", "tags")
        )
        self.sort_col = col
        self.refresh_table()

    def action_cycle_sort(self) -> None:
        self.sort_col = SORT_CYCLE[(SORT_CYCLE.index(self.sort_col) + 1) % len(SORT_CYCLE)]
        self.sort_desc = self.sort_col not in ("profile", "topic", "project", "model", "tags")
        self.refresh_table()
        self.app.notify(f"Sorted by {self.sort_col} {'descending' if self.sort_desc else 'ascending'}", timeout=2)

    # ------------------------------------------------------------------ marks

    def _refresh_mark(self, key: str) -> None:
        table = self.query_one("#table", DataTable)
        mark = Text("✓" if key in self.marked else " ", style="bold")
        table.update_cell(key, "mark", mark)
        self._update_status()

    def action_toggle_mark(self) -> None:
        row = self.current_row()
        if row:
            self.marked ^= {row.key}
            self._refresh_mark(row.key)

    def action_mark_all(self) -> None:
        self.marked |= {r.key for r in self.view_rows}
        self.refresh_table()

    def action_clear_marks(self) -> None:
        self.marked.clear()
        self.refresh_table()

    # ------------------------------------------------------------------ detail

    @on(DataTable.RowHighlighted, "#table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self._select(str(event.row_key.value))

    def apply_meta(self) -> None:
        """Re-read renames and tags after a change, keeping the retention flags."""
        risk = {r.key: r.risk_days_left for r in self.all_rows if r.risk_days_left is not None}
        self.all_rows = build_rows([r.summary for r in self.all_rows], self.cfg, load_meta(), risk)
        self.refresh_table()

    def current_row(self) -> Row | None:
        return next((r for r in self.view_rows if r.key == self.selected_key), None)

    def _select(self, key: str | None) -> None:
        self.selected_key = key
        row = self.current_row()
        overview = self.query_one("#overview", Static)
        if row is None:
            overview.update("Select a session." if self.all_rows else "No sessions to show.")
            self.query_one("#timeline", DataTable).clear()
            self.query_one("#timeline-status", Static).update("")
            return
        days = self.retention[row.summary.profile].days if row.summary.profile in self.retention else None
        overview.update(render_overview(row, self.cfg, days))
        self.query_one("#btn-mark", Button).label = "Unmark" if row.key in self.marked else "Mark"
        if self.query_one("#detail-tabs", TabbedContent).active == "tab-timeline":
            self._load_timeline(row)

    @on(TabbedContent.TabActivated, "#detail-tabs")
    def _tab_changed(self, event: TabbedContent.TabActivated) -> None:
        row = self.current_row()
        if event.pane.id == "tab-timeline" and row:
            self._load_timeline(row)

    def _load_timeline(self, row: Row) -> None:
        self.query_one("#timeline-status", Static).update("Reading transcript…")
        self._timeline_worker(row)

    @work(thread=True, exclusive=True, group="timeline")
    def _timeline_worker(self, row: Row) -> None:
        worker = get_current_worker()
        profile = self.profiles.get(row.summary.profile)
        if profile is None:
            return
        try:
            items = session_timeline(profile, row.summary, lambda: worker.is_cancelled)
        except OSError as e:
            self.app.call_from_thread(self._timeline_failed, str(e))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._timeline_ready, row.key, items)

    def _timeline_failed(self, message: str) -> None:
        self.query_one("#timeline-status", Static).update(Text(f"Cannot read transcript: {message}", style="bold"))

    def _timeline_ready(self, key: str, items: list) -> None:  # noqa: ANN001
        if key != self.selected_key:
            return
        table = self.query_one("#timeline", DataTable)
        table.clear()
        for i in items:
            table.add_row(
                Text(str(i.index), justify="right"),
                i.role,
                i.preview,
                Text(human(i.tokens_in) if i.tokens_in else "", justify="right"),
                Text(human(i.tokens_out) if i.tokens_out else "", justify="right"),
                Text(str(i.tools) if i.tools else "", justify="right"),
            )
        self.query_one("#timeline-status", Static).update(f"{len(items)} messages (main conversation)")

    # ------------------------------------------------------------------ search

    def action_search(self) -> None:
        panel = self.query_one("#search-panel")
        panel.remove_class("hidden")
        self.query_one("#search-input", Input).focus()

    def action_escape(self) -> None:
        panel = self.query_one("#search-panel")
        if not panel.has_class("hidden"):
            self.workers.cancel_group(self, "search")
            panel.add_class("hidden")
        self.query_one("#table", DataTable).focus()

    @on(Input.Submitted, "#search-input")
    def _search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self._search_hits = []
        self.query_one("#hits", DataTable).clear()
        self.query_one("#search-status", Static).update(f"Searching for “{query}”…")
        self._search_worker(query)

    @work(thread=True, exclusive=True, group="search")
    def _search_worker(self, query: str) -> None:
        worker = get_current_worker()
        call = self.app.call_from_thread
        sessions = [r.summary for r in apply_filters(self.all_rows, self.filters)]
        total = search.search_sessions(
            self.profiles,
            sessions,
            query,
            lambda: worker.is_cancelled,
            lambda hit: call(self._add_hit, hit),
            lambda d, t: call(self._search_progress, d, t),
        )
        call(self._search_done, query, total, worker.is_cancelled)

    def _search_progress(self, done: int, total: int) -> None:
        self.query_one("#search-status", Static).update(
            f"Searching… {done}/{total} sessions, {len(self._search_hits)} hits"
        )

    def _add_hit(self, hit: search.SearchHit) -> None:
        self._search_hits.append(hit)
        row = next((r for r in self.all_rows if r.key == f"{hit.profile}/{hit.session_id}"), None)
        label = row.topic[:30] if row else hit.session_id[:8]
        self.query_one("#hits", DataTable).add_row(
            hit.profile, label, hit.role, Text(hit.snippet, no_wrap=True), key=str(len(self._search_hits) - 1)
        )

    def _search_done(self, query: str, total: int, cancelled: bool) -> None:
        state = "cancelled" if cancelled else "done"
        note = "" if total else " · no matches"
        self.query_one("#search-status", Static).update(f"Search {state}: {total} hit(s) for “{query}”{note}")

    @on(DataTable.RowSelected, "#hits")
    def _hit_selected(self, event: DataTable.RowSelected) -> None:
        hit = self._search_hits[int(str(event.row_key.value))]
        self.reveal(f"{hit.profile}/{hit.session_id}")

    def reveal(self, key: str) -> None:
        """Show a session in the table, dropping filters that hide it."""
        if not any(r.key == key for r in self.view_rows):
            self.clear_filters()
            self.app.set_profile_tab(None)  # type: ignore[attr-defined]
            self.refresh_table()
        table = self.query_one("#table", DataTable)
        for i, r in enumerate(self.view_rows):
            if r.key == key:
                table.move_cursor(row=i)
                self._select(key)
                break
