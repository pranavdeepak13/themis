"""Row model, filtering and sorting for the session table. Pure functions, no UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from themis.config import Config
from themis.sessions import meta as meta_store
from themis.sessions.metrics import context_percent
from themis.sessions.model import SessionSummary


@dataclass
class Row:
    summary: SessionSummary
    topic: str
    tags: list[str]
    project: str  # folder name of the cwd
    context_pct: float
    model: str
    renamed: bool = False
    risk_days_left: float | None = None
    haystack: str = field(default="", repr=False)

    @property
    def key(self) -> str:
        return f"{self.summary.profile}/{self.summary.session_id}"

    @property
    def last_active(self) -> str:
        return self.summary.ended_at or ""


def build_rows(
    sessions: list[SessionSummary], cfg: Config, meta: dict[str, Any], risk: dict[str, float] | None = None
) -> list[Row]:
    rows = []
    for s in sessions:
        title = meta_store.title_of(meta, s.profile, s.session_id)
        tags = meta_store.tags_of(meta, s.profile, s.session_id)
        topic = title or s.topic or "(no topic)"
        project = Path(s.cwd).name if s.cwd else s.project_dir
        model = (s.last_model or "-").removeprefix("claude-")
        hay = " ".join([topic, project, s.cwd or "", s.session_id, model, s.git_branch or "", *tags]).lower()
        rows.append(
            Row(
                s,
                topic,
                tags,
                project,
                context_percent(s, cfg, peak=True),
                model,
                bool(title),
                (risk or {}).get(f"{s.profile}/{s.session_id}"),
                hay,
            )
        )
    return rows


@dataclass
class Filters:
    profile: str | None = None
    project: str = ""
    model: str = ""
    tag: str = ""
    text: str = ""
    since: date | None = None
    until: date | None = None


def parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()) if text.strip() else None
    except ValueError:
        return None


def apply_filters(rows: list[Row], f: Filters) -> list[Row]:
    out = []
    for r in rows:
        if f.profile and r.summary.profile != f.profile:
            continue
        if f.project and f.project.lower() not in r.project.lower():
            continue
        if f.model and f.model.lower() not in r.model.lower():
            continue
        if f.tag and not any(f.tag.lower() in t for t in r.tags):
            continue
        if f.text and not all(w in r.haystack for w in f.text.lower().split()):
            continue
        day = parse_date(r.last_active[:10])
        if (f.since and (day is None or day < f.since)) or (f.until and (day is None or day > f.until)):
            continue
        out.append(r)
    return out


SORT_KEYS: dict[str, Callable[[Row], Any]] = {
    "profile": lambda r: r.summary.profile,
    "last": lambda r: r.last_active,
    "topic": lambda r: r.topic.lower(),
    "project": lambda r: r.project.lower(),
    "model": lambda r: r.model,
    "tokens": lambda r: r.summary.tokens.total,
    "context": lambda r: r.context_pct,
    "memory": lambda r: len(r.summary.memory_writes),
    "size": lambda r: r.summary.disk_bytes,
    "tags": lambda r: ",".join(r.tags),
}


def sort_rows(rows: list[Row], column: str, descending: bool) -> list[Row]:
    return sorted(rows, key=SORT_KEYS[column], reverse=descending)
