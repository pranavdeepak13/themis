"""Scanners, scope classification, drift, usage, doctor, redaction of MCP config."""

from __future__ import annotations

from pathlib import Path

import orjson

from fixtures.make_fixtures import FAKE_KEY, SID_MAIN
from themis.profiles.discover import discover_profiles
from themis.resources.doctor import run_doctor
from themis.resources.drift import diff_resources, drift_peers
from themis.resources.fsutil import estimate_tokens, parse_frontmatter
from themis.resources.hooks import script_path
from themis.resources.mcp_health import parse_mcp_list
from themis.resources.model import Resource
from themis.resources.reader import read_body
from themis.resources.scan import scan_inventory
from themis.resources.scopes import classify, matrix, scope_name
from themis.resources.usage import UsageIndex
from themis.sessions.cache import load_sessions


def _inv():
    profiles = discover_profiles()
    return profiles, scan_inventory(profiles)


def _find(inv, kind: str, name: str, profile: str, origin: str = "user"):
    return next(r for r in inv.resources if (r.kind, r.name, r.profile, r.origin) == (kind, name, profile, origin))


def test_frontmatter_and_token_estimate() -> None:
    fm, body, ok = parse_frontmatter('---\nname: x\ndescription: >\n  folded\n  text\nmodel: "m"\n---\nbody\n')
    assert ok and fm == {"name": "x", "description": "folded text", "model": "m"} and body == "body"
    assert parse_frontmatter("no frontmatter")[2] is False
    assert estimate_tokens("a" * 40) == 10


def test_symlink_copy_drift_and_scope(tree: dict[str, Path]) -> None:
    _profiles, inv = _inv()
    a, b = _find(inv, "skill", "shared-skill", "alpha"), _find(inv, "skill", "shared-skill", "beta")
    assert a.is_symlink and a.link_state == "symlink" and a.realpath == b.realpath
    assert a.scope == "global" and not a.drifted  # linked into all (two) profiles
    ca, cb = _find(inv, "skill", "copy-skill", "alpha"), _find(inv, "skill", "copy-skill", "beta")
    assert ca.content_hash == cb.content_hash and ca.realpath != cb.realpath
    assert ca.link_state == "copy" and ca.identical_copy and ca.scope == "global" and not ca.drifted
    da, db = _find(inv, "skill", "drift-skill", "alpha"), _find(inv, "skill", "drift-skill", "beta")
    assert da.drifted and db.drifted and da.scope == "profile-only"
    assert _find(inv, "skill", "solo", "alpha").scope == "profile-only"
    diff = diff_resources(da, db)
    assert "-Alpha body." in diff and "+Beta body." in diff
    assert drift_peers(da, inv.resources) == [db]


def test_scope_names_with_three_profiles() -> None:
    assert [scope_name(n, 3) for n in (1, 2, 3)] == ["profile-only", "shared", "global"]
    mk = lambda p: Resource(kind="skill", name="s", profile=p, content_hash="h", realpath=f"/{p}/s")  # noqa: E731
    rs = [mk("a"), mk("b")]
    classify(rs, 3)
    assert {r.scope for r in rs} == {"shared"} and all(r.link_state == "copy" for r in rs)


def test_broken_link_and_missing_frontmatter(tree: dict[str, Path]) -> None:
    _profiles, inv = _inv()
    assert _find(inv, "skill", "ghost", "alpha").broken
    assert not _find(inv, "skill", "nofm", "alpha").frontmatter_ok


def test_plugin_contents_and_enabled_state(tree: dict[str, Path]) -> None:
    _profiles, inv = _inv()
    plug = _find(inv, "plugin", "demo@mk", "alpha")
    assert plug.enabled is True and plug.extra["install_exists"] and plug.extra["marketplace_known"]
    assert plug.extra["skills"] == 1
    ps = _find(inv, "skill", "plug-skill", "alpha", "plugin")
    assert ps.origin_name == "demo@mk" and ps.scope == "plugin" and ps.enabled is True
    assert _find(inv, "command", "plug-cmd", "alpha", "plugin")
    assert _find(inv, "mcp", "plug-mcp", "alpha", "plugin")


def test_mcp_config_is_redacted_and_only_needed_keys_read(tree: dict[str, Path]) -> None:
    _profiles, inv = _inv()
    gh = _find(inv, "mcp", "github", "alpha")
    dumped = gh.model_dump_json()
    assert FAKE_KEY not in dumped and "fake@example.com" not in dumped
    assert gh.extra["config"]["env"] == {"GITHUB_TOKEN": "***REDACTED***"}
    docs = _find(inv, "mcp", "docs", "alpha")
    assert FAKE_KEY not in docs.model_dump_json() and docs.extra["transport"] == "http"
    assert FAKE_KEY not in read_body(gh)
    for r in inv.resources:
        assert FAKE_KEY not in r.model_dump_json()


def test_hooks_and_permissions(tree: dict[str, Path]) -> None:
    _profiles, inv = _inv()
    hooks = [r for r in inv.resources if r.kind == "hook" and r.profile == "alpha"]
    user = next(h for h in hooks if h.origin == "user")
    assert user.extra["event"] == "Stop" and user.extra["script_missing"]
    plug = next(h for h in hooks if h.origin == "plugin")
    assert plug.extra["script_missing"] and "nope.sh" in plug.extra["script"]
    assert any(r.kind == "permission" and r.name == "Bash(ls:*)" for r in inv.resources)
    assert script_path("$CLAUDE_PROJECT_DIR/x.sh") is None  # not checkable without a project


def test_doctor_findings(tree: dict[str, Path]) -> None:
    profiles, inv = _inv()
    sessions = load_sessions(profiles, rebuild=True)
    codes = {(f.code, f.profile) for f in run_doctor(inv, profiles, sessions)}
    assert ("broken-symlink", "alpha") in codes
    assert ("bad-frontmatter", "alpha") in codes
    assert ("hook-script-missing", "alpha") in codes
    assert ("plugin-marketplace-missing", "alpha") in codes  # lost@gone
    assert ("plugin-not-installed", "alpha") not in codes  # it is installed (files are missing instead)
    assert ("plugin-files-missing", "alpha") in codes
    assert ("orphan-project", "alpha") in codes  # fixture cwd /work/demo-app does not exist


def test_usage_join(tree: dict[str, Path]) -> None:
    profiles = discover_profiles()
    sessions = load_sessions(profiles, rebuild=True)
    usage = UsageIndex(sessions)
    st = usage.lookup("skill", "tdd")
    assert st.invocations == 1 and st.per_profile == {"alpha": 1} and st.last_used
    assert usage.lookup("command", "init").invocations == 1
    assert usage.lookup("agent", "Explore").invocations == 1
    assert usage.lookup("mcp", "github").invocations == 1
    assert usage.lookup("skill", "nothing").invocations == 0
    assert SID_MAIN


def test_matrix_rows(tree: dict[str, Path]) -> None:
    profiles, inv = _inv()
    rows = {(r["kind"], r["name"]): r for r in matrix(inv.resources, [p.name for p in profiles])}
    assert rows[("skill", "shared-skill")]["alpha"] == "symlink"
    assert rows[("skill", "drift-skill")]["beta"] == "drift"
    assert "beta" not in rows[("skill", "solo")]


def test_parse_mcp_list_handles_colons_and_states() -> None:
    text = (
        "Checking MCP server health…\n\n"
        "claude.ai Docs: https://x.invalid/mcp - ✔ Connected\n"
        "plugin:a:b: cmd --flag  - ✗ Failed to connect\n"
        "other: https://y.invalid - ! Needs authentication\n"
    )
    got = {h.name: h.status for h in parse_mcp_list(text)}
    assert got == {"claude.ai Docs": "connected", "plugin:a:b": "failed", "other": "needs-auth"}


def test_resource_json_roundtrips(tree: dict[str, Path]) -> None:
    _profiles, inv = _inv()
    assert orjson.loads(inv.model_dump_json())["profiles"] == ["alpha", "beta"]
