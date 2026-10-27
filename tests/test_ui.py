"""Smoke tests for the Textual app, driven with the pilot against the synthetic profile tree."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Static, Tabs

from fixtures.make_fixtures import SID_MAIN
from helpers import age_files
from themis.ui.app import ThemisApp
from themis.ui.screens.sessions import SessionsPane
from themis.ui.widgets.modals import ConfirmModal, ResumeModal, TextPromptModal


async def until(pilot: Pilot, cond: Callable[[], bool], timeout: float = 8.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out waiting for the UI"
        await pilot.pause(0.05)


def run_app(scenario: Callable[[ThemisApp, Pilot, SessionsPane], Awaitable[None]]) -> None:
    async def go() -> None:
        app = ThemisApp()
        async with app.run_test(size=(180, 50)) as pilot:
            pane = app.query_one(SessionsPane)
            await until(pilot, lambda: pane.loaded)
            await scenario(app, pilot, pane)

    asyncio.run(go())


def test_lists_sessions_and_opens_detail(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        table = app.query_one("#table", DataTable)
        assert table.row_count == 3 and len(pane.all_rows) == 3
        # regression: the pane once collapsed to zero size because ContentSwitcher defaults to height: auto
        assert table.region.height > 10 and table.region.width > 60
        assert app.query_one("#detail").region.width > 30
        await pilot.press("down")
        await pilot.pause()
        row = pane.current_row()
        assert row is not None and row.key == pane.selected_key
        overview = app.query_one("#overview", Static)
        assert row.topic in str(overview.content.renderables[0])  # type: ignore[union-attr]
        # the profile bar got a tab per profile
        assert [t.id for t in app.query_one("#profiles", Tabs).query("Tab")] == ["p-all", "p-alpha", "p-beta"]

    run_app(scenario)


def test_profile_filter_text_filter_and_sort(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        app.query_one("#profiles", Tabs).active = "p-beta"
        await until(pilot, lambda: app.query_one("#table", DataTable).row_count == 1)
        app.set_profile_tab(None)
        await until(pilot, lambda: app.query_one("#table", DataTable).row_count == 3)
        app.query_one("#f-text", Input).value = "login"
        await until(pilot, lambda: app.query_one("#table", DataTable).row_count == 1)
        assert pane.view_rows[0].summary.session_id == SID_MAIN
        app.query_one("#f-text", Input).value = ""
        await until(pilot, lambda: app.query_one("#table", DataTable).row_count == 3)
        await pilot.press("s")
        assert pane.sort_col != "last"

    run_app(scenario)


def test_navigation_placeholders_and_help(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#main", ContentSwitcher).current == "skills"
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one("#main", ContentSwitcher).current == "sessions"
        await pilot.press("question_mark")
        await pilot.pause()
        assert type(app.screen).__name__ == "HelpModal"
        await pilot.press("escape")
        await pilot.pause()

    run_app(scenario)


def test_search_streams_hits_and_reveals_session(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        await pilot.press("slash")
        box = app.query_one("#search-input", Input)
        assert box.has_focus
        box.value = "login bug"
        await pilot.press("enter")
        await until(pilot, lambda: len(pane._search_hits) == 1)
        hit = pane._search_hits[0]
        assert hit.session_id == SID_MAIN and "REDACTED" in hit.snippet
        await until(pilot, lambda: "done" in str(app.query_one("#search-status", Static).content))
        app.query_one("#hits", DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert pane.selected_key == f"alpha/{SID_MAIN}"
        await pilot.press("escape")
        assert app.query_one("#search-panel").has_class("hidden")

    run_app(scenario)


def test_resume_shows_exact_command(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        pane.reveal(f"alpha/{SID_MAIN}")
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, ResumeModal)
        shown = str(app.screen.query_one("#resume-command", Static).content)
        assert shown == f"cd /work/demo-app && CLAUDE_CONFIG_DIR={tree['alpha']} claude --resume {SID_MAIN}"
        await pilot.press("escape")

    run_app(scenario)


def test_delete_flow_is_blocked_for_recent_then_needs_typed_token(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        pane.reveal(f"alpha/{SID_MAIN}")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        assert app.screen.query_one("#ok", Button).disabled  # files were just written: refused
        await pilot.press("escape")
        await pilot.pause()
        assert (tree["alpha"] / "projects/-work-demo-app" / f"{SID_MAIN}.jsonl").exists()

        age_files(tree["alpha"])
        await pilot.press("d")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        ok = modal.query_one("#ok", Button)
        assert ok.disabled
        modal.query_one("#dialog-token", Input).value = "wrong"
        await pilot.pause()
        assert ok.disabled
        modal.query_one("#dialog-token", Input).value = SID_MAIN[:8]
        await pilot.pause()
        assert not ok.disabled
        await pilot.click("#ok")
        await until(pilot, lambda: len(pane.all_rows) == 2 and pane.loaded)
        assert not (tree["alpha"] / "projects/-work-demo-app" / f"{SID_MAIN}.jsonl").exists()

    run_app(scenario)


def test_mark_tag_and_bulk_bar(tree: dict[str, Path]) -> None:
    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        assert len(pane.marked) == 2 and not app.query_one("#bulk").has_class("hidden")
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, TextPromptModal)
        app.screen.query_one(Input).value = "review, urgent"
        await pilot.press("enter")
        await pilot.pause()
        assert sum(1 for r in pane.all_rows if r.tags == ["review", "urgent"]) == 2
        await pilot.press("u")
        assert not pane.marked

    run_app(scenario)


def test_narrow_terminal_stacks_detail_under_table(tree: dict[str, Path]) -> None:
    async def go() -> None:
        app = ThemisApp()
        async with app.run_test(size=(120, 50)) as pilot:
            pane = app.query_one(SessionsPane)
            await until(pilot, lambda: pane.loaded)
            await pilot.pause()
            assert pane.has_class("stacked")
            table, detail = app.query_one("#table"), app.query_one("#detail")
            assert detail.region.y > table.region.y and detail.region.width >= table.region.width - 2

    asyncio.run(go())


def test_retention_banner_and_archive_all_at_risk(tree: dict[str, Path]) -> None:
    import os
    from pathlib import Path as P

    import orjson

    old = time.time() - 27 * 86400  # default 30 days, warn window 7 days
    for p in tree["alpha"].rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    (tree["alpha"] / "settings.json").write_bytes(orjson.dumps({}))

    async def scenario(app: ThemisApp, pilot: Pilot, pane: SessionsPane) -> None:
        assert len(pane.at_risk_rows) == 2  # both alpha sessions; beta's files are fresh
        assert not app.query_one("#banner").has_class("hidden")
        assert "2 session(s)" in str(app.query_one("#banner-text", Static).content)
        await pilot.click("#btn-archive-risk")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        await pilot.click("#ok")
        archive_dir = P(os.environ["THEMIS_HOME"]) / "archive" / "alpha"
        await until(pilot, lambda: len(list(archive_dir.glob("*.tar.gz"))) == 2)
        assert (tree["alpha"] / "projects/-work-demo-app" / f"{SID_MAIN}.jsonl").exists()  # originals stay

    run_app(scenario)
