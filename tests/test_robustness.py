"""Profile directories that vanish, become unreadable, or hold broken files must not crash anything."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from textual.widgets import Static

from fixtures.make_fixtures import FAKE_KEY
from helpers import profiles_by_name
from test_ui import until
from themis.profiles.discover import discover_profiles, usable
from themis.resources.scan import scan_inventory
from themis.resources.store import load_all
from themis.sessions.cache import load_sessions
from themis.ui.app import ThemisApp


def test_profile_that_vanishes_after_discovery_is_reported(tree: dict[str, Path]) -> None:
    found = discover_profiles()
    shutil.rmtree(tree["beta"])
    good, bad = usable(found)
    assert (
        [p.name for p in good] == ["alpha"] and bad[0].name == "beta" and "no longer exists" in (bad[0].problem or "")
    )
    assert len(load_sessions(good)) == 2 and scan_inventory(good).resources  # the survivor still scans
    scan_inventory(found)  # even handing it the stale list must not raise
    load_sessions(found)


def test_unreadable_profile_is_skipped_with_a_doctor_finding(tree: dict[str, Path]) -> None:
    os.chmod(tree["beta"], 0)
    try:
        data = load_all()  # must not raise PermissionError
        assert [p.name for p in data.profiles] == ["alpha"]
        assert [p.name for p in data.unavailable] == ["beta"]
        finding = next(f for f in data.findings if f.code == "profile-unavailable")
        assert finding.severity == "error" and finding.profile == "beta"
    finally:
        os.chmod(tree["beta"], 0o755)
    assert [p.name for p in load_all().profiles] == ["alpha", "beta"]  # and it comes back once readable


def test_broken_settings_and_unreadable_files_are_reported_without_content(tree: dict[str, Path]) -> None:
    (tree["alpha"] / "settings.json").write_text('{"env": {"TOKEN": "' + FAKE_KEY + '"')  # truncated JSON
    md = tree["alpha"] / "CLAUDE.md"
    md.write_text("secret rules")
    os.chmod(md, 0)
    try:
        data = load_all()
    finally:
        os.chmod(md, 0o644)
    codes = {f.code for f in data.findings}
    assert "config-unreadable" in codes and "broken-symlink" in codes  # unreadable CLAUDE.md is flagged as broken
    assert all(FAKE_KEY not in f.message for f in data.findings) and FAKE_KEY not in " ".join(data.inventory.errors)


def test_app_empty_states_with_no_profiles(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("THEMIS_PROFILES", str(tmp_path / "nothing-here"))
    monkeypatch.setenv("THEMIS_HOME", str(tmp_path / "themis"))

    async def go() -> None:
        app = ThemisApp()
        async with app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: getattr(app, "resource_data", None) is not None)
            for sid, status in [
                ("skills", "#r-status"),
                ("plugins", "#r-status"),
                ("doctor", "#d-status"),
                ("insights", "#i-status"),
                ("settings", "#s-status"),
                ("compare", "#c-status"),
            ]:
                app.query_one("#main").current = sid
                await pilot.pause(0.1)
                text = str(app.query_one(f"#{sid} {status}", Static).content)
                assert text and "Loading" not in text, (sid, text)
            assert "No sessions found" in str(app.query_one("#status", Static).content)

    asyncio.run(go())


def test_profiles_fixture_still_has_two(tree: dict[str, Path]) -> None:
    assert set(profiles_by_name()) == {"alpha", "beta"}
