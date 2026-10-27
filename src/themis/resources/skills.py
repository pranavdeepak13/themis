"""Skill scanner: SKILL.md directories under a skills root (user, synced, plugin or project)."""

from __future__ import annotations

import os
from pathlib import Path

from themis.resources.fsutil import (
    dir_hash,
    estimate_tokens,
    link_info,
    parse_frontmatter,
    read_text,
)
from themis.resources.model import Resource

MAX_DEPTH = 4


def skill_from_dir(skill_dir: Path, profile: str, origin: str, origin_name: str | None, fallback: str) -> Resource:
    main = skill_dir / "SKILL.md"
    text = read_text(main)
    is_link, broken, real = link_info(skill_dir)
    fm, body, ok = parse_frontmatter(text) if text is not None else ({}, "", False)
    name = fm.get("name") or fallback
    desc = fm.get("description") or None
    ok = ok and bool(desc)  # Claude Code needs a description; the name defaults to the directory name
    return Resource(
        kind="skill",
        name=name,
        profile=profile,
        origin=origin,
        origin_name=origin_name,
        path=str(main),
        realpath=real,
        is_symlink=is_link,
        broken=broken or text is None,
        content_hash=dir_hash(skill_dir) if text is not None else None,
        description=desc,
        frontmatter=fm,
        frontmatter_ok=ok,
        tokens_startup=estimate_tokens(f"{name} {desc or ''}"),
        tokens_full=estimate_tokens(text or ""),
        extra={"dir": str(skill_dir), "entry": fallback},
    )


def scan_skills(
    root: Path, profile: str, origin: str = "user", origin_name: str | None = None, depth: int = 0
) -> list[Resource]:
    """Find skills below root. Symlinked entries are skills when they hold a SKILL.md; real directories
    without one are containers (e.g. `synced/<id>/` or plugin categories) and are searched deeper."""
    out: list[Resource] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if e.name.startswith("."):
            continue
        p = Path(e.path)
        if e.is_symlink() and not p.exists():
            out.append(skill_from_dir(p, profile, origin, origin_name, e.name))  # broken link
            continue
        if not p.is_dir():
            continue
        if (p / "SKILL.md").is_file():
            out.append(skill_from_dir(p, profile, origin, origin_name, e.name))
        elif not e.is_symlink() and depth < MAX_DEPTH:
            sub_origin = "synced" if e.name == "synced" and origin == "user" else origin
            out.extend(scan_skills(p, profile, sub_origin, origin_name, depth + 1))
    return out
