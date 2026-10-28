"""The Themis Textual app. Runs in the terminal and, through textual-serve, in the browser."""

from __future__ import annotations

import re

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import ContentSwitcher, Footer, Header, Static, Tab, Tabs

from themis.resources.store import ResourceData, load_all
from themis.ui.screens.insights import InsightsPane
from themis.ui.screens.placeholder import PlaceholderPane
from themis.ui.screens.resources import ComparePane, DoctorPane, LoadingMixin, ResourcePane, SettingsPane
from themis.ui.screens.sessions import SessionsPane
from themis.ui.widgets.modals import HelpModal

SECTIONS: list[tuple[str, str, str, str]] = [  # id, label, phase, blurb
    ("sessions", "Sessions", "", ""),
    ("skills", "Skills", "", ""),
    ("agents", "Agents", "", ""),
    ("commands", "Commands", "", ""),
    ("plugins", "Plugins", "", ""),
    ("mcp", "MCP", "", ""),
    ("hooks", "Hooks", "", ""),
    ("settings", "Settings", "", ""),
    ("compare", "Compare", "", ""),
    ("doctor", "Doctor", "", ""),
    ("insights", "Insights", "", ""),
]
RESOURCE_KINDS: dict[str, tuple[str, ...]] = {
    "skills": ("skill",),
    "agents": ("agent",),
    "commands": ("command",),
    "plugins": ("plugin", "marketplace"),
    "mcp": ("mcp",),
    "hooks": ("hook",),
}


THEMIS_THEME = Theme(
    name="themis",
    primary="#fafafa",
    secondary="#b4b4b4",
    accent="#fafafa",
    foreground="#fafafa",
    background="#000000",
    surface="#0a0a0a",
    panel="#111111",
    success="#fafafa",
    warning="#b4b4b4",
    error="#fafafa",
    dark=True,
    variables={"block-cursor-text-style": "bold", "input-selection-background": "#fafafa 30%"},
)


def _tab_id(name: str) -> str:
    return "p-" + re.sub(r"[^A-Za-z0-9_-]", "_", name)


class ThemisApp(App[None]):
    CSS_PATH = "themis.tcss"
    TITLE = "Themis"
    SUB_TITLE = "Claude Code profiles"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "pane('action_search')", "Search"),
        Binding("space", "pane('action_toggle_mark')", "Mark"),
        Binding("r", "pane('do_resume')", "Resume"),
        Binding("d", "pane('do_delete')", "Delete"),
        Binding("a", "pane('do_archive')", "Archive"),
        Binding("t", "pane('do_tag')", "Tag"),
        Binding("n", "pane('do_rename')", "Rename"),
        Binding("e", "pane('do_export')", "Export"),
        Binding("b", "toggle_nav", "Sections"),
        Binding("question_mark", "help", "Help"),
        Binding("v", "pane('do_restore')", "Archives", show=False),
        Binding("c", "pane('do_copy')", "Copy to", show=False),
        Binding("m", "pane('do_move')", "Move to", show=False),
        Binding("s", "pane('action_cycle_sort')", "Sort", show=False),
        Binding("u", "pane('action_clear_marks')", "Clear marks", show=False),
        Binding("ctrl+a", "pane('action_mark_all')", "Mark all", show=False),
        Binding("ctrl+r", "rebuild", "Rebuild cache", show=False),
        Binding("escape", "pane('action_escape')", "Back", show=False),
        *[
            Binding(str(i), f"section('{sid}')", label, show=False)
            for i, (sid, label, *_) in enumerate(SECTIONS[:9], 1)
        ],
        Binding("0", "section('doctor')", "Doctor", show=False),
        Binding("i", "section('insights')", "Insights", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._profile_names: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(Tab("All", id="p-all"), id="profiles")
        with ContentSwitcher(initial="sessions", id="main"):
            for sid, label, phase, blurb in SECTIONS:
                if sid == "sessions":
                    yield SessionsPane(id=sid)
                elif sid in RESOURCE_KINDS:
                    yield ResourcePane(RESOURCE_KINDS[sid], id=sid)
                elif sid == "settings":
                    yield SettingsPane(id=sid)
                elif sid == "compare":
                    yield ComparePane(id=sid)
                elif sid == "doctor":
                    yield DoctorPane(id=sid)
                elif sid == "insights":
                    yield InsightsPane(id=sid)
                else:
                    yield PlaceholderPane(label, phase, blurb, id=sid)
        with Vertical(id="navbar"):
            yield Static(id="nav-toggle")
            yield Tabs(
                *[
                    Tab(f"{i} {label}" if i < 10 else label, id=f"s-{sid}")
                    for i, (sid, label, *_) in enumerate(SECTIONS, 1)
                ],
                id="nav",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(THEMIS_THEME)
        self.theme = "themis"
        self._sync_nav()
        self.resource_data: ResourceData | None = None
        self._load_resources()

    # ------------------------------------------------------------------ resource data

    def _resource_panes(self) -> list[LoadingMixin]:
        return list(self.query("ResourcePane, SettingsPane, ComparePane, DoctorPane, InsightsPane"))  # type: ignore[arg-type]

    @work(thread=True, exclusive=True, group="resources")
    def _load_resources(self) -> None:
        try:
            data = load_all()
        except Exception as exc:  # noqa: BLE001 - surface any scan failure in the panes instead of crashing
            self.call_from_thread(self._resources_ready, None, f"Could not scan resources: {exc.__class__.__name__}")
            return
        self.call_from_thread(self._resources_ready, data, None)

    def _resources_ready(self, data: ResourceData | None, error: str | None) -> None:
        self.resource_data = data
        for pane in self._resource_panes():
            pane.set_data(data, error)

    def reload_resources(self) -> None:
        """Rescan resources after a write action."""
        for pane in self._resource_panes():
            pane.set_data(None)
        self._load_resources()

    # ------------------------------------------------------------------ navigation

    nav_open = True

    @on(Tabs.TabActivated, "#nav")
    def _nav(self, event: Tabs.TabActivated) -> None:
        sid = (event.tab.id or "")[2:]
        if sid:
            self.query_one("#main", ContentSwitcher).current = sid
            self._sync_nav()

    def action_section(self, section: str) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        self.query_one("#nav", Tabs).active = f"s-{section}"

    def action_toggle_nav(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        self.nav_open = not self.nav_open
        self._sync_nav()

    @on(events.Click, "#nav-toggle")
    def _click_toggle(self) -> None:
        self.action_toggle_nav()

    def _sync_nav(self) -> None:
        """Expanded: the full section strip. Collapsed: one line naming the current section."""
        current = self.query_one("#main", ContentSwitcher).current or "sessions"
        label = next(lbl for sid, lbl, *_ in SECTIONS if sid == current)
        self.query_one("#nav", Tabs).display = self.nav_open
        arrow = "▾" if self.nav_open else "▸"
        hint = "b collapse" if self.nav_open else "b expand · 1-9 jump"
        self.query_one("#nav-toggle", Static).update(f" {arrow} {label}   [dim]{hint}[/dim]")

    # ------------------------------------------------------------------ profile bar

    def set_profiles(self, names: list[str]) -> None:
        """Called after a load: add tabs for new profiles, drop tabs for vanished ones."""
        tabs = self.query_one("#profiles", Tabs)
        for name in self._profile_names:
            if name not in names:
                tabs.remove_tab(_tab_id(name))
        for name in names:
            if name not in self._profile_names:
                tabs.add_tab(Tab(name, id=_tab_id(name)))
        self._profile_names = list(names)

    def set_profile_tab(self, name: str | None) -> None:
        self.query_one("#profiles", Tabs).active = _tab_id(name) if name else "p-all"

    @on(Tabs.TabActivated, "#profiles")
    def _profile_tab(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id or "p-all"
        name = next((n for n in self._profile_names if _tab_id(n) == tab_id), None)
        pane = self.query_one("#sessions", SessionsPane)
        if pane.filters.profile != name:
            pane.set_profile(name)
        for rp in self._resource_panes():
            rp.set_profile(name)

    # ------------------------------------------------------------------ actions

    def action_pane(self, method: str) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        current = self.query_one("#main", ContentSwitcher).current
        if current != "sessions":
            target = self.query_one(f"#{current}")
            if method == "do_tag" and hasattr(target, "do_tag"):
                target.do_tag()
                return
            self.notify("That key applies to the Sessions section.", timeout=2)
            return
        getattr(self.query_one("#sessions", SessionsPane), method)()

    def action_rebuild(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self.query_one("#sessions", SessionsPane).reload(rebuild=True)

    def action_help(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self.push_screen(HelpModal())


def run() -> None:
    ThemisApp().run()
