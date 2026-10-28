"""Modals for the resource screens: profile picker, info, settings editor."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, Static, TextArea

from themis.resources.settings import validate_settings_text


def diff_text(diff: str) -> Text:
    out = Text()
    for line in diff.splitlines():
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
    return out if diff else Text("(no differences)", style="dim")


class ProfilePickModal(ModalScreen[list[str] | None]):
    """Tick the profiles that should receive something. Dismisses with the chosen names, or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, names: list[str], preselected: set[str], note: str = "") -> None:
        super().__init__()
        self._title, self._names, self._pre, self._note = title, names, preselected, note

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            if self._note:
                yield Static(self._note)
            for i, n in enumerate(self._names):
                yield Checkbox(n, value=n in self._pre, id=f"pick-{i}")
            with Horizontal(id="dialog-buttons"):
                yield Button("Next", id="ok", variant="primary")
                yield Button("All", id="all")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#all")
    def _all(self) -> None:
        for cb in self.query(Checkbox):
            cb.value = True

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        chosen = [n for i, n in enumerate(self._names) if self.query_one(f"#pick-{i}", Checkbox).value]
        if chosen:
            self.dismiss(chosen)
        else:
            self.notify("Pick at least one profile.", severity="warning")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class InfoModal(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title: str, body: RenderableType) -> None:
        super().__init__()
        self._title, self._body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(self._body)
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close", variant="primary")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)


class EditSettingsModal(ModalScreen[str | None]):
    """JSON editor with live validation. Dismisses with the edited text, or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, profile: str, text: str) -> None:
        super().__init__()
        self._profile, self._text = profile, text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="wide"):
            yield Label(f"Edit settings.json: {self._profile}", id="dialog-title")
            yield Static("Secret values are masked; leave the mask in place to keep the original.", id="edit-note")
            yield TextArea(self._text, language="json", id="edit-area", show_line_numbers=True)
            yield Static("", id="edit-error")
            with Horizontal(id="dialog-buttons"):
                yield Button("Review diff", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    @on(TextArea.Changed)
    def _changed(self, event: TextArea.Changed) -> None:
        problem = validate_settings_text(event.text_area.text)
        self.query_one("#edit-error", Static).update(
            Text(problem, style="bold reverse") if problem else Text("Valid JSON.", style="bold")
        )
        self.query_one("#ok", Button).disabled = problem is not None

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one(TextArea).text)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
