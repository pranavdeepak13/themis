"""Tolerant JSON reading. Failures are reported by path only, never with file content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson


def read_json(path: Path) -> tuple[Any, str | None]:
    """(data, error). A missing file is (None, None); invalid JSON is (None, 'invalid JSON')."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"unreadable ({exc.__class__.__name__})"
    try:
        return orjson.loads(raw), None
    except orjson.JSONDecodeError:
        return None, "invalid JSON"
