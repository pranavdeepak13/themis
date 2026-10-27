from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "guard", Path(__file__).parent.parent / "scripts" / "check_no_transcripts.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)  # type: ignore[union-attr]


def test_paths() -> None:
    assert guard.check_path(".claude-pcl/projects/x/y.txt")
    assert guard.check_path("tests/data/session.jsonl")
    assert guard.check_path("src/themis/cli.py") is None
    assert guard.check_path(".claude/settings.json") is None  # this repo's own project settings


def test_text() -> None:
    sid = guard.KEY_SESSION
    line = '{"type":"user",' + sid + ':"abc","uuid":"1"}\n'
    assert guard.check_text(line * 5)
    assert guard.check_text(line) is None
    assert guard.check_text("{" + guard.KEY_PARENT + ":null," + sid + ':"s"}')
    assert guard.check_text("see /Users/" + "someone/.claude-pcl/projects")
    assert guard.check_text("plain prose about sessions") is None
