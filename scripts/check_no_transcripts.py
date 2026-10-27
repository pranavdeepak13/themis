#!/usr/bin/env python3
"""Pre-commit guard (CLAUDE.md rule 7): refuse staged files that look like real Claude data."""

from __future__ import annotations

import json
import re
import subprocess
import sys

PROFILE_PATH = re.compile(
    r"(^|/)\.claude(-[\w.\-]+)?/(projects|file-history|session-env|shell-snapshots|todos|debug|"
    r"sessions|history\.jsonl|\.credentials\.json)"
)
HOME_PATH = re.compile(r"/Users/[^/\s]+/\.claude")
MAX_JSON_ENTRY_LINES = 3
# Key names are built, not spelled as one literal, so this file does not trip its own check.
KEY_PARENT = '"parent' + 'Uuid"'
KEY_SESSION = '"session' + 'Id"'


def check_path(path: str) -> str | None:
    if PROFILE_PATH.search(path):
        return "path is inside a Claude profile directory"
    if path.endswith(".jsonl"):
        return "JSONL files are not allowed (fixtures are generated, never committed)"
    return None


def check_text(text: str) -> str | None:
    if HOME_PATH.search(text):
        return "contains an absolute ~/.claude* path"
    transcript_like = 0
    for line in text.splitlines():
        if KEY_PARENT in line and KEY_SESSION in line:
            return "contains transcript entries"
        s = line.strip()
        if s.startswith("{") and (KEY_SESSION in s or '"uuid"' in s):
            try:
                if isinstance(json.loads(s), dict):
                    transcript_like += 1
            except ValueError:
                pass
    if transcript_like > MAX_JSON_ENTRY_LINES:
        return "contains long transcript-like JSONL"
    return None


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.splitlines() if p]


def staged_text(path: str) -> str | None:
    res = subprocess.run(["git", "show", f":{path}"], capture_output=True, check=True)
    if b"\0" in res.stdout[:8192]:
        return None
    return res.stdout.decode("utf-8", errors="replace")


def main() -> int:
    bad: list[str] = []
    for path in staged_files():
        reason = check_path(path)
        if reason is None and (text := staged_text(path)) is not None:
            reason = check_text(text)
        if reason:
            bad.append(f"  {path}: {reason}")
    if bad:
        print("Refusing to commit possible real session data:\n" + "\n".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
