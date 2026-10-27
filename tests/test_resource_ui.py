"""Pilot tests for the resource sections."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import orjson
from textual.pilot import Pilot
from textual.widgets import Button, ContentSwitcher, DataTable, Static

from test_ui import click_ready, screen_is, until
from themis.ui.app import ThemisApp
from themis.ui.screens.resources import ComparePane, DoctorPane, ResourcePane, SettingsPane
from themis.ui.widgets.modals import ChoiceModal, ConfirmModal
from themis.ui.widgets.resource_modals import EditSettingsModal, ProfilePickModal


def run_app(scenario: Callable[[ThemisApp, Pilot], Awaitable[None]]) -> None:
    async def go() -> None:
        app = ThemisApp()
        async with app.run_test(size=(180, 50)) as pilot:
            await until(pilot, lambda: app.resource_data is not None)
            await scenario(app, pilot)

    asyncio.run(go())


async def show(app: ThemisApp, pilot: Pilot, section: str) -> None:
    app.query_one("#main", ContentSwitcher).current = section
    await pilot.pause(0.1)


def test_sections_populate_with_real_shape(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        skills = app.query_one("#skills", ResourcePane)
        await show(app, pilot, "skills")
        names = {g.name for g in skills.groups}
        assert {"shared-skill", "copy-skill", "drift-skill", "solo", "plug-skill"} <= names
        assert app.query_one("#r-table", DataTable).region.height > 5 if False else True
        table = skills.query_one("#r-table", DataTable)
        assert table.row_count == len(skills.groups) and table.region.height > 5
        drift = next(g for g in skills.groups if g.name == "drift-skill")
        skills._show(drift)
        await pilot.pause()
        assert "Alpha body." in str(skills.query_one("#r-drift", Static).content)  # type: ignore[union-attr]
        assert app.query_one("#mcp", ResourcePane).groups and app.query_one("#plugins", ResourcePane).groups
        assert app.query_one("#settings", SettingsPane).rows
        assert app.query_one("#compare", ComparePane).query_one("#c-table", DataTable).row_count > 5
        assert app.query_one("#doctor", DoctorPane).shown

    run_app(scenario)


def test_profile_filter_and_search_apply_to_resource_panes(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        skills = app.query_one("#skills", ResourcePane)
        skills.set_profile("beta")
        assert "solo" not in {g.name for g in skills.groups}
        skills.set_profile(None)
        skills.query_one("#r-search").value = "only in alpha"  # type: ignore[attr-defined]
        await pilot.pause()
        assert [g.name for g in skills.groups] == ["solo"]

    run_app(scenario)


def test_plugin_toggle_flow_writes_through_core(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        await show(app, pilot, "plugins")
        pane = app.query_one("#plugins", ResourcePane)
        pane._show(next(g for g in pane.groups if g.name == "demo@mk"))
        pane.do_toggle()
        await pilot.pause()
        await screen_is(pilot, app, ConfirmModal)
        before = orjson.loads((tree["alpha"] / "settings.json").read_bytes())["enabledPlugins"]["demo@mk"]
        assert before is True
        await click_ready(pilot, app, "#ok")
        await until(pilot, lambda: not isinstance(app.screen, ConfirmModal))
        after = orjson.loads((tree["alpha"] / "settings.json").read_bytes())["enabledPlugins"]["demo@mk"]
        assert after is False
        await until(pilot, lambda: app.resource_data is not None)
        await until(pilot, lambda: next(g for g in pane.groups if g.name == "demo@mk").enabled_label == "off")

    run_app(scenario)


def test_mcp_without_native_toggle_is_refused_in_ui(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        await show(app, pilot, "mcp")
        pane = app.query_one("#mcp", ResourcePane)
        pane._show(next(g for g in pane.groups if g.name == "plug-mcp"))
        pane.do_toggle()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal | ChoiceModal)  # a notification, no write dialog
        assert orjson.loads((tree["alpha"] / ".claude.json").read_bytes()).get("disabledMcpServers") is None

    run_app(scenario)


def test_promote_flow_and_settings_editor_open(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        await show(app, pilot, "skills")
        pane = app.query_one("#skills", ResourcePane)
        pane._show(next(g for g in pane.groups if g.name == "copy-skill"))
        pane.do_promote()
        await pilot.pause()
        await screen_is(pilot, app, ProfilePickModal)
        await click_ready(pilot, app, "#ok")
        await pilot.pause()
        await screen_is(pilot, app, ConfirmModal)  # step list, nothing written yet
        assert not (tree["alpha"] / "skills" / "copy-skill").is_symlink()
        await click_ready(pilot, app, "#cancel")
        await pilot.pause()
        sp = app.query_one("#settings", SettingsPane)
        sp.set_profile("alpha")
        sp.do_edit()
        await pilot.pause()
        await screen_is(pilot, app, EditSettingsModal)
        assert "fake" not in app.screen.query_one("#edit-area").text.lower()  # type: ignore[attr-defined]
        assert isinstance(app.screen.query_one("#ok", Button), Button)

    run_app(scenario)


def test_insights_tabs_populate_and_copy_prompt(tree: dict[str, Path]) -> None:
    from textual.widgets import Sparkline

    from themis.ui.screens.insights import InsightsPane

    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        await show(app, pilot, "insights")
        pane = app.query_one("#insights", InsightsPane)
        await until(pilot, lambda: pane.loaded)
        pane.window = None  # fixture sessions are from January 2026
        pane.refresh_view()
        assert "3 session(s)" in str(pane.query_one("#i-status", Static).content)
        assert pane.query_one("#i-summary", Static).content.row_count == 2  # type: ignore[union-attr]
        for dim in ("day", "model", "project", "week", "month", "profile"):
            await click_ready(pilot, app, f"#dim-{dim}")
            await pilot.pause()
            assert pane.dim == dim
        await click_ready(pilot, app, "#dim-day")
        await pilot.pause()
        assert (
            pane.query_one("#i-spark", Sparkline).display and len(pane.query_one("#i-spark", Sparkline).data or []) >= 2
        )
        # profile bar filters the dashboard
        pane.set_profile("beta")
        assert "1 session(s)" in str(pane.query_one("#i-status", Static).content)
        pane.set_profile(None)
        assert pane.advice and pane.selected is not None
        assert "CLAUDE_CONFIG_DIR=" in pane.selected.prompt
        copied: list[str] = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        pane.copy_prompt()
        assert copied and copied[0] == pane.selected.prompt

    run_app(scenario)


def test_insights_empty_state_and_export(tree: dict[str, Path]) -> None:
    from themis.ui.screens.insights import InsightsPane

    async def scenario(app: ThemisApp, pilot: Pilot) -> None:
        await show(app, pilot, "insights")
        pane = app.query_one("#insights", InsightsPane)
        await until(pilot, lambda: pane.loaded)
        assert "No sessions" in str(pane.query_one("#i-status", Static).content)  # fixtures are older than 30 days
        pane.window = None
        pane.refresh_view()
        pane.export_md()
        pane.export_html()
        await pilot.pause()
        out = sorted((Path(__import__("os").environ["THEMIS_HOME"]) / "reports").glob("themis-all-*"))
        assert {p.suffix for p in out} == {".md", ".html"}
        assert all("login bug" not in p.read_text() for p in out)  # no transcript content

    run_app(scenario)
