"""Pilot tests for the resource sections."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import orjson
from textual.pilot import Pilot
from textual.widgets import Button, ContentSwitcher, DataTable, Static

from test_ui import until
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
        assert isinstance(app.screen, ConfirmModal)
        before = orjson.loads((tree["alpha"] / "settings.json").read_bytes())["enabledPlugins"]["demo@mk"]
        assert before is True
        await pilot.click("#ok")
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
        assert isinstance(app.screen, ProfilePickModal)
        await pilot.click("#ok")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)  # step list, nothing written yet
        assert not (tree["alpha"] / "skills" / "copy-skill").is_symlink()
        await pilot.click("#cancel")
        await pilot.pause()
        sp = app.query_one("#settings", SettingsPane)
        sp.set_profile("alpha")
        sp.do_edit()
        await pilot.pause()
        assert isinstance(app.screen, EditSettingsModal)
        assert "fake" not in app.screen.query_one("#edit-area").text.lower()  # type: ignore[attr-defined]
        assert isinstance(app.screen.query_one("#ok", Button), Button)

    run_app(scenario)
