"""Per-session JSON summary cache in ~/.themis/cache/<profile>/<session-id>.json.

A cached summary is valid while the session's signature (transcript size and mtime plus the
count, bytes and newest mtime of all related files) and the schema version are unchanged.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import orjson

from themis.paths import cache_dir
from themis.profiles.model import Profile
from themis.sessions.files import session_files
from themis.sessions.model import SCHEMA_VERSION, SessionSummary
from themis.sessions.parser import parse_session

Progress = Callable[[int, int], None]
POOL_THRESHOLD = 6


def _signature(profile_dir: Path, transcript: Path) -> list[int]:
    files = session_files(profile_dir, transcript.parent, transcript.stem)
    main = next((f for f, _ in files if f.kind == "transcript"), None)
    main_mtime = next((m for f, m in files if f.kind == "transcript"), 0)
    return [
        main.size if main else 0,
        main_mtime,
        len(files),
        sum(f.size for f, _ in files),
        max((m for _, m in files), default=0),
    ]


def _cache_file(profile: str, session_id: str) -> Path:
    return cache_dir() / profile / f"{session_id}.json"


def _read(profile: str, session_id: str, sig: list[int]) -> SessionSummary | None:
    try:
        raw = orjson.loads(_cache_file(profile, session_id).read_bytes())
        if raw.get("schema") != SCHEMA_VERSION or raw.get("sig") != sig:
            return None
        return SessionSummary.model_validate(raw["summary"])
    except (OSError, orjson.JSONDecodeError, ValueError, KeyError):
        return None


def _write(summary: SessionSummary, sig: list[int]) -> None:
    path = _cache_file(summary.profile, summary.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = orjson.dumps({"schema": SCHEMA_VERSION, "sig": sig, "summary": summary.model_dump(mode="json")})
    tmp = path.with_suffix(f".tmp{os.getpid()}-{threading.get_ident()}")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _parse_and_store(args: tuple[str, str, str]) -> SessionSummary:
    """Top-level so the process pool can pickle it."""
    name, profile_dir, transcript = args
    pdir, tpath = Path(profile_dir), Path(transcript)
    sig = _signature(pdir, tpath)  # taken before parsing: a file growing mid-parse is re-parsed
    summary = parse_session(name, pdir, tpath)
    _write(summary, sig)
    return summary


def find_transcripts(profile: Profile) -> list[Path]:
    out: list[Path] = []
    try:
        for proj in sorted(profile.projects_dir.iterdir()):
            if proj.is_dir():
                out.extend(sorted(p for p in proj.glob("*.jsonl") if p.is_file()))
    except OSError:
        pass
    return out


def load_sessions(
    profiles: list[Profile], *, rebuild: bool = False, progress: Progress | None = None
) -> list[SessionSummary]:
    """All sessions of the given profiles, using the cache wherever it is still valid."""
    results: list[SessionSummary] = []
    misses: list[tuple[str, str, str]] = []
    todo = [(p, t) for p in profiles for t in find_transcripts(p)]
    total, done = len(todo), 0
    keep: dict[str, set[str]] = {p.name: set() for p in profiles}
    for profile, transcript in todo:
        keep[profile.name].add(transcript.stem)
        cached = None if rebuild else _read(profile.name, transcript.stem, _signature(profile.path, transcript))
        if cached is not None:
            results.append(cached)
            done += 1
            if progress:
                progress(done, total)
        else:
            misses.append((profile.name, str(profile.path), str(transcript)))

    if len(misses) >= POOL_THRESHOLD:
        with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
            for summary in pool.map(_parse_and_store, misses, chunksize=2):
                results.append(summary)
                done += 1
                if progress:
                    progress(done, total)
    else:
        for args in misses:
            results.append(_parse_and_store(args))
            done += 1
            if progress:
                progress(done, total)

    _prune(keep)
    return results


def _prune(keep: dict[str, set[str]]) -> None:
    """Drop cache entries whose session no longer exists (only inside ~/.themis/cache)."""
    for name, ids in keep.items():
        d = cache_dir() / name
        if not d.is_dir():
            continue
        for f in d.glob("*.json"):
            if f.stem not in ids:
                f.unlink(missing_ok=True)
