"""User metadata (display names and tags) in ~/.themis/meta.json.

Keyed by "<profile>/<session-id>". Never writes into transcripts. Unknown top-level keys are
preserved so later phases can add resource tags to the same file.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

import orjson

from themis import audit
from themis.paths import meta_path

VERSION = 1


def _key(profile: str, session_id: str) -> str:
    return f"{profile}/{session_id}"


def load_meta() -> dict[str, Any]:
    try:
        raw = orjson.loads(meta_path().read_bytes())
    except (OSError, orjson.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("sessions"), dict):
        raw["sessions"] = {}
    raw.setdefault("version", VERSION)
    return raw


def _save(meta: dict[str, Any]) -> None:
    path = meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_bytes(orjson.dumps(meta, option=orjson.OPT_INDENT_2))
    os.replace(tmp, path)


def title_of(meta: dict[str, Any], profile: str, session_id: str) -> str | None:
    entry = meta["sessions"].get(_key(profile, session_id))
    return entry.get("title") if isinstance(entry, dict) else None


def tags_of(meta: dict[str, Any], profile: str, session_id: str) -> list[str]:
    entry = meta["sessions"].get(_key(profile, session_id))
    tags = entry.get("tags") if isinstance(entry, dict) else None
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def clean_tags(tags: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in tags:
        t = " ".join(t.split()).lower()
        if t:
            seen[t] = None
    return list(seen)


def _update(profile: str, session_id: str, **fields: Any) -> dict[str, Any]:
    meta = load_meta()
    entry = meta["sessions"].setdefault(_key(profile, session_id), {})
    for k, v in fields.items():
        if v in (None, "", []):
            entry.pop(k, None)
        else:
            entry[k] = v
    if not entry:
        meta["sessions"].pop(_key(profile, session_id), None)
    _save(meta)
    return meta


def set_title(profile: str, session_id: str, title: str | None) -> None:
    """Rename a session in Themis only. An empty title clears the rename."""
    title = " ".join((title or "").split())[:120] or None
    _update(profile, session_id, title=title)
    audit.record("session.rename", profile=profile, session_id=session_id, title=title)


def set_tags(profile: str, session_id: str, tags: Iterable[str]) -> list[str]:
    final = clean_tags(tags)
    _update(profile, session_id, tags=final)
    audit.record("session.tags", profile=profile, session_id=session_id, tags=final)
    return final


def bulk_tag(items: Iterable[tuple[str, str]], add: Iterable[str] = (), remove: Iterable[str] = ()) -> int:
    """Add and remove tags on many sessions with one write. Returns how many sessions changed."""
    items = list(items)
    add_l, rem = clean_tags(add), set(clean_tags(remove))
    meta = load_meta()
    changed = 0
    for profile, sid in items:
        entry = meta["sessions"].setdefault(_key(profile, sid), {})
        old = [t for t in entry.get("tags", []) if isinstance(t, str)]
        new = clean_tags([*old, *add_l])
        new = [t for t in new if t not in rem]
        if new != old:
            changed += 1
        if new:
            entry["tags"] = new
        else:
            entry.pop("tags", None)
        if not entry:
            meta["sessions"].pop(_key(profile, sid), None)
    _save(meta)
    audit.record("session.bulk_tag", sessions=[f"{p}/{s}" for p, s in items], add=add_l, remove=sorted(rem))
    return changed
