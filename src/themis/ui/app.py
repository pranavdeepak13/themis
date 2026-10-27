"""The Themis Textual app. Runs in the terminal and, through textual-serve, in the browser."""

from __future__ import annotations

import re

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Footer, Header, OptionList, Tab, Tabs
from textual.widgets.option_list import Option

from themis.ui.screens.placeholder import PlaceholderPane
from themis.ui.screens.sessions import SessionsPane
from themis.ui.widgets.modals import HelpModal

SECTIONS: list[tuple[str, str, str, str]] = [  # id, label, phase, blurb
    ("sessions", "Sessions", "", ""),
    ("skills", "Skills", "Phase 3", "Skills across profiles: scope, drift, usage and token cost."),
    ("plugins", "Plugins", "Phase 3", "Installed and enabled plugins and marketplaces per profile."),
    ("mcp", "MCP", "Phase 3", "MCP servers per profile, with redacted config and an on-demand health check."),
    ("hooks", "Hooks", "Phase 3", "Hooks from user, project, local and plugin settings."),
    ("settings", "Settings", "Phase 3", "Merged settings with precedence and origin, secrets redacted."),
    ("insights", "Insights", "Phase 4", "Token dashboards and the optimisation advisor."),
]


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
        Binding("question_mark", "help", "Help"),
        Binding("v", "pane('do_restore')", "Archives", show=False),
        Binding("c", "pane('do_copy')", "Copy to", show=False),
        Binding("m", "pane('do_move')", "Move to", show=False),
        Binding("s", "pane('action_cycle_sort')", "Sort", show=False),
        Binding("u", "pane('action_clear_marks')", "Clear marks", show=False),
        Binding("ctrl+a", "pane('action_mark_all')", "Mark all", show=False),
        Binding("ctrl+r", "rebuild", "Rebuild cache", show=False),
        Binding("escape", "pane('action_escape')", "Back", show=False),
        *[Binding(str(i), f"section('{sid}')", label, show=False) for i, (sid, label, *_) in enumerate(SECTIONS, 1)],
    ]

    def __init__(self) -> None:
        super().__init__()
        self._profile_names: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(Tab("All", id="p-all"), id="profiles")
        with Horizontal(id="shell"):
            yield OptionList(*[Option(label, id=sid) for sid, label, *_ in SECTIONS], id="nav")
            with ContentSwitcher(initial="sessions", id="main"):
                for sid, label, phase, blurb in SECTIONS:
                    if sid == "sessions":
                        yield SessionsPane(id=sid)
                    else:
                        yield PlaceholderPane(label, phase, blurb, id=sid)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#nav", OptionList).highlighted = 0

    # ------------------------------------------------------------------ navigation

    @on(OptionList.OptionHighlighted, "#nav")
    @on(OptionList.OptionSelected, "#nav")
    def _nav(self, event: OptionList.OptionHighlighted | OptionList.OptionSelected) -> None:
        if event.option.id:
            self.query_one("#main", ContentSwitcher).current = event.option.id

    def action_section(self, section: str) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        nav = self.query_one("#nav", OptionList)
        nav.highlighted = [s[0] for s in SECTIONS].index(section)

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

    # ------------------------------------------------------------------ actions

    def action_pane(self, method: str) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self.query_one("#main", ContentSwitcher).current != "sessions":
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
