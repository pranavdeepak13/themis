from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

import orjson
import pytest

from fixtures.make_fixtures import SID_MAIN
from helpers import age_files, profiles_by_name, summary_of
from themis.paths import audit_log_path, cache_dir
from themis.sessions import actions
from themis.sessions.actions import CONFIRM, ActionError


def audit_actions() -> list[str]:
    return [orjson.loads(line)["action"] for line in audit_log_path().read_bytes().splitlines()]


def session_paths(profile_dir: Path) -> list[Path]:
    return [p for p in profile_dir.rglob("*") if SID_MAIN in str(p) and p.is_file()]


def test_delete_refuses_recently_modified_files(tree: dict[str, Path]) -> None:
    prof, s = profiles_by_name()["alpha"], summary_of()
    plan = actions.plan_files(prof, s)
    assert not plan.ok and any("Modified" in r for r in plan.refusals)
    with pytest.raises(ActionError):
        actions.delete_session(prof, s, confirm=actions.delete_token(s))
    assert session_paths(tree["alpha"])  # untouched


def test_delete_dry_run_and_token(tree: dict[str, Path]) -> None:
    age_files(tree["alpha"])
    prof, s = profiles_by_name()["alpha"], summary_of()
    dry = actions.delete_session(prof, s, dry_run=True)
    assert dry.ok and dry.dry_run and dry.files == len(s.related_files) and dry.bytes == s.disk_bytes
    assert session_paths(tree["alpha"])
    with pytest.raises(ActionError, match="token"):
        actions.delete_session(prof, s, confirm="nope")
    assert not audit_log_path().exists()


def test_delete_removes_exactly_the_session(tree: dict[str, Path]) -> None:
    age_files(tree["alpha"])
    prof, s = profiles_by_name()["alpha"], summary_of()
    other = next(p for p in tree["alpha"].rglob("*.jsonl") if SID_MAIN not in p.name and "subagents" not in str(p))
    res = actions.delete_session(prof, s, confirm=actions.delete_token(s))
    assert res.ok and res.files == len(s.related_files)
    assert not session_paths(tree["alpha"])
    assert not (tree["alpha"] / "projects/-work-demo-app" / SID_MAIN).exists()  # emptied dirs removed
    assert not (tree["alpha"] / "file-history" / SID_MAIN).exists()
    assert other.exists() and (tree["alpha"] / "settings.json").exists()
    assert not (cache_dir() / "alpha" / f"{SID_MAIN}.json").exists()
    assert audit_actions() == ["session.delete"]


def test_delete_refuses_symlinked_session_dir_pointing_outside(tree: dict[str, Path], tmp_path: Path) -> None:
    age_files(tree["alpha"])
    prof, s = profiles_by_name()["alpha"], summary_of()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    link = tree["alpha"] / "file-history" / SID_MAIN
    for p in link.iterdir():
        p.unlink()
    link.rmdir()
    link.symlink_to(outside)
    plan = actions.plan_files(prof, s)
    assert not plan.ok
    with pytest.raises(ActionError):
        actions.delete_session(prof, s, confirm=actions.delete_token(s))
    assert (outside / "keep.txt").read_text() == "precious"


def test_delete_refuses_file_changed_after_plan(tree: dict[str, Path]) -> None:
    age_files(tree["alpha"])
    prof, s = profiles_by_name()["alpha"], summary_of()
    plan = actions.plan_files(prof, s)
    target = tree["alpha"] / "file-history" / SID_MAIN / "abc@v1"
    os.utime(target, (time.time() - 5000, time.time() - 5000))
    with pytest.raises(ActionError, match="changed"):
        actions._delete_planned(prof, s, plan)


def test_delete_refuses_unsafe_session_id(tree: dict[str, Path]) -> None:
    prof, s = profiles_by_name()["alpha"], summary_of()
    bad = s.model_copy(update={"session_id": "../../etc"})
    assert not actions.plan_files(prof, bad).ok


def test_archive_restore_round_trip(tree: dict[str, Path]) -> None:
    prof, s = profiles_by_name()["alpha"], summary_of()
    originals = {str(p.relative_to(tree["alpha"])): p.read_bytes() for p in session_paths(tree["alpha"])}
    dry = actions.archive_session(prof, s, dry_run=True)
    assert dry.ok and not Path(dry.path or "").exists()
    with pytest.raises(ActionError):
        actions.archive_session(prof, s, confirm="x")
    res = actions.archive_session(prof, s, confirm=CONFIRM)
    assert res.ok and Path(res.path or "").is_file()
    assert session_paths(tree["alpha"])  # originals are kept
    again = actions.archive_session(prof, s, dry_run=True)
    assert not again.ok and "Already archived" in again.reasons[0]
    listed = actions.list_archives("alpha")
    assert [a.session_id for a in listed] == [SID_MAIN] and listed[0].files == len(originals)

    with pytest.raises(ActionError, match="refused"):  # files still exist
        actions.restore_session(prof, SID_MAIN, confirm=CONFIRM)
    age_files(tree["alpha"])
    actions.delete_session(prof, s, confirm=actions.delete_token(s))
    assert not session_paths(tree["alpha"])
    restored = actions.restore_session(prof, SID_MAIN, confirm=CONFIRM)
    assert restored.files == len(originals)
    back = {str(p.relative_to(tree["alpha"])): p.read_bytes() for p in session_paths(tree["alpha"])}
    assert back == originals
    newest = max(p.stat().st_mtime for p in session_paths(tree["alpha"]))
    assert time.time() - newest < 60  # restored files get fresh mtimes
    assert audit_actions() == ["session.archive", "session.delete", "session.restore"]


def test_restore_rejects_traversal_member(tree: dict[str, Path], tmp_path: Path) -> None:
    prof, s = profiles_by_name()["alpha"], summary_of()
    actions.archive_session(prof, s, confirm=CONFIRM)
    tar_path, manifest_path = actions._archive_paths("alpha", SID_MAIN)
    manifest = orjson.loads(manifest_path.read_bytes())
    manifest["files"].append({"path": "../escape.txt", "size": 1, "sha256": "0"})
    manifest_path.write_bytes(orjson.dumps(manifest))
    age_files(tree["alpha"])
    actions.delete_session(prof, s, confirm=actions.delete_token(s))
    with pytest.raises(ActionError, match="refused") as e:
        actions.restore_session(prof, SID_MAIN, confirm=CONFIRM)
    assert any("Unsafe" in r for r in e.value.reasons)
    assert not (tree["alpha"].parent / "escape.txt").exists()
    assert tarfile.is_tarfile(tar_path)


def test_copy_to_other_profile_keeps_project_dir(tree: dict[str, Path]) -> None:
    a, b = profiles_by_name()["alpha"], profiles_by_name()["beta"]
    s = summary_of()
    assert not actions.copy_session(a, a, s, dry_run=True).ok
    res = actions.copy_session(a, b, s, confirm=CONFIRM)
    assert res.ok and res.files == len(s.related_files)
    assert (tree["beta"] / "projects/-work-demo-app" / f"{SID_MAIN}.jsonl").is_file()
    assert (tree["beta"] / "file-history" / SID_MAIN / "abc@v1").read_bytes() == b"x" * 100
    assert session_paths(tree["alpha"])
    assert summary_of(profile="beta").tokens.total == s.tokens.total
    with pytest.raises(ActionError):  # second copy would overwrite
        actions.copy_session(a, b, s, confirm=CONFIRM)


def test_move_is_copy_plus_confirmed_delete(tree: dict[str, Path]) -> None:
    a, b = profiles_by_name()["alpha"], profiles_by_name()["beta"]
    s = summary_of()
    with pytest.raises(ActionError, match="refused"):  # recent files
        actions.move_session(a, b, s, confirm=actions.delete_token(s))
    age_files(tree["alpha"])
    with pytest.raises(ActionError, match="token"):
        actions.move_session(a, b, s, confirm=CONFIRM)
    assert not (tree["beta"] / "file-history" / SID_MAIN).exists()
    res = actions.move_session(a, b, s, confirm=actions.delete_token(s))
    assert res.ok
    assert not session_paths(tree["alpha"])
    assert len(session_paths(tree["beta"])) == len(s.related_files)
    assert audit_actions() == ["session.move"]


def test_resume_command_quotes_paths(tree: dict[str, Path]) -> None:
    a, s = profiles_by_name()["alpha"], summary_of()
    cmd = actions.resume_command(a, s)
    assert cmd == f"cd /work/demo-app && CLAUDE_CONFIG_DIR={tree['alpha']} claude --resume {SID_MAIN}"
    spaced = s.model_copy(update={"cwd": "/my work/app"})
    assert "cd '/my work/app' &&" in actions.resume_command(a, spaced)


def test_launch_requires_configured_launcher() -> None:
    from themis.config import Config

    with pytest.raises(ActionError):
        actions.launch_resume("claude", Config())


def test_bulk_continues_after_refusal(tree: dict[str, Path]) -> None:
    age_files(tree["beta"])
    a, b = profiles_by_name()["alpha"], profiles_by_name()["beta"]
    from fixtures.make_fixtures import SID_OTHER

    items = [(a, summary_of()), (b, summary_of(SID_OTHER, "beta"))]
    out = actions.run_bulk(items, lambda p, s: actions.delete_session(p, s, confirm=actions.delete_token(s)))
    assert [r.ok for _, r in out] == [False, True]  # alpha was too recent, beta deleted
