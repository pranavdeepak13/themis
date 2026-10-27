"""Analytics, advisor rules, prompts and reports against the synthetic tree."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest

from fixtures.make_fixtures import FAKE_KEY, SID_MAIN, SID_OTHER, _skill
from themis import analytics
from themis.advisor.rules import run_advisor
from themis.config import load_config
from themis.redact import redact_secrets, redact_text
from themis.reports.build import build_report, render
from themis.resources.store import ResourceData, load_all
from themis.sessions.model import SessionSummary

NOW = datetime(2026, 1, 5, tzinfo=UTC)  # the fixture sessions are dated 2026-01-01..03


def _snapshot(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def _data(tree: dict[str, Path]) -> ResourceData:
    return load_all()


def _by_rule(findings, rule: str):
    return [f for f in findings if f.rule == rule]


# ------------------------------------------------------------------ analytics


def test_parse_since_and_window() -> None:
    assert analytics.parse_since("30d", NOW).date().isoformat() == "2025-12-06"
    assert analytics.parse_since("2w", NOW).date().isoformat() == "2025-12-22"
    assert analytics.parse_since("2026-01-02", NOW).date().isoformat() == "2026-01-02"
    with pytest.raises(ValueError):
        analytics.parse_since("soon", NOW)


def test_tokens_by_dimensions(tree: dict[str, Path]) -> None:
    d = _data(tree)
    cfg = d.cfg
    by_profile = {b.label: b for b in analytics.tokens_by(d.sessions, "profile", cfg)}
    assert by_profile["alpha"].sessions == 2 and by_profile["beta"].total == 10
    days = [b.label for b in analytics.tokens_by(d.sessions, "day", cfg)]
    assert days == sorted(days) and "2026-01-01" in days
    by_model = {b.label: b for b in analytics.tokens_by(d.sessions, "model", cfg)}
    assert {"sonnet-5-5", "opus-5-5", "haiku-5-5"} <= by_model.keys() and by_model["opus-5-5"].cost > 0
    assert analytics.tokens_by(d.sessions, "month", cfg)[0].label == "2026-01"
    assert analytics.tokens_by(d.sessions, "week", cfg)[0].label.startswith("2026-W")
    assert analytics.tokens_by([], "day", cfg) == []
    assert 0 < (analytics.cache_hit_rate(d.sessions) or 0) < 1


def test_context_stats_top_and_usage(tree: dict[str, Path]) -> None:
    d = _data(tree)
    stats = analytics.context_stats(d.sessions, d.cfg)
    assert sum(n for _, n in stats.histogram) == stats.sessions == 2 and stats.compacted == 1
    assert [s.session_id for s in analytics.top_sessions(d.sessions, "tokens", d.cfg, 1)] == [SID_MAIN]
    assert analytics.top_tools(d.sessions)[0][0] in {"Write", "Skill", "mcp__github__list_prs", "Edit", "Agent"}
    assert analytics.usage_by_profile(d.sessions, "skills") == {"tdd": {"alpha": 1}}
    assert analytics.usage_by_profile(d.sessions, "mcp") == {"github": {"alpha": 1}}
    assert analytics.bar(5, 10, 10) == "█████" and analytics.bar(0, 10) == "" and analytics.bar(1, 0) == ""


# ------------------------------------------------------------------ advisor rules


def test_advisor_never_edits_anything(tree: dict[str, Path]) -> None:
    before = _snapshot(tree["alpha"].parent)
    d = _data(tree)
    run_advisor(d, NOW)
    assert _snapshot(tree["alpha"].parent) == before


def test_unused_skills_and_startup_overhead(tree: dict[str, Path]) -> None:
    for i in range(6):
        _skill(tree["alpha"] / "skills" / f"big-{i}", f"big-{i}", "x" * 280 + f" unique{i}")
    d = _data(tree)
    d.cfg.advisor.startup_overhead_warn_tokens = 100
    found = run_advisor(d, NOW)
    unused = [f for f in _by_rule(found, "unused-skills") if f.profiles == ["alpha"]]
    assert unused, "six large unused skills should be flagged"
    f = unused[0]
    assert f.tokens_per_session >= 400 and f.tokens_per_month == f.tokens_per_session * 2  # two alpha sessions
    assert f.confidence == "low" and any("big-" in e for e in f.evidence)
    assert not any("tdd" in e for e in f.evidence)  # a skill that was used is not listed
    overhead = [x for x in _by_rule(found, "startup-overhead") if x.profiles == ["alpha"]][0]
    assert overhead.severity == "medium" and any("Largest single contributors" in e for e in overhead.evidence)


def test_no_findings_without_sessions_in_window(tree: dict[str, Path]) -> None:
    for i in range(6):
        _skill(tree["alpha"] / "skills" / f"big-{i}", f"big-{i}", "x" * 280 + f" u{i}")
    d = _data(tree)
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    assert not _by_rule(run_advisor(d, far_future), "unused-skills")  # absence of use proves nothing


def test_skill_overrides_off_hides_skill_from_advice(tree: dict[str, Path]) -> None:
    for i in range(6):
        _skill(tree["alpha"] / "skills" / f"big-{i}", f"big-{i}", "x" * 280 + f" u{i}")
    settings = tree["alpha"] / "settings.json"
    obj = orjson.loads(settings.read_bytes())
    obj["skillOverrides"] = {f"big-{i}": "off" for i in range(6)}
    settings.write_bytes(orjson.dumps(obj))
    found = [f for f in _by_rule(run_advisor(_data(tree), NOW), "unused-skills") if f.profiles == ["alpha"]]
    assert not found


def test_unused_mcp_and_used_mcp(tree: dict[str, Path]) -> None:
    found = _by_rule(run_advisor(_data(tree), NOW), "unused-mcp")
    alpha = [f for f in found if f.profiles == ["alpha"]][0]
    assert any("docs" in e for e in alpha.evidence) and not any(e.startswith("github") for e in alpha.evidence)
    assert alpha.tokens_per_session is None and any("not measurable" in e for e in alpha.evidence)


def test_duplicates_drift_and_descriptions(tree: dict[str, Path]) -> None:
    _skill(
        tree["alpha"] / "skills" / "clone-a", "clone-a", "Review pull requests carefully for security and style issues"
    )
    _skill(
        tree["beta"] / "skills" / "clone-b", "clone-b", "Review pull requests carefully for security and style issues"
    )
    _skill(tree["alpha"] / "skills" / "wordy", "wordy", "word " * 150 + "end")
    d = _data(tree)
    d.cfg.advisor.skill_desc_max_chars = 200
    found = run_advisor(d, NOW)
    copies = _by_rule(found, "identical-copies")[0]
    assert any("copy-skill" in e for e in copies.evidence) and not any("shared-skill" in e for e in copies.evidence)
    assert any("clone-a" in e and "clone-b" in e for e in _by_rule(found, "near-duplicate")[0].evidence)
    drift = _by_rule(found, "drift")[0]
    assert "drift-skill" in drift.title and set(drift.profiles) == {"alpha", "beta"}
    assert any("wordy" in e for e in _by_rule(found, "long-description")[0].evidence)


def test_overlap_rule_uses_its_own_band(tree: dict[str, Path]) -> None:
    _skill(tree["alpha"] / "skills" / "ov-a", "ov-a", "Create database migrations schema changes for postgres projects")
    _skill(tree["alpha"] / "skills" / "ov-b", "ov-b", "Create database migrations schema changes for mysql projects")
    found = _by_rule(run_advisor(_data(tree), NOW), "overlap")
    assert found and any("ov-a ~ ov-b" in e for e in found[0].evidence)


def test_claude_md_size_and_repetition(tree: dict[str, Path]) -> None:
    para = "Always run the full test suite before you claim that a change is finished and ready to merge."
    (tree["alpha"] / "CLAUDE.md").write_text(f"# Rules\n\n{para}\n")
    (tree["beta"] / "CLAUDE.md").write_text(f"# Other\n\n{para}\n")
    d = _data(tree)
    d.cfg.advisor.claude_md_max_tokens = 5
    found = _by_rule(run_advisor(d, NOW), "claude-md")
    assert any("is large" in f.title for f in found)
    rep = next(f for f in found if "repeated" in f.title)
    assert set(rep.profiles) == {"alpha", "beta"} and rep.tokens_per_session > 0


def test_high_context_and_low_cache(tree: dict[str, Path]) -> None:
    d = _data(tree)
    d.cfg.context_windows["default"] = 100  # beta's 8-token context becomes 8% of a tiny window
    d.cfg.advisor.high_context_percent = 5
    d.cfg.advisor.min_cache_tokens = 1
    d.cfg.advisor.low_cache_hit_rate = 0.9
    found = run_advisor(d, NOW)
    hot = _by_rule(found, "high-context")
    assert [f.profiles for f in hot] == [["beta"]]  # alpha's main session compacted
    assert SID_OTHER[:8] in " ".join(hot[0].evidence)
    cache = _by_rule(found, "low-cache")
    assert (
        cache
        and all(f.confidence == "low" for f in cache)
        and any("Patterns seen" in e for f in cache for e in f.evidence)
    )


# ------------------------------------------------------------------ prompts


def test_prompts_are_self_contained_and_clean(tree: dict[str, Path]) -> None:
    (tree["alpha"] / "CLAUDE.md").write_text("word " * 3000)
    d = _data(tree)
    d.cfg.advisor.claude_md_max_tokens = 100
    found = run_advisor(d, NOW)
    assert found
    for f in found:
        p = f.prompt
        assert FAKE_KEY not in p
        for name in f.profiles:
            assert f"CLAUDE_CONFIG_DIR={tree[name]}" in p
        assert "diff" in p and "confirmation" in p and "~/.claude*" in p
        assert "Goal:" in p and "Evidence from Themis" in p
        if f.files:
            assert f.files[0] in p
    big = next(f for f in found if "is large" in f.title)
    assert str(tree["alpha"] / "CLAUDE.md") in big.prompt


def test_finding_ids_are_stable(tree: dict[str, Path]) -> None:
    a = {f.id for f in run_advisor(_data(tree), NOW)}
    b = {f.id for f in run_advisor(_data(tree), NOW)}
    assert a == b and all(len(i) == 10 for i in a)


# ------------------------------------------------------------------ reports


def test_report_has_no_transcript_content_and_no_secrets(tree: dict[str, Path]) -> None:
    d = _data(tree)
    assert any(s.topic for s in d.sessions)  # topics exist in the data...
    rep = build_report(d, None, "30d", now=NOW)
    for fmt in ("md", "html"):
        text = render(rep, fmt)
        assert "Fix login bug" not in text and "login bug" not in text  # ...but never appear in a report
        assert "What does" not in text and FAKE_KEY not in text
        assert "Usage totals" in text and "Advisor findings" in text and "Doctor findings" in text
        assert "Tokens:" in text or "Tokens: " in text.replace("</li>", "")
    assert "<script" not in render(rep, "html") and "http" not in render(rep, "html").split("</style>")[1][:50]


def test_report_since_filters_and_profile_scope(tree: dict[str, Path]) -> None:
    d = _data(tree)
    all_time = build_report(d, None, None, now=NOW)
    recent = build_report(d, None, "3d", now=NOW)  # 2026-01-02 onward
    assert all_time.sessions == 3 and recent.sessions == 2
    only_beta = build_report(d, "beta", None, now=NOW)
    assert only_beta.sessions == 1 and {t.profile for t in only_beta.top} == {"beta"}
    assert all(f.profiles == ["beta"] or "beta" in f.profiles for f in only_beta.advice)


# ------------------------------------------------------------------ redaction split


def test_redact_secrets_keeps_prose_but_redact_text_masks_assignments() -> None:
    assert redact_secrets("Tokens: 2.2B (input 5k)") == "Tokens: 2.2B (input 5k)"
    assert FAKE_KEY not in redact_secrets(f"key {FAKE_KEY}")
    assert "***REDACTED***" in redact_text("API_TOKEN=abcd1234efgh")
    assert "abcd1234efgh" not in redact_text("API_TOKEN=abcd1234efgh")


def test_config_advisor_overrides_from_toml(tree: dict[str, Path]) -> None:
    from themis.paths import config_path

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        "[advisor]\nunused_days = 14\nclaude_md_max_tokens = 99\nbogus = 1\nlow_cache_hit_rate = 0.25\n"
    )
    cfg = load_config()
    assert cfg.advisor.unused_days == 14 and cfg.advisor.claude_md_max_tokens == 99
    assert cfg.advisor.low_cache_hit_rate == 0.25 and cfg.advisor.near_duplicate == 0.85


def test_session_summary_type_still_used() -> None:
    assert SessionSummary.model_fields["profile"]
