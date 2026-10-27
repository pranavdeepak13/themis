"""Find the file set that belongs to one session (read-only stat walk)."""

from __future__ import annotations

import os
from pathlib import Path

from themis.sessions.model import RelatedFile


def _walk(root: Path, kind_of) -> list[tuple[str, Path, int, int]]:
    out: list[tuple[str, Path, int, int]] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        out.append((kind_of(Path(e.path)), Path(e.path), st.st_size, st.st_mtime_ns))
        except OSError:
            continue
    return out


def session_files(profile_dir: Path, project_dir: Path, session_id: str) -> list[tuple[RelatedFile, int]]:
    """Return (file, mtime_ns) for every file of the session, transcript first."""
    found: list[tuple[str, Path, int, int]] = []
    main = project_dir / f"{session_id}.jsonl"
    try:
        st = main.stat()
        found.append(("transcript", main, st.st_size, st.st_mtime_ns))
    except OSError:
        pass

    def project_kind(p: Path) -> str:
        parts = p.relative_to(project_dir / session_id).parts
        return "subagent" if parts[0] == "subagents" else parts[0]

    sub = project_dir / session_id
    if sub.is_dir():
        found += _walk(sub, project_kind)
    for sub_name in ("file-history", "session-env"):
        d = profile_dir / sub_name / session_id
        if d.is_dir():
            found += _walk(d, lambda _p, k=sub_name: k)
    for sub_name in ("todos", "debug"):
        d = profile_dir / sub_name
        if d.is_dir():
            try:
                for e in os.scandir(d):
                    if e.name.startswith(session_id) and e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        found.append((sub_name, Path(e.path), st.st_size, st.st_mtime_ns))
            except OSError:
                pass
    return [
        (RelatedFile(kind=k, path=str(p.relative_to(profile_dir)), size=size), mtime) for k, p, size, mtime in found
    ]
