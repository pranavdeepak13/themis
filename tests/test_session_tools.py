from __future__ import annotations

import os
import time
from pathlib import Path

import orjson

from fixtures.make_fixtures import FAKE_KEY, SID_MAIN, SID_OTHER
from helpers import profiles_by_name, summary_of
from themis.config import Config
from themis.paths import audit_log_path
from themis.sessions import meta, query, retention, search
from themis.sessions.actions import CONFIRM
from themis.sessions.cache import load_sessions
from themis.sessions.export import export_markdown, export_session
from themis.sessions.timeline import build_timeline


def test_timeline_merges_blocks_of_one_api_message(tree: dict[str, Path]) -> None:
    prof, s = profiles_by_name()["alpha"], summary_of()
    items = build_timeline(prof.path / s.transcript)
    roles = [i.role for i in items]
    assert roles.count("assistant") == 3 and roles.count("tool_result") == 1  # msg_1..3, not one row per block
    first = next(i for i in items if i.role == "assistant")
    assert first.tokens_in == 10 + 1000 + 500 and first.tokens_out == 100 and first.tools == 2
    assert all(FAKE_KEY not in i.preview for i in items)
    assert [i.index for i in items] == list(range(1, len(items) + 1))


def test_search_finds_and_redacts(tree: dict[str, Path]) -> None:
    profiles = profiles_by_name()
    sessions = load_sessions(list(profiles.values()))
    hits = []
    n = search.search_sessions(profiles, sessions, "LOGIN bug", lambda: False, hits.append)
    assert n == 1 and hits[0].session_id == SID_MAIN and hits[0].role == "user"
    assert FAKE_KEY not in hits[0].snippet and "login bug" in hits[0].snippet.lower()
    assert search.search_sessions(profiles, sessions, FAKE_KEY, lambda: False, hits.append) == 0  # secrets never match


def test_search_can_be_cancelled_and_handles_empty(tree: dict[str, Path]) -> None:
    profiles = profiles_by_name()
    sessions = load_sessions(list(profiles.values()))
    assert search.search_sessions(profiles, sessions, "repo", lambda: True, lambda h: None) == 0
    assert search.search_sessions(profiles, sessions, "   ", lambda: False, lambda h: None) == 0
    hits = []
    search.search_sessions(profiles, sessions, "repo do", lambda: False, hits.append)
    assert [h.session_id for h in hits] == [SID_OTHER]


def test_export_markdown_is_redacted_and_tool_output_is_optional(tree: dict[str, Path]) -> None:
    prof, s = profiles_by_name()["alpha"], summary_of()
    plain = export_markdown(prof, s)
    assert FAKE_KEY not in plain and "login bug" in plain and "**Tool call** `Skill`" in plain
    assert "Tool output" not in plain
    assert "Tool output" in export_markdown(prof, s, include_tool_output=True)
    before = set(tree["alpha"].rglob("*.md"))
    res = export_session(prof, s, confirm=CONFIRM)
    assert Path(res.path or "").read_text() == plain
    assert set(tree["alpha"].rglob("*.md")) == before  # nothing written under the profile


def test_meta_rename_and_tags(tree: dict[str, Path]) -> None:
    meta.set_title("alpha", SID_MAIN, "  My   title ")
    assert meta.set_tags("alpha", SID_MAIN, ["Work", "work", " Bug "]) == ["work", "bug"]
    m = meta.load_meta()
    assert meta.title_of(m, "alpha", SID_MAIN) == "My title" and meta.tags_of(m, "alpha", SID_MAIN) == ["work", "bug"]
    assert meta.bulk_tag([("alpha", SID_MAIN), ("beta", SID_OTHER)], add=["x"], remove=["bug"]) == 2
    m = meta.load_meta()
    assert meta.tags_of(m, "alpha", SID_MAIN) == ["work", "x"] and meta.tags_of(m, "beta", SID_OTHER) == ["x"]
    meta.set_title("alpha", SID_MAIN, "")
    assert meta.title_of(meta.load_meta(), "alpha", SID_MAIN) is None
    assert len(audit_log_path().read_bytes().splitlines()) == 4


def test_meta_preserves_unknown_keys_and_survives_garbage(tree: dict[str, Path]) -> None:
    from themis.paths import meta_path

    meta_path().parent.mkdir(parents=True, exist_ok=True)
    meta_path().write_bytes(orjson.dumps({"resources": {"x": 1}}))
    meta.set_tags("alpha", SID_MAIN, ["a"])
    assert orjson.loads(meta_path().read_bytes())["resources"] == {"x": 1}
    meta_path().write_bytes(b"{{not json")
    assert meta.load_meta()["sessions"] == {}


def test_transcripts_are_never_modified(tree: dict[str, Path]) -> None:
    before = {p: p.read_bytes() for p in tree["alpha"].rglob("*.jsonl")}
    prof, s = profiles_by_name()["alpha"], summary_of()
    export_session(prof, s, confirm=CONFIRM)
    meta.set_tags("alpha", SID_MAIN, ["a"])
    build_timeline(prof.path / s.transcript)
    assert {p: p.read_bytes() for p in tree["alpha"].rglob("*.jsonl")} == before


def test_retention_flags_old_sessions(tree: dict[str, Path]) -> None:
    profs = list(profiles_by_name().values())
    sessions = load_sessions(profs, rebuild=True)
    risky, policy = retention.find_at_risk(profs, sessions, warn_days=7)
    assert risky == [] and policy["alpha"].days == 30 and not policy["alpha"].explicit
    (tree["alpha"] / "settings.json").write_bytes(orjson.dumps({"cleanupPeriodDays": 10}))
    old = time.time() - 5 * 86400
    transcript = tree["alpha"] / summary_of().transcript
    os.utime(transcript, (old, old))
    risky, policy = retention.find_at_risk(profs, load_sessions(profs), warn_days=7)
    assert policy["alpha"].days == 10 and policy["alpha"].explicit
    assert [r.summary.session_id for r in risky] == [SID_MAIN] and 4.5 < risky[0].days_left < 5.5
    os.utime(transcript, (time.time() - 12 * 86400,) * 2)
    risky, _ = retention.find_at_risk(profs, load_sessions(profs), warn_days=0)
    assert risky and risky[0].days_left < 0


def test_retention_ignores_invalid_setting_and_desktop_sessions(tree: dict[str, Path]) -> None:
    profs = list(profiles_by_name().values())
    (tree["alpha"] / "settings.json").write_bytes(orjson.dumps({"cleanupPeriodDays": 0}))
    assert retention.cleanup_days(profs[0]).days == 30
    sessions = load_sessions(profs, rebuild=True)
    sessions = [s.model_copy(update={"entrypoint": "claude-desktop"}) for s in sessions]
    old = time.time() - 40 * 86400
    for p in tree["alpha"].rglob("*.jsonl"):
        os.utime(p, (old, old))
    assert retention.find_at_risk(profs, sessions, warn_days=7)[0] == []


def test_filters_and_sorting(tree: dict[str, Path]) -> None:
    sessions = load_sessions(list(profiles_by_name().values()), rebuild=True)
    meta.set_tags("alpha", SID_MAIN, ["urgent"])
    rows = query.build_rows(sessions, Config(), meta.load_meta())
    assert len(rows) == 3
    F = query.Filters
    assert {r.summary.profile for r in query.apply_filters(rows, F(profile="beta"))} == {"beta"}
    assert [r.summary.session_id for r in query.apply_filters(rows, F(tag="urg"))] == [SID_MAIN]
    assert [r.summary.session_id for r in query.apply_filters(rows, F(text="fix LOGIN"))] == [SID_MAIN]
    assert len(query.apply_filters(rows, F(model="opus"))) == 1
    assert len(query.apply_filters(rows, F(project="demo"))) == 3
    since = query.parse_date("2026-01-02")
    assert {r.summary.session_id for r in query.apply_filters(rows, F(since=since))} != {SID_MAIN}
    assert query.parse_date("not a date") is None
    top = query.sort_rows(rows, "tokens", True)
    assert top[0].summary.session_id == SID_MAIN
    assert query.sort_rows(rows, "last", False)[0].summary.session_id == SID_MAIN
