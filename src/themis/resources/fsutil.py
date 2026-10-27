"""Filesystem helpers for scanners: symlink info, content hashes, frontmatter, token estimates."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

CHARS_PER_TOKEN = 4  # rough heuristic for English prose and code; an estimate, not a tokenizer
MAX_HASH_FILES = 300
MAX_READ = 2_000_000
_SKIP_DIRS = {"node_modules", ".git", "__pycache__"}


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def read_text(path: Path, limit: int = MAX_READ) -> str | None:
    try:
        with path.open("rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def link_info(path: Path) -> tuple[bool, bool, str | None]:
    """(is_symlink, broken, realpath)."""
    is_link = path.is_symlink()
    broken = is_link and not path.exists()
    try:
        real = os.path.realpath(path)
    except OSError:
        real = None
    return is_link, broken, real


def file_hash(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def dir_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dp, dns, fns in os.walk(root, followlinks=False):
        dns[:] = sorted(d for d in dns if d not in _SKIP_DIRS)
        for fn in sorted(fns):
            out.append(Path(dp) / fn)
            if len(out) >= MAX_HASH_FILES:
                return out
    return out


def dir_hash(root: Path) -> str | None:
    """Hash of relative names and contents of every file under root."""
    if not root.is_dir():
        return None
    h = hashlib.sha256()
    for f in dir_files(root):
        h.update(str(f.relative_to(root)).encode())
        fh = file_hash(f)
        h.update((fh or "?").encode())
    return h.hexdigest()[:16]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, bool]:
    """Minimal YAML-subset frontmatter: returns (fields, body, well_formed).

    Handles `key: value`, quoted values and indented or folded continuation lines. Nested structures
    are kept as raw text. Not a YAML parser; unknown shapes degrade to strings.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, False
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text, False
    fields: dict[str, str] = {}
    key: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] not in " \t" and ":" in raw:
            key, _, val = raw.partition(":")
            key = key.strip()
            fields[key] = val.strip()
        elif key is not None:
            fields[key] = (fields[key] + " " + raw.strip()).strip()
    for k, v in fields.items():
        v = v.strip()
        v = re.sub(r"^[>|][+-]?\s*", "", v)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        fields[k] = v.strip()
    return fields, "\n".join(lines[end + 1 :]), True
