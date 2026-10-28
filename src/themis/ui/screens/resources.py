"""Resource sections: Skills, Agents, Commands, Plugins, MCP, Hooks; plus Settings, Compare and Doctor."""

from __future__ import annotations

from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Markdown, Static, TabbedContent, TabPane

from themis.format import when_short
from themis.resources import settings as settings_mod
from themis.resources.doctor import Finding
from themis.resources.drift import diff_resources, drift_peers
from themis.resources.model import Resource
from themis.resources.reader import read_body
from themis.resources.scopes import matrix
from themis.resources.store import ResourceData
from themis.resources.view import Group, build_groups
from themis.ui.screens.resource_actions import ResourceActions

SCOPE_STYLE = {
    "global": "bold",
    "shared": "",
    "profile-only": "italic",
    "plugin": "underline",
    "project": "dim",
}
STATE_STYLE = {
    "symlink": "",
    "copy": "italic",
    "drift": "bold reverse",
    "local": "",
    "on": "bold",
    "off": "dim strike",
    "yes": "bold",
    "plugin": "underline",
    "project": "dim",
    "synced": "dim",
    "user": "white",
}
MAX_BODY_CHARS = 30_000

KIND_TITLES = {
    "skill": "Skills",
    "agent": "Agents",
    "command": "Commands",
    "plugin": "Plugins",
    "mcp": "MCP servers",
    "hook": "Hooks",
}


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


class LoadingMixin(Vertical):
    """Panes show a loading or empty line until the app has the resource data."""

    data: ResourceData | None = None
    profile: str | None = None

    def set_data(self, data: ResourceData | None, error: str | None = None) -> None:
        raise NotImplementedError

    def set_profile(self, name: str | None) -> None:
        self.profile = name
        if self.data is not None:
            self.refresh_view()

    def refresh_view(self) -> None:
        raise NotImplementedError


class ResourcePane(ResourceActions, LoadingMixin):
    """List of one resource family with detail, content, drift and usage."""

    def __init__(self, kinds: tuple[str, ...], **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.kinds = kinds
        self.groups: list[Group] = []
        self.selected: Group | None = None
        self.filter_text = ""
        self.loaded = False

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Input(placeholder=f"search {KIND_TITLES.get(self.kinds[0], 'resources').lower()}", id="r-search")
        yield Static("Loading…", id="r-status")
        with Horizontal(id="r-body"):
            yield DataTable(id="r-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="r-detail"):
                with Horizontal(id="r-actions"):
                    kind = self.kinds[0]
                    if kind in ("skill", "agent", "command"):
                        yield Button("Promote…", id="btn-promote", variant="primary", compact=True)
                    if kind == "plugin":
                        yield Button("Enable / disable…", id="btn-toggle", variant="primary", compact=True)
                    if kind == "mcp":
                        yield Button("Enable / disable…", id="btn-toggle", variant="primary", compact=True)
                        yield Button("Health check", id="btn-health", compact=True)
                    yield Button("Tag", id="btn-rtag", compact=True)
                with TabbedContent(id="r-tabs"):
                    with TabPane("Overview", id="r-overview-tab"), VerticalScroll():
                        yield Static("Select a row.", id="r-overview")
                    with TabPane("Content", id="r-content-tab"), VerticalScroll():
                        yield Markdown("", id="r-content")
                    with TabPane("Drift", id="r-drift-tab"), VerticalScroll():
                        yield Static("", id="r-drift")

    def on_mount(self) -> None:
        self.query_one("#r-table", DataTable).add_columns(
            "Name", "Scope", "Origin", "Profiles", "Info", "Tok", "Uses", "On", "Tags"
        )

    # ------------------------------------------------------------------ data

    def set_data(self, data: ResourceData | None, error: str | None = None) -> None:
        self.data = data
        if data is None:
            self.query_one("#r-status", Static).update(error or "Loading…")
            return
        self.refresh_view()

    def refresh_view(self) -> None:
        assert self.data is not None
        self.groups = build_groups(
            self.data.inventory.resources, self.kinds, self.data.usage, self.data.meta, self.profile, self.filter_text
        )
        table = self.query_one("#r-table", DataTable)
        table.clear()
        keep = self.selected.key if self.selected else None
        for g in self.groups:
            drift = " ⚠drift" if g.drifted else ""
            table.add_row(
                _clip(g.name, 34),
                Text(g.scope + drift, style=SCOPE_STYLE.get(g.scope, "")),
                g.origin if g.origin_name is None else _clip(f"{g.origin}:{g.origin_name}", 22),
                ",".join(g.profiles),
                _clip(g.info, 40),
                Text(str(g.first.tokens_startup or ""), justify="right"),
                Text(str(g.usage.invocations or ""), justify="right"),
                Text(g.enabled_label, style=STATE_STYLE.get(g.enabled_label, "")),
                ",".join(g.tags),
                key=g.key,
            )
        if keep:
            idx = next((i for i, g in enumerate(self.groups) if g.key == keep), None)
            if idx is not None:
                table.move_cursor(row=idx)
        self.loaded = True
        total = sum(len(g.members) for g in self.groups)
        title = KIND_TITLES.get(self.kinds[0], "resources")
        if not self.groups:
            self.query_one("#r-status", Static).update(
                f"No {title.lower()} match the filter." if self.filter_text else f"No {title.lower()} found."
            )
            self.query_one("#r-overview", Static).update("Nothing to show.")
        else:
            self.query_one("#r-status", Static).update(
                f"{len(self.groups)} {title.lower()} ({total} entries across profiles). "
                "Token counts are estimates (about 4 characters per token)."
            )
            if self.selected is None or all(g.key != self.selected.key for g in self.groups):
                self._show(self.groups[0])

    @on(Input.Changed, "#r-search")
    def _search_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value.strip()
        if self.data is not None:
            self.refresh_view()

    @on(DataTable.RowHighlighted, "#r-table")
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        g = next((g for g in self.groups if g.key == event.row_key.value), None)
        if g:
            self._show(g)

    def current_group(self) -> Group | None:
        return self.selected

    # ------------------------------------------------------------------ detail

    def _show(self, g: Group) -> None:
        self.selected = g
        assert self.data is not None
        self.query_one("#r-overview", Static).update(self._overview(g))
        body = read_body(g.first)[:MAX_BODY_CHARS]
        self.query_one("#r-content", Markdown).update(body or "*No content to show for this resource type.*")
        self.query_one("#r-drift", Static).update(self._drift(g))

    def _overview(self, g: Group) -> Table:
        assert self.data is not None
        f = g.first
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim", no_wrap=True)
        t.add_column(overflow="fold")
        t.add_row("name", Text(g.name, style="bold"))
        t.add_row("kind / origin", f"{f.kind} / {g.origin}" + (f" ({g.origin_name})" if g.origin_name else ""))
        t.add_row(
            "scope",
            Text(g.scope, style=SCOPE_STYLE.get(g.scope, "")) + Text("  DRIFTED" if g.drifted else "", "bold reverse"),
        )
        if f.description:
            t.add_row("description", f.description)
        t.add_row("tokens (est.)", f"startup {f.tokens_startup}, full {f.tokens_full}  (name+description / whole file)")
        u = g.usage
        t.add_row("usage", f"{u.invocations} invocation(s) in {u.sessions} session(s); last {when_short(u.last_used)}")
        if u.per_profile:
            t.add_row("by profile", ", ".join(f"{p}: {n}" for p, n in sorted(u.per_profile.items())))
        t.add_row("tags", ", ".join(g.tags) or "-")
        if f.frontmatter:
            fm = "\n".join(f"{k}: {_clip(v, 90)}" for k, v in f.frontmatter.items())
            t.add_row("frontmatter", fm + ("" if f.frontmatter_ok else "\n(invalid or incomplete)"))
        for m in g.members:
            bits = [f"{m.profile}:"]
            if m.enabled is not None:
                bits.append("enabled" if m.enabled else "disabled")
            if m.link_state:
                bits.append(m.link_state)
            if m.broken:
                bits.append("BROKEN")
            line = Text(" ".join(bits), style="bold")
            line.append(f"\n{m.path or ''}", style="dim")
            if m.is_symlink and m.realpath:
                line.append(f"\n -> {m.realpath}", style="dim")
            if m.content_hash:
                line.append(f"\n hash {m.content_hash}", style="dim")
            for k, v in m.extra.items():
                if k in ("config", "dir", "entry", "script", "lines", "level"):
                    continue
                if v not in (None, "", False) or k in ("script_missing", "install_exists"):
                    line.append(f"\n {k}: {v}", style="dim")
            t.add_row("", line)
        if f.kind == "mcp":
            t.add_row("config (redacted)", Syntax(_json(f.extra.get("config", {})), "json", theme="ansi_dark"))
        return t

    def _drift(self, g: Group) -> Text | str:
        assert self.data is not None
        peers = drift_peers(g.first, self.data.inventory.resources)
        if not peers:
            return "No drifted copies. This resource has one version across the profiles that hold it."
        out = Text()
        for peer in peers[:3]:
            out.append(f"{g.first.profile} vs {peer.profile}\n", style="bold")
            for line in diff_resources(g.first, peer).splitlines():
                style = (
                    "bold"
                    if line.startswith("+")
                    else "dim strike"
                    if line.startswith("-")
                    else "dim"
                    if line.startswith("@@")
                    else ""
                )
                out.append(line + "\n", style=style)
            out.append("\n")
        return out

    def selected_members(self) -> list[Resource]:
        return self.selected.members if self.selected else []


def _json(obj: object) -> str:
    import orjson

    return orjson.dumps(obj, option=orjson.OPT_INDENT_2).decode()


# ====================================================================== settings


class SettingsPane(ResourceActions, LoadingMixin):
    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.rows: list[tuple[str, settings_mod.SettingValue]] = []

    def compose(self) -> ComposeResult:
        yield Static("Loading…", id="s-status")
        with Horizontal(id="r-body"):
            yield DataTable(id="s-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="r-detail"):
                with Horizontal(id="r-actions"):
                    yield Button("Edit settings.json…", id="btn-edit", variant="primary", compact=True)
                with VerticalScroll():
                    yield Static("Select a setting.", id="s-detail")

    def on_mount(self) -> None:
        self.query_one("#s-table", DataTable).add_columns("Profile", "Key", "Value", "From", "Layers")

    def set_data(self, data: ResourceData | None, error: str | None = None) -> None:
        self.data = data
        if data is None:
            self.query_one("#s-status", Static).update(error or "Loading…")
        else:
            self.refresh_view()

    def refresh_view(self) -> None:
        assert self.data is not None
        table = self.query_one("#s-table", DataTable)
        table.clear()
        self.rows = []
        errors: list[str] = []
        for p in self.data.profiles:
            if self.profile and p.name != self.profile:
                continue
            values, errs = settings_mod.merged_settings(p)
            errors += errs
            for sv in values:
                self.rows.append((p.name, sv))
                table.add_row(
                    p.name, sv.key, _clip(_json(sv.value), 50), sv.origin, str(len(sv.layers)), key=f"{p.name}|{sv.key}"
                )
        status = (
            f"{len(self.rows)} settings. Precedence: user < project < local < remote (managed). Secrets are masked."
        )
        if errors:
            status += f"  Unreadable: {len(errors)} file(s)."
        if not self.rows:
            status = "No settings found for the selected profile(s)."
        self.query_one("#s-status", Static).update(status)

    @on(DataTable.RowHighlighted, "#s-table")
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        row = next(((p, sv) for p, sv in self.rows if f"{p}|{sv.key}" == event.row_key.value), None)
        if row is None:
            return
        p, sv = row
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim")
        t.add_column(overflow="fold")
        t.add_row("profile", p)
        t.add_row("key", Text(sv.key, style="bold"))
        t.add_row("effective", Syntax(_json(sv.value), "json", theme="ansi_dark"))
        t.add_row("from", sv.origin + ("  (overrides lower layers)" if sv.overridden else ""))
        for level, value in sv.layers:
            t.add_row(f"  {level}", Syntax(_json(value), "json", theme="ansi_dark"))
        self.query_one("#s-detail", Static).update(t)


# ====================================================================== compare


class ComparePane(LoadingMixin):
    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter by kind or name (e.g. skill, drift, wrap)", id="c-search")
        yield Static("Loading…", id="c-status")
        yield DataTable(id="c-table", cursor_type="row", zebra_stripes=True)

    def set_data(self, data: ResourceData | None, error: str | None = None) -> None:
        self.data = data
        if data is None:
            self.query_one("#c-status", Static).update(error or "Loading…")
        else:
            self.refresh_view()

    def refresh_view(self) -> None:
        assert self.data is not None
        names = [p.name for p in self.data.profiles]
        table = self.query_one("#c-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Kind", "Name", "Origin", *names)
        rows = matrix(self.data.inventory.resources, names)
        words = self.filter_text.lower().split()
        shown = 0
        for r in rows:
            hay = " ".join([r["kind"], r["name"], r["origin"], *(r.get(n, "") for n in names)]).lower()
            if not all(w in hay for w in words):
                continue
            cells = [Text(r.get(n, "·"), style=STATE_STYLE.get(r.get(n, ""), "dim")) for n in names]
            table.add_row(r["kind"], _clip(r["name"], 40), r["origin"], *cells)
            shown += 1
        legend = "symlink = linked to a shared copy, copy = identical but separate, drift = same name different content"
        self.query_one("#c-status", Static).update(
            f"{shown} of {len(rows)} resources across {len(names)} profiles.  {legend}"
            if rows
            else "No resources found."
        )

    def set_profile(self, name: str | None) -> None:  # the matrix always shows every profile
        self.profile = name

    @on(Input.Changed, "#c-search")
    def _changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value.strip()
        if self.data is not None:
            self.refresh_view()


# ====================================================================== doctor

SEVERITY_STYLE = {"error": "bold reverse", "warning": "bold", "info": "dim"}


class DoctorPane(LoadingMixin):
    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.shown: list[Finding] = []

    def compose(self) -> ComposeResult:
        yield Static("Loading…", id="d-status")
        yield DataTable(id="d-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="d-detail")

    def on_mount(self) -> None:
        self.query_one("#d-table", DataTable).add_columns("Severity", "Profile", "Check", "Finding")

    def set_data(self, data: ResourceData | None, error: str | None = None) -> None:
        self.data = data
        if data is None:
            self.query_one("#d-status", Static).update(error or "Loading…")
        else:
            self.refresh_view()

    def refresh_view(self) -> None:
        assert self.data is not None
        table = self.query_one("#d-table", DataTable)
        table.clear()
        self.shown = [f for f in self.data.findings if not self.profile or f.profile in (None, self.profile)]
        for i, f in enumerate(self.shown):
            table.add_row(
                Text(f.severity, style=SEVERITY_STYLE[f.severity]),
                f.profile or "-",
                f.code,
                _clip(f.message, 110),
                key=str(i),
            )
        counts = {s: sum(1 for f in self.shown if f.severity == s) for s in ("error", "warning", "info")}
        self.query_one("#d-status", Static).update(
            "No findings. Everything checked looks healthy."
            if not self.shown
            else f"{counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} note(s). Read-only."
        )

    @on(DataTable.RowHighlighted, "#d-table")
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value is None:
            return
        f = self.shown[int(event.row_key.value)]
        self.query_one("#d-detail", Static).update(f"{f.message}\n[dim]{f.path or ''}[/dim]")
