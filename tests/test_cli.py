from __future__ import annotations

from pathlib import Path

import orjson
from typer.testing import CliRunner

from fixtures.make_fixtures import SID_MAIN
from themis.cli import app

runner = CliRunner()


def test_profiles_and_sessions_json(tree: dict[str, Path]) -> None:
    res = runner.invoke(app, ["profiles"])
    assert res.exit_code == 0 and "alpha" in res.output and "beta" in res.output
    res = runner.invoke(app, ["sessions", "--json", "--sort", "tokens", "-n", "0"])
    rows = orjson.loads(res.output)
    assert [r["session_id"] for r in rows][0] == SID_MAIN  # most tokens first
    assert rows[0]["cost_estimate_note"].startswith("API-equivalent")
    only = orjson.loads(runner.invoke(app, ["sessions", "--json", "-p", "beta", "-n", "0"]).output)
    assert {r["profile"] for r in only} == {"beta"}


def test_session_detail_by_prefix_and_unknown(tree: dict[str, Path]) -> None:
    ok = runner.invoke(app, ["session", SID_MAIN[:8], "--json"])
    assert ok.exit_code == 0 and orjson.loads(ok.output)["session_id"] == SID_MAIN
    assert runner.invoke(app, ["session", "zzzz"]).exit_code == 1


def test_scan_rebuild(tree: dict[str, Path]) -> None:
    res = runner.invoke(app, ["scan", "--rebuild"])
    assert res.exit_code == 0 and "3 sessions" in res.output
