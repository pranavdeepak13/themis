"""Placeholder for sections that arrive in later phases."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static


class PlaceholderPane(Vertical):
    def __init__(self, title: str, phase: str, blurb: str, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self._title, self._phase, self._blurb = title, phase, blurb

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="placeholder-title")
        yield Static(f"{self._blurb}\n\nComing in {self._phase}.", classes="placeholder-text")
