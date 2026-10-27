"""Slash command and subagent scanners: markdown files with optional frontmatter."""

from __future__ import annotations

import os
from pathlib import Path

from themis.resources.fsutil import estimate_tokens, file_hash, link_info, parse_frontmatter, read_text
from themis.resources.model import Resource


def _md_files(root: Path, depth: int = 0) -> list[tuple[Path, str]]:
    """(path, relative name without .md) for markdown files; real subdirectories namespace with ':'."""
    out: list[tuple[Path, str]] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if e.name.startswith("."):
            continue
        p = Path(e.path)
        if e.name.endswith(".md") and (p.is_file() or e.is_symlink()):
            out.append((p, e.name[:-3]))
        elif p.is_dir() and not e.is_symlink() and depth < 3:
            out.extend((fp, f"{e.name}:{n}") for fp, n in _md_files(p, depth + 1))
    return out


def scan_markdown(
    root: Path, kind: str, profile: str, origin: str = "user", origin_name: str | None = None
) -> list[Resource]:
    out: list[Resource] = []
    for path, rel in _md_files(root):
        text = read_text(path)
        is_link, broken, real = link_info(path)
        fm, _body, ok = parse_frontmatter(text) if text is not None else ({}, "", False)
        name = rel if kind == "command" else (fm.get("name") or rel)
        desc = fm.get("description") or None
        out.append(
            Resource(
                kind=kind,
                name=name,
                profile=profile,
                origin=origin,
                origin_name=origin_name,
                path=str(path),
                realpath=real,
                is_symlink=is_link,
                broken=broken or text is None,
                content_hash=file_hash(path),
                description=desc,
                frontmatter=fm,
                # commands need no frontmatter; agents need name and description
                frontmatter_ok=True if kind == "command" else (ok and bool(fm.get("name")) and bool(desc)),
                tokens_startup=estimate_tokens(f"{name} {desc or ''}"),
                tokens_full=estimate_tokens(text or ""),
                extra={"entry": path.name},
            )
        )
    return out
