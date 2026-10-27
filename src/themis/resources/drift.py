"""Unified diffs between drifted copies of a resource."""

from __future__ import annotations

import difflib
from pathlib import Path

from themis.redact import redact_text
from themis.resources.fsutil import dir_files, read_text
from themis.resources.model import Resource

MAX_DIFF_LINES = 400


def _files(r: Resource) -> dict[str, Path]:
    if r.kind == "skill" and r.extra.get("dir"):
        root = Path(r.extra["dir"])
        return {str(f.relative_to(root)): f for f in dir_files(root)}
    return {Path(r.path).name: Path(r.path)} if r.path else {}


def diff_resources(a: Resource, b: Resource) -> str:
    """Unified diff of every differing file between two copies (redacted, capped)."""
    fa, fb = _files(a), _files(b)
    out: list[str] = []
    for rel in sorted({*fa, *fb}):
        ta = read_text(fa[rel]) if rel in fa else None
        tb = read_text(fb[rel]) if rel in fb else None
        if ta == tb:
            continue
        if ta is None or tb is None:
            out.append(f"--- {rel}: only in {'second' if ta is None else 'first'} copy\n")
            continue
        out.extend(
            difflib.unified_diff(
                redact_text(ta).splitlines(True),
                redact_text(tb).splitlines(True),
                f"{a.profile}/{rel}",
                f"{b.profile}/{rel}",
            )
        )
    lines = "".join(out).splitlines(True)
    if len(lines) > MAX_DIFF_LINES:
        lines = [*lines[:MAX_DIFF_LINES], f"... {len(lines) - MAX_DIFF_LINES} more lines\n"]
    return "".join(lines)


def drift_peers(r: Resource, all_resources: list[Resource]) -> list[Resource]:
    """Other copies with the same kind, name and origin whose content differs."""
    return [
        o
        for o in all_resources
        if o is not r
        and (o.kind, o.origin, o.name) == (r.kind, r.origin, r.name)
        and o.content_hash != r.content_hash
        and not o.broken
    ]
