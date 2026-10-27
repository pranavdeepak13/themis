"""Write paths under a temp THEMIS_PROFILES: plugin/MCP toggles, settings editor, promote, URN stability."""

from __future__ import annotations

import os
from pathlib import Path

import orjson
import pytest

from fixtures.make_fixtures import FAKE_KEY
from themis.config import Config
from themis.paths import audit_log_path
from themis.profiles.discover import discover_profiles
from themis.redact import MASK
from themis.registry.manifest import build_manifest
from themis.registry.search import inspect, search
from themis.registry.urn import make_urn, parse_urn
from themis.resources import settings as st
from themis.resources.model import Resource
from themis.resources.scan import scan_inventory
from themis.resources.symlinks import PromoteError, plan_promote, promote, shared_root
from themis.sessions import meta


def _profile(name: str):
    return next(p for p in discover_profiles() if p.name == name)


def _audit_actions() -> list[str]:
    return [orjson.loads(line)["action"] for line in audit_log_path().read_text().splitlines()]


def test_plugin_toggle_round_trip(tree: dict[str, Path]) -> None:
    alpha = _profile("alpha")
    path = alpha.path / "settings.json"
    before = path.read_bytes()
    dry = st.set_plugin_enabled(alpha, "demo@mk", False)
    assert dry.dry_run and dry.plan and "false" in dry.plan.diff and path.read_bytes() == before
    with pytest.raises(st.SettingsError):
        st.set_plugin_enabled(alpha, "demo@mk", False, confirm="nope", dry_run=False)
    res = st.set_plugin_enabled(alpha, "demo@mk", False, confirm="confirm", dry_run=False)
    assert res.backup and Path(res.backup).read_bytes() == before
    data = orjson.loads(path.read_bytes())
    assert data["enabledPlugins"]["demo@mk"] is False and data["model"] == "sonnet"  # nothing else touched
    assert path.read_bytes().endswith(b"\n")
    st.set_plugin_enabled(alpha, "demo@mk", True, confirm="confirm", dry_run=False)
    assert orjson.loads(path.read_bytes()) == orjson.loads(before)
    assert _audit_actions() == ["plugin.toggle", "plugin.toggle"]


def test_mcp_toggle_uses_disabled_list_and_never_leaks(tree: dict[str, Path]) -> None:
    alpha = _profile("alpha")
    path = alpha.path / ".claude.json"
    dry = st.set_mcp_enabled(alpha, "github", False)
    assert dry.plan and FAKE_KEY not in dry.plan.diff and "disabledMcpServers" in dry.plan.diff
    st.set_mcp_enabled(alpha, "github", False, confirm="confirm", dry_run=False)
    data = orjson.loads(path.read_bytes())
    assert data["disabledMcpServers"] == ["github"] and data["oauthAccount"]["emailAddress"] == "fake@example.com"
    inv = scan_inventory(discover_profiles())
    assert next(r for r in inv.resources if r.kind == "mcp" and r.name == "github").enabled is False
    st.set_mcp_enabled(alpha, "github", True, confirm="confirm", dry_run=False)
    assert orjson.loads(path.read_bytes())["disabledMcpServers"] == []
    with pytest.raises(st.SettingsError):
        st.set_mcp_enabled(alpha, "nonexistent", False)


def test_settings_editor_masks_validates_and_restores_secrets(tree: dict[str, Path]) -> None:
    alpha = _profile("alpha")
    path = alpha.path / "settings.json"
    text = st.settings_text_for_edit(alpha)
    assert FAKE_KEY not in text and MASK in text
    assert st.validate_settings_text("{nope") and st.validate_settings_text("[]")
    assert st.validate_settings_text('{"permissions": {"allow": "x"}}')
    with pytest.raises(st.SettingsError):
        st.edit_settings(alpha, "{broken", confirm="confirm", dry_run=False)
    edited = text.replace('"sonnet"', '"opus"')
    dry = st.edit_settings(alpha, edited)
    assert dry.plan and '-  "model": "sonnet"' in dry.plan.diff and FAKE_KEY not in dry.plan.diff
    res = st.edit_settings(alpha, edited, confirm="confirm", dry_run=False)
    data = orjson.loads(path.read_bytes())
    assert data["model"] == "opus" and data["env"]["SOME_TOKEN"] == FAKE_KEY  # real secret restored
    assert res.backup and os.path.exists(res.backup)
    with pytest.raises(st.SettingsError):  # a mask where no secret existed
        st.edit_settings(alpha, '{"newkey": "' + MASK + '"}', confirm="confirm", dry_run=False)


def test_promote_identical_copy_to_symlink(tree: dict[str, Path]) -> None:
    profiles = discover_profiles()
    inv = scan_inventory(profiles)
    cfg = Config()
    root = shared_root(cfg, inv.resources)
    assert root == tree["alpha"].parent / ".claude-shared"  # detected from existing symlinks
    dry = promote("skill", "copy-skill", profiles, inv.resources, cfg)
    assert dry.ok and dry.dry_run and not (root / "skills" / "copy-skill").exists()
    assert [s.op for s in dry.plan.steps] == ["canonicalize", "replace-copy", "replace-copy"]
    res = promote("skill", "copy-skill", profiles, inv.resources, cfg, confirm="confirm", dry_run=False)
    assert res.ok
    for p in profiles:
        link = p.path / "skills" / "copy-skill"
        assert link.is_symlink() and link.resolve() == (root / "skills" / "copy-skill").resolve()
        assert (link / "SKILL.md").exists()
    backups = list((Path(os.environ["THEMIS_HOME"]) / "backups").glob("promote-*/*/skills/copy-skill/SKILL.md"))
    assert len(backups) == 2  # the replaced copies were moved aside, not deleted
    inv2 = scan_inventory(profiles)
    assert {r.link_state for r in inv2.resources if r.name == "copy-skill"} == {"symlink"}
    assert "resource.promote" in _audit_actions()


def test_promote_refuses_drift_without_choice_then_honours_it(tree: dict[str, Path]) -> None:
    profiles = discover_profiles()
    inv = scan_inventory(profiles)
    cfg = Config()
    plan = plan_promote("skill", "drift-skill", profiles, inv.resources, cfg)
    assert plan.needs_choice and "Alpha body." in plan.diff and plan.choices == ["alpha", "beta"]
    with pytest.raises(PromoteError):
        promote("skill", "drift-skill", profiles, inv.resources, cfg, confirm="confirm", dry_run=False)
    assert not (tree["alpha"] / "skills" / "drift-skill").is_symlink()
    promote("skill", "drift-skill", profiles, inv.resources, cfg, prefer="beta", confirm="confirm", dry_run=False)
    shared = shared_root(cfg, inv.resources) / "skills" / "drift-skill" / "SKILL.md"
    assert "Beta body." in shared.read_text()
    assert (tree["alpha"] / "skills" / "drift-skill").is_symlink()
    backup_text = [p.read_text() for p in (Path(os.environ["THEMIS_HOME"]) / "backups").rglob("SKILL.md")]
    assert any("Alpha body." in t for t in backup_text)  # the losing side survives in the backup


def test_promote_needs_token_and_valid_target(tree: dict[str, Path]) -> None:
    profiles = discover_profiles()
    inv = scan_inventory(profiles)
    with pytest.raises(PromoteError):
        promote("skill", "solo", profiles, inv.resources, Config(), confirm="wrong", dry_run=False)
    assert not (tree["beta"] / "skills" / "solo").exists()
    bad = promote("skill", "plug-skill", profiles, inv.resources, Config())  # plugin skills are not user-level
    assert not bad.ok
    cmd = promote("command", "ship", profiles, inv.resources, Config())
    assert cmd.ok and {s.op for s in cmd.plan.steps} == {"keep"}


def test_resource_tags_preserve_session_meta(tree: dict[str, Path]) -> None:
    meta.set_tags("alpha", "abc", ["x"])
    assert meta.set_resource_tags("skill", "solo", [" Review ", "review", "Core"]) == ["review", "core"]
    m = meta.load_meta()
    assert meta.resource_tags(m, "skill", "solo") == ["review", "core"] and meta.tags_of(m, "alpha", "abc") == ["x"]
    meta.set_resource_tags("skill", "solo", [])
    assert meta.resource_tags(meta.load_meta(), "skill", "solo") == []


def test_urn_follows_ard_grammar_and_is_stable(tree: dict[str, Path]) -> None:
    inv = scan_inventory(discover_profiles())
    a = next(r for r in inv.resources if r.name == "shared-skill" and r.profile == "alpha")
    b = next(r for r in inv.resources if r.name == "shared-skill" and r.profile == "beta")
    urn = make_urn(a)
    assert urn == make_urn(b) == "urn:air:themis.local:skill:user:shared-skill"
    moved = a.model_copy(update={"path": "/elsewhere/SKILL.md", "realpath": "/else", "profile": "zzz"})
    assert make_urn(moved) == urn
    assert make_urn(a, "example.com") == "urn:air:example.com:skill:user:shared-skill"
    assert parse_urn(urn) == {"publisher": "themis.local", "namespace": "skill:user", "name": "shared-skill"}
    plug = next(r for r in inv.resources if r.name == "plug-skill")
    assert make_urn(plug) == "urn:air:themis.local:skill:plugin.demo:plug-skill"
    odd = a.model_copy(update={"name": "anthropic-skills:x y"})
    assert parse_urn(make_urn(odd))["name"] == "anthropic-skills:x y"
    assert make_urn(Resource(kind="hook", name="h", profile="alpha")) is None
    assert parse_urn("urn:air:onlypublisher") is None


def test_manifest_is_ard_shaped_and_clean(tree: dict[str, Path]) -> None:
    inv = scan_inventory(discover_profiles())
    m = build_manifest(inv.resources, meta={"resources": {}})
    by_id = {e["identifier"]: e for e in m["entries"]}
    for e in m["entries"]:  # MUST fields, and exactly one of url / data
        assert {"identifier", "displayName", "type"} <= e.keys() and ("url" in e) != ("data" in e)
    skill = by_id["urn:air:themis.local:skill:user:shared-skill"]
    assert skill["type"] == "application/agent-skills+md" and skill["url"].startswith("file://")
    assert skill["metadata"]["themis"]["profiles"] == ["alpha", "beta"]
    assert skill["metadata"]["themis"]["scope"] == "global"
    drift = by_id["urn:air:themis.local:skill:user:drift-skill"]
    assert len(drift["metadata"]["themis"]["variants"]) == 2
    mcp = by_id["urn:air:themis.local:mcp-server:user:github"]
    assert mcp["type"] == "application/mcp-server-card+json" and "data" in mcp
    assert FAKE_KEY not in orjson.dumps(m).decode()


def test_search_shape_filters_and_paging(tree: dict[str, Path]) -> None:
    inv = scan_inventory(discover_profiles())
    m = build_manifest(inv.resources, meta={"resources": {}})
    resp = search(m, "shared skill", {"type": ["application/agent-skills+md"]})
    assert resp["referrals"] == [] and resp["results"][0]["displayName"] == "shared-skill"
    assert resp["results"][0]["score"] == 100
    assert search(m, "", {"publisher": "themis.local", "type": "application/mcp-server-card+json"})["results"]
    assert not search(m, "", {"publisher": "other.example"})["results"]  # AND across keys, publisher from URN
    both = search(m, "", {"metadata.themis.profiles": ["alpha", "beta"]})["results"]  # OR within a key
    assert both
    assert not search(m, "shared nonexistentword")["results"]
    page1 = search(m, "", page_size=3)
    assert len(page1["results"]) == 3 and page1["pageToken"] == "3"
    page2 = search(m, "", page_size=3, page_token=page1["pageToken"])
    assert {r["identifier"] for r in page1["results"]}.isdisjoint(r["identifier"] for r in page2["results"])
    shared = "urn:air:themis.local:skill:user:shared-skill"
    assert inspect(m, shared)["identifier"] == shared and inspect(m, "urn:air:themis.local:skill:user:none") is None
