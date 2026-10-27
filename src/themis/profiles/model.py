from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Profile(BaseModel):
    """A Claude Code config directory (what CLAUDE_CONFIG_DIR points at)."""

    name: str
    path: Path
    alias: str | None = None
    problem: str | None = None  # why the directory cannot be scanned right now, if it cannot

    @property
    def display(self) -> str:
        return self.alias or self.name

    @property
    def projects_dir(self) -> Path:
        return self.path / "projects"
