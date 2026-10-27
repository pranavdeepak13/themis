"""End-to-end smoke test: every write action in one run, inside a temp copy of the profiles.

Nothing here may touch a real ~/.claude* directory; the last assertions check the audit log for that.
"""

from __future__ import annotations

from pathlib import Path

import orjson
from typer.testing import CliRunner

from fixtures.make_fixtures import FAKE_KEY, SID_MAIN
from helpers import age_files, profiles_by_name, summary_of
from themis.cli import app
from themis.config import Config
from themis.paths import audit_log_path, themis_home
from themis.registry.manifest import build_manifest
from themis.reports.build import build_report, write_report
from themis.resources import settings as st
from themis.resources.scan import scan_inventory
from themis.resources.store import load_all
from themis.resources.symlinks import promote
from themis.sessions import actions, meta
from themis.sessions.actions import CONFIRM, delete_token
from themis.sessions.cache import load_sessions
from themis.sessions.export import export_session

runner = CliRunner()


def test_every_write_action_in_a_temp_copy(tree: dict[str, Path]) -> None:
    real_home = Path.home()
    alpha, beta = profiles_by_name()["alpha"], profiles_by_name()["beta"]
    age_files(tree["alpha"])
    age_files(tree["beta"])

    # ---- sessions: rename, tag, export, archive, delete, restore, copy, move
    s = summary_of()
    meta.set_title("alpha", s.session_id, "Smoke test")
    meta.set_tags("alpha", s.session_id, ["smoke"])
    assert export_session(alpha, s, confirm=CONFIRM).ok
    assert actions.archive_session(alpha, s, confirm=CONFIRM).ok
    assert actions.delete_session(alpha, s, confirm=delete_token(s)).ok
    assert not any(SID_MAIN in str(p) for p in tree["alpha"].rglob("*") if p.is_file())
    assert actions.restore_session(alpha, SID_MAIN, confirm=CONFIRM).ok
    age_files(tree["alpha"])
    assert actions.copy_session(alpha, beta, summary_of(), confirm=CONFIRM).ok
    assert any(SID_MAIN in str(p) for p in tree["beta"].rglob("*") if p.is_file())
    s = next(x for x in load_sessions([alpha, beta], rebuild=True) if x.session_id == SID_MAIN and x.profile == "alpha")
    age_files(tree["alpha"])
    age_files(tree["beta"])
    # moving needs a target without the session, so remove the copy first
    s_beta = next(
        x for x in load_sessions([alpha, beta], rebuild=True) if x.session_id == SID_MAIN and x.profile == "beta"
    )
    assert actions.delete_session(beta, s_beta, confirm=delete_token(s_beta)).ok
    assert actions.move_session(alpha, beta, s, confirm=delete_token(s)).ok
    assert any(SID_MAIN in str(p) for p in tree["beta"].rglob("*") if p.is_file())
    assert not any(SID_MAIN in str(p) for p in tree["alpha"].rglob("*") if p.is_file())

    # ---- resources: tags, plugin and MCP toggles, settings editor, promote
    meta.set_resource_tags("skill", "solo", ["smoke"])
    assert st.set_plugin_enabled(alpha, "demo@mk", False, confirm=st.CONFIRM, dry_run=False).ok
    assert st.set_mcp_enabled(alpha, "github", False, confirm=st.CONFIRM, dry_run=False).ok
    text = st.settings_text_for_edit(alpha).replace('"sonnet"', '"opus"')
    assert st.edit_settings(alpha, text, confirm=st.CONFIRM, dry_run=False).ok
    obj = orjson.loads((tree["alpha"] / "settings.json").read_bytes())
    assert obj["model"] == "opus" and obj["enabledPlugins"]["demo@mk"] is False and obj["env"]["SOME_TOKEN"] == FAKE_KEY
    inv = scan_inventory([alpha, beta])
    assert promote("skill", "copy-skill", [alpha, beta], inv.resources, Config(), confirm="confirm", dry_run=False).ok
    inv = scan_inventory([alpha, beta])
    assert promote(
        "skill", "drift-skill", [alpha, beta], inv.resources, Config(), prefer="alpha", confirm="confirm", dry_run=False
    ).ok
    after = scan_inventory([alpha, beta])
    assert {r.link_state for r in after.resources if r.name in ("copy-skill", "drift-skill")} == {"symlink"}

    # ---- reports and registry
    data = load_all()
    report_path = write_report(build_report(data, None, None), "html")
    assert Path(report_path).exists() and FAKE_KEY not in Path(report_path).read_text()
    assert "login bug" not in Path(report_path).read_text()
    assert build_manifest(data.inventory.resources)["entries"]
    result = runner.invoke(app, ["advise", "--format", "json"])
    assert result.exit_code == 0 and isinstance(orjson.loads(result.stdout), list)
    result = runner.invoke(app, ["report", "--since", "5y", "--stdout"])
    assert result.exit_code == 0 and "Usage totals" in result.stdout

    # ---- audit: every write was recorded, and none of them touched the real home
    entries = [orjson.loads(line) for line in audit_log_path().read_text().splitlines()]
    kinds = {e["action"] for e in entries}
    expected = {
        "session.rename", "session.tags", "session.export", "session.archive", "session.delete", "session.restore",
        "session.copy", "session.move", "resource.tags", "plugin.toggle", "mcp.toggle", "settings.edit",
        "resource.promote", "report.export",
    }  # fmt: skip
    assert expected <= kinds, expected - kinds
    blob = audit_log_path().read_text()
    assert FAKE_KEY not in blob
    for e in entries:
        for value in e.values():
            if isinstance(value, str) and value.startswith(str(real_home)):
                raise AssertionError(f"audit entry points at the real home: {value}")
    assert str(themis_home()).startswith(str(tree["alpha"].parent.parent))  # state lives in the temp dir
    assert list((themis_home() / "backups").rglob("*"))  # settings and promote backups exist
