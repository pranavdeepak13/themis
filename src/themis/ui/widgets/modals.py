"""Modal dialogs: confirm (with optional typed token), text prompt, choice list, resume, help."""

from __future__ import annotations

from rich.console import RenderableType
from rich.table import Table
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from themis.sessions.actions import ArchiveEntry


class ConfirmModal(ModalScreen[tuple[bool, bool]]):
    """Dismisses with (confirmed, checkbox_checked). A token makes the user type it to enable Confirm."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        body: RenderableType,
        *,
        token: str | None = None,
        confirm_label: str = "Confirm",
        danger: bool = False,
        checkbox: str | None = None,
        blocked: bool = False,
    ) -> None:
        super().__init__()
        self._title, self._body, self._token = title, body, token
        self._label, self._danger, self._checkbox, self._blocked = confirm_label, danger, checkbox, blocked

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="danger" if self._danger else ""):
            yield Label(self._title, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(self._body)
            if self._checkbox:
                yield Checkbox(self._checkbox, id="dialog-check")
            if self._token and not self._blocked:
                yield Label(f"Type [b]{self._token}[/b] to confirm:", id="dialog-prompt")
                yield Input(id="dialog-token", placeholder=self._token)
            with Horizontal(id="dialog-buttons"):
                yield Button(
                    self._label,
                    id="ok",
                    variant="error" if self._danger else "primary",
                    disabled=self._blocked or bool(self._token),
                )
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#dialog-token" if self._token and not self._blocked else "#cancel").focus()

    @on(Input.Changed, "#dialog-token")
    def _token_changed(self, event: Input.Changed) -> None:
        self.query_one("#ok", Button).disabled = event.value.strip() != self._token

    @on(Input.Submitted, "#dialog-token")
    def _token_submitted(self) -> None:
        if not self.query_one("#ok", Button).disabled:
            self._finish(True)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._finish(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self._finish(False)

    def _finish(self, ok: bool) -> None:
        checked = bool(self.query("#dialog-check")) and self.query_one("#dialog-check", Checkbox).value
        self.dismiss((ok, checked))


class TextPromptModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, label: str, initial: str = "") -> None:
        super().__init__()
        self._title, self._label, self._initial = title, label, initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            yield Label(self._label)
            yield Input(self._initial, id="prompt-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Save", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one(Input).value)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: list[tuple[str, str]], empty: str = "Nothing to choose from.") -> None:
        super().__init__()
        self._title, self._choices, self._empty = title, choices, empty

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            if self._choices:
                yield OptionList(*[Option(label, id=value) for value, label in self._choices], id="choices")
            else:
                yield Label(self._empty)
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if self._choices:
            self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ResumeModal(ModalScreen[str | None]):
    """Shows the resume command. Dismisses with 'copy', 'launch' or None."""

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, command: str, *, can_launch: bool, cwd_missing: bool) -> None:
        super().__init__()
        self._command, self._can_launch, self._cwd_missing = command, can_launch, cwd_missing

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Resume this session", id="dialog-title")
            yield Static(self._command, id="resume-command", markup=False)
            if self._cwd_missing:
                yield Label("[yellow]The session's working directory no longer exists on disk.[/yellow]")
            if not self._can_launch:
                yield Label("[dim]Set resume_launcher in ~/.themis/config.toml to open this in another terminal.[/dim]")
            with Horizontal(id="dialog-buttons"):
                yield Button("Copy command", id="copy", variant="primary")
                yield Button("Open in terminal", id="launch", disabled=not self._can_launch)
                yield Button("Close", id="close")

    @on(Button.Pressed, "#copy")
    def _copy(self) -> None:
        self.dismiss("copy")

    @on(Button.Pressed, "#launch")
    def _launch(self) -> None:
        self.dismiss("launch")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)


class ArchivesModal(ModalScreen[ArchiveEntry | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, entries: list[ArchiveEntry]) -> None:
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Archived sessions (select one to restore)", id="dialog-title")
            if self._entries:
                yield OptionList(
                    *[
                        Option(
                            f"{e.profile}  {e.session_id[:8]}  {e.created[:10]}  {e.topic or '(no topic)'}", id=str(i)
                        )
                        for i, e in enumerate(self._entries)
                    ],
                    id="choices",
                )
            else:
                yield Label("No archives yet.")
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="cancel")

    def on_mount(self) -> None:
        if self._entries:
            self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._entries[int(event.option.id or 0)])

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


HELP_KEYS = [
    ("Everywhere", ""),
    (
        "1 to 9, 0, i",
        "1 Sessions 2 Skills 3 Agents 4 Commands 5 Plugins 6 MCP 7 Hooks 8 Settings 9 Compare 0 Doctor i Insights",
    ),
    (
        "Left / Right",
        "Switch the profile filter when the profile bar has focus (it filters every section except Compare)",
    ),
    ("Tab / Shift+Tab", "Move focus; Enter or a click activates a button or row"),
    ("t", "Tag the selected session (Sessions) or resource (Skills, Agents, Commands, Plugins, MCP, Hooks)"),
    ("Ctrl+R", "Rescan everything (sessions cache rebuild)"),
    ("?", "This help"),
    ("q", "Quit"),
    ("Sessions", ""),
    ("/", "Full-text search across sessions (Esc cancels)"),
    ("Space", "Mark or unmark the session under the cursor (bulk select)"),
    ("Ctrl+A / u", "Mark all shown / clear marks"),
    ("r", "Resume: show and copy the resume command"),
    ("d", "Delete (permanent, typed confirmation); acts on marked sessions if any"),
    ("a", "Archive to ~/.themis/archive; acts on marked sessions if any"),
    ("v", "Browse archives and restore"),
    ("n", "Rename (stored in Themis, never in the transcript)"),
    ("e", "Export to redacted markdown"),
    ("c / m", "Copy / move the session to another profile"),
    ("s", "Cycle the sort column; click a column header to sort by it"),
    ("Resources", ""),
    (
        "Buttons",
        "Promote (symlink to the shared copy), enable or disable a plugin or MCP server, health check, edit settings",
    ),
    ("Insights", ""),
    (
        "Buttons",
        "Window (7d to all), grouping (profile, project, model, day, week, month), copy prompt, export",
    ),
    (
        "Writes",
        "Every write shows a diff or file list, needs confirmation, is backed up where a file is edited, audited",
    ),
]


class HelpModal(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close"), ("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        table = Table.grid(padding=(0, 2))
        for key, text in HELP_KEYS:
            if text:
                table.add_row(f"[b]{key}[/b]", text)
            else:
                table.add_row(f"\n[b]{key}[/b]", "")
        with Vertical(id="dialog"):
            yield Label("Keyboard shortcuts", id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(table)
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close", variant="primary")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
