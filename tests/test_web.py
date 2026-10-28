from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from themis.web.server import make_app

PORT = 8765
HEADERS = {"Host": f"127.0.0.1:{PORT}"}
POST = {**HEADERS, "Origin": f"http://127.0.0.1:{PORT}"}


def run(coro: Any) -> Any:
    async def go() -> Any:
        async with TestClient(TestServer(make_app(PORT))) as client:
            return await coro(client)

    return asyncio.run(go())


def test_pages_and_json(tree: dict[str, Path]) -> None:
    async def go(c: TestClient) -> None:
        assert (await c.get("/", headers=HEADERS)).status == 200
        assert (await c.get("/static/app.js", headers=HEADERS)).status == 200
        meta = await (await c.get("/api/meta", headers=HEADERS)).json()
        assert {p["name"] for p in meta["profiles"]} == set(tree)
        ov = await (await c.get("/api/overview?since=all", headers=HEADERS)).json()
        assert ov["sessions"] >= 1 and ov["tokens"]["total"] > 0
        for kind in ("skills", "agents", "commands", "plugins", "mcp", "hooks"):
            r = await c.get(f"/api/resources/{kind}", headers=HEADERS)
            assert r.status == 200, kind
        assert (await c.get("/api/resources/nope", headers=HEADERS)).status == 404
        for path in ("/api/sessions", "/api/doctor", "/api/advice"):
            assert (await c.get(path, headers=HEADERS)).status == 200

    run(go)


def test_rejects_foreign_host_and_origin(tree: dict[str, Path]) -> None:
    async def go(c: TestClient) -> None:
        assert (await c.get("/api/meta", headers={"Host": "evil.example"})).status == 403
        body = {"kind": "plugin", "name": "demo@mk", "profile": "alpha", "enabled": False}
        assert (
            await c.post("/api/toggle", json=body, headers={**HEADERS, "Origin": "http://evil.example"})
        ).status == 403
        assert (await c.post("/api/toggle", json=body, headers=HEADERS)).status == 403  # no Origin at all

    run(go)


def test_plugin_toggle_previews_then_writes(tree: dict[str, Path]) -> None:
    settings = tree["alpha"] / "settings.json"
    before = settings.read_bytes()

    async def go(c: TestClient) -> None:
        body = {"kind": "plugin", "name": "demo@mk", "profile": "alpha", "enabled": False}
        plan = await (await c.post("/api/toggle", json=body, headers=POST)).json()
        assert plan["changed"] and not plan["applied"] and "demo@mk" in plan["diff"]
        assert settings.read_bytes() == before  # preview never writes
        done = await (await c.post("/api/toggle", json={**body, "apply": True}, headers=POST)).json()
        assert done["applied"] and done["backup"]
        assert json.loads(settings.read_text())["enabledPlugins"]["demo@mk"] is False
        rows = (await (await c.get("/api/resources/plugins", headers=HEADERS)).json())["rows"]
        demo = next(r for r in rows if r["name"] == "demo@mk" or r["name"] == "demo")
        assert any(m["profile"] == "alpha" and m["enabled"] is False for m in demo["members"])
        bad = await c.post("/api/toggle", json={**body, "profile": "nope"}, headers=POST)
        assert bad.status == 400

    run(go)


def test_mcp_toggle(tree: dict[str, Path]) -> None:
    async def go(c: TestClient) -> None:
        rows = (await (await c.get("/api/resources/mcp?profile=alpha", headers=HEADERS)).json())["rows"]
        user = next(r for r in rows for m in r["members"] if m["toggleable"])
        body = {"kind": "mcp", "name": user["name"], "profile": "alpha", "enabled": False, "apply": True}
        assert (await (await c.post("/api/toggle", json=body, headers=POST)).json())["applied"]
        cfg = json.loads((tree["alpha"] / ".claude.json").read_text())
        assert user["name"] in cfg["disabledMcpServers"]

    run(go)
