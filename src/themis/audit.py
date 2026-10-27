"""Append-only audit log for every write Themis performs (rule 1)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import orjson

from themis.paths import audit_log_path


def record(action: str, **fields: Any) -> None:
    path = audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": dt.datetime.now(dt.UTC).isoformat(), "action": action, **fields}
    with path.open("ab") as fh:
        fh.write(orjson.dumps(entry, default=str) + b"\n")
