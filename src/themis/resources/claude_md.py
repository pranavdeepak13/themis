"""CLAUDE.md scanner: user-level (profile dir) and project-level files."""

from __future__ import annotations

from pathlib import Path

from themis.resources.fsutil import estimate_tokens, file_hash, link_info, read_text
from themis.resources.model import Resource


def _one(path: Path, profile: str, origin: str, origin_name: str | None, name: str) -> Resource | None:
    if not (path.is_file() or path.is_symlink()):
        return None
    text = read_text(path)
    is_link, broken, real = link_info(path)
    return Resource(
        kind="claude_md",
        name=name,
        profile=profile,
        origin=origin,
        origin_name=origin_name,
        path=str(path),
        realpath=real,
        is_symlink=is_link,
        broken=broken or text is None,
        content_hash=file_hash(path),
        tokens_startup=estimate_tokens(text or ""),  # CLAUDE.md is loaded whole at startup
        tokens_full=estimate_tokens(text or ""),
        extra={"lines": (text or "").count("\n") + 1 if text else 0},
    )


def scan_user_claude_md(profile_dir: Path, profile: str) -> list[Resource]:
    return [r for n in ("CLAUDE.md", "CLAUDE.local.md") if (r := _one(profile_dir / n, profile, "user", None, n))]


def scan_project_claude_md(project: Path, profile: str) -> list[Resource]:
    found = [
        _one(project / rel, profile, "project", str(project), f"{project.name}/{rel}")
        for rel in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
    ]
    return [r for r in found if r]
