"""Advisor findings. Advisory only: nothing here edits a file."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

SEVERITIES = ("high", "medium", "low", "info")
CONFIDENCES = ("high", "medium", "low")


class Advice(BaseModel):
    rule: str  # unused-skills, unused-mcp, startup-overhead, duplicate-skills, drift, claude-md, high-context, ...
    severity: str
    confidence: str
    title: str
    summary: str
    profiles: list[str]
    files: list[str] = Field(default_factory=list)  # exact paths involved
    evidence: list[str] = Field(default_factory=list)
    goal: str = ""
    tokens_per_session: int | None = None  # estimated tokens saved at the start of each session
    tokens_per_month: int | None = None  # the same, times sessions in the last 30 days (estimate)
    prompt: str = ""  # ready-to-paste Claude Code prompt

    @property
    def id(self) -> str:
        key = "|".join([self.rule, self.title, *sorted(self.profiles)])
        return hashlib.sha1(key.encode()).hexdigest()[:10]  # noqa: S324 - identifier, not security
