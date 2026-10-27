from __future__ import annotations

from pathlib import Path

from fixtures.make_fixtures import FAKE_KEY, SID_EMPTY, SID_MAIN, SID_OTHER
from themis.config import Config
from themis.profiles.discover import discover_profiles
from themis.sessions.cache import load_sessions
from themis.sessions.metrics import context_percent, estimate_cost
from themis.sessions.parser import parse_session


def _main(tree: dict[str, Path]):
    alpha = tree["alpha"]
    return parse_session("alpha", alpha, alpha / "projects" / "-work-demo-app" / f"{SID_MAIN}.jsonl")


def test_discovery_uses_override(tree: dict[str, Path]) -> None:
    assert [p.name for p in discover_profiles()] == ["alpha", "beta"]


def test_usage_counted_once_per_api_message(tree: dict[str, Path]) -> None:
    s = _main(tree)
    # msg_1 appears as 3 lines, msg_2 as 4; usage must be counted once each.
    # main: msg_1 (10,100,1000,500) + msg_2 (20,200,4000,100) + msg_3 (5,50,900,0)
    # sidechain: msg_sub1 (7,70,700,70). <synthetic> is ignored.
    assert s.tokens.input == 10 + 20 + 5 + 7
    assert s.tokens.output == 100 + 200 + 50 + 70
    assert s.tokens.cache_read == 1000 + 4000 + 900 + 700
    assert s.tokens.cache_creation == 500 + 100 + 70
    assert s.subagent_tokens.total == 7 + 70 + 700 + 70
    assert s.assistant_messages == 5  # msg_1, msg_2, msg_3, msg_err, msg_sub1


def test_context_is_last_main_chain_message_not_sidechain(tree: dict[str, Path]) -> None:
    s = _main(tree)
    assert s.context_tokens == 5 + 900  # msg_3; the later subagent timestamp is irrelevant
    assert s.context_peak == 20 + 4000 + 100
    assert s.last_model == "claude-opus-5-5"
    cfg = Config()
    assert context_percent(s, cfg) == round(100 * 905 / 200_000, 1)


def test_extended_window_inferred_when_exceeded(tree: dict[str, Path]) -> None:
    s = _main(tree)
    s.context_tokens = s.context_peak = 500_000
    assert context_percent(s, Config()) == 50.0


def test_tools_skills_mcp_subagents(tree: dict[str, Path]) -> None:
    s = _main(tree)
    assert s.tool_calls["Write"] == 2
    assert s.skills == {"tdd": 1}
    assert s.mcp_tools == {"github/list_prs": 1}
    assert s.mcp_servers == {"github": 1}
    assert s.subagent_types == {"Explore": 1}
    assert s.subagent_count == 1
    assert s.commands == {"/init": 1}
    assert s.compactions == 1


def test_memory_writes_only_memory_targets(tree: dict[str, Path]) -> None:
    s = _main(tree)
    assert [m.path for m in s.memory_writes] == [
        "/work/demo-app/CLAUDE.md",
        "/home/u/.claude/projects/p/memory/note.md",
    ]
    assert all(m.timestamp for m in s.memory_writes)


def test_topic_priority_and_redaction(tree: dict[str, Path]) -> None:
    assert _main(tree).topic == "Fix login bug"  # ai-title wins over prompts and commands
    beta = tree["beta"]
    other = parse_session("beta", beta, beta / "projects" / "-work-demo-app" / f"{SID_OTHER}.jsonl")
    assert other.topic == "What does this repo do?"  # system-reminder skipped, tags stripped
    assert other.topic_source == "first-prompt"


def test_secret_in_prompt_never_reaches_summary(tree: dict[str, Path]) -> None:
    alpha = tree["alpha"]
    path = alpha / "projects" / "-work-demo-app" / f"{SID_MAIN}.jsonl"
    # without the ai-title the topic would come from the prompt containing the key
    text = b"\n".join(ln for ln in path.read_bytes().splitlines() if b"ai-title" not in ln)
    path.write_bytes(text + b"\n")
    s = parse_session("alpha", alpha, path)
    assert s.topic_source == "first-prompt"  # a real prompt beats a slash command
    assert s.topic is not None and "Please fix the login bug" in s.topic
    assert FAKE_KEY not in s.model_dump_json()


def test_unparseable_lines_counted_not_fatal(tree: dict[str, Path]) -> None:
    assert _main(tree).parse_errors == 1


def test_empty_session_is_zero_not_error(tree: dict[str, Path]) -> None:
    alpha = tree["alpha"]
    s = parse_session("alpha", alpha, alpha / "projects" / "-work-demo-app" / f"{SID_EMPTY}.jsonl")
    assert s.tokens.total == 0 and s.topic is None and s.context_tokens == 0


def test_related_files_and_disk_size(tree: dict[str, Path]) -> None:
    s = _main(tree)
    kinds = sorted(f.kind for f in s.related_files)
    assert kinds == ["file-history", "subagent", "subagent", "transcript"]
    assert s.disk_bytes == sum(f.size for f in s.related_files)
    assert all(not f.path.startswith("/") for f in s.related_files)


def test_cost_estimate_uses_split_cache_rates(tree: dict[str, Path]) -> None:
    cost, incomplete = estimate_cost(_main(tree), Config())
    assert cost > 0 and not incomplete


def test_load_sessions_all_profiles(tree: dict[str, Path]) -> None:
    got = load_sessions(discover_profiles())
    assert sorted((s.profile, s.session_id[:2]) for s in got) == [
        ("alpha", "11"),
        ("alpha", "22"),
        ("beta", "33"),
    ]
