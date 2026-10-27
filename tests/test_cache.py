from __future__ import annotations

from pathlib import Path

from fixtures.make_fixtures import SID_MAIN
from themis.paths import cache_dir
from themis.profiles.discover import discover_profiles
from themis.sessions import cache as cache_mod
from themis.sessions.cache import load_sessions


def _count_parses(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = cache_mod.parse_session

    def spy(name, pdir, path):
        calls.append(path.stem)
        return real(name, pdir, path)

    monkeypatch.setattr(cache_mod, "parse_session", spy)
    return calls


def test_warm_scan_does_not_reparse(tree, monkeypatch) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    calls = _count_parses(monkeypatch)
    load_sessions(profiles)
    assert calls == []


def test_rebuild_reparses_everything(tree, monkeypatch) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    calls = _count_parses(monkeypatch)
    load_sessions(profiles, rebuild=True)
    assert len(calls) == 3


def test_changed_transcript_invalidates_only_that_session(tree: dict[str, Path], monkeypatch) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    path = tree["alpha"] / "projects" / "-work-demo-app" / f"{SID_MAIN}.jsonl"
    path.write_bytes(path.read_bytes() + b'{"type":"mode","mode":"x"}\n')
    calls = _count_parses(monkeypatch)
    load_sessions(profiles)
    assert calls == [SID_MAIN]


def test_new_subagent_file_invalidates(tree: dict[str, Path], monkeypatch) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    (tree["alpha"] / "projects" / "-work-demo-app" / SID_MAIN / "subagents" / "agent-new.jsonl").write_bytes(b"\n")
    calls = _count_parses(monkeypatch)
    load_sessions(profiles)
    assert calls == [SID_MAIN]


def test_schema_version_bump_invalidates(tree, monkeypatch) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    monkeypatch.setattr(cache_mod, "SCHEMA_VERSION", 999)
    calls = _count_parses(monkeypatch)
    load_sessions(profiles)
    assert len(calls) == 3


def test_corrupt_cache_entry_is_reparsed(tree, monkeypatch) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    (cache_dir() / "alpha" / f"{SID_MAIN}.json").write_bytes(b"garbage")
    calls = _count_parses(monkeypatch)
    load_sessions(profiles)
    assert calls == [SID_MAIN]


def test_deleted_session_cache_is_pruned_and_profile_dir_untouched(tree: dict[str, Path]) -> None:
    profiles = discover_profiles()
    load_sessions(profiles)
    (tree["beta"] / "projects" / "-work-demo-app").joinpath("33333333-aaaa-4bbb-8ccc-000000000003.jsonl").unlink()
    got = load_sessions(profiles)
    assert len(got) == 2
    assert not list((cache_dir() / "beta").glob("*.json"))


def test_process_pool_path(tree, monkeypatch) -> None:
    monkeypatch.setattr(cache_mod, "POOL_THRESHOLD", 1)
    got = load_sessions(discover_profiles(), rebuild=True)
    assert len(got) == 3
