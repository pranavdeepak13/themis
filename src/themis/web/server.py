"""Local HTTP server for the web frontend. Binds 127.0.0.1 only and rejects foreign Host/Origin headers, so a
page on another site cannot drive the toggle endpoints (DNS rebinding and CSRF)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aiohttp import web

from themis.resources.store import ResourceData, load_all
from themis.web import api

HOST = "127.0.0.1"  # not configurable: Themis shows local session data
DEFAULT_PORT = 8765
STATIC = Path(__file__).parent / "static"
HOLD: web.AppKey[dict[str, ResourceData]] = web.AppKey("hold")  # mutable: aiohttp freezes app keys once started
LOCK: web.AppKey[asyncio.Lock] = web.AppKey("lock")
PORT: web.AppKey[int] = web.AppKey("port")


def _allowed_hosts(port: int) -> set[str]:
    return {f"{HOST}:{port}", f"localhost:{port}"}


@web.middleware
async def guard(request: web.Request, handler: Any) -> web.StreamResponse:
    port = request.app[PORT]
    if request.headers.get("Host", "") not in _allowed_hosts(port):
        raise web.HTTPForbidden(text="Unexpected Host header.")
    if request.method == "POST":
        origin = request.headers.get("Origin", "")
        if origin not in {f"http://{h}" for h in _allowed_hosts(port)}:
            raise web.HTTPForbidden(text="Unexpected Origin.")
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
    return resp


def _profile(request: web.Request) -> str | None:
    return request.query.get("profile") or None


async def _load(app: web.Application, rebuild: bool = False) -> None:
    async with app[LOCK]:
        app[HOLD]["data"] = await asyncio.get_running_loop().run_in_executor(None, lambda: load_all(rebuild=rebuild))


async def get_meta(request: web.Request) -> web.Response:
    return web.json_response(api.meta(request.app[HOLD]["data"]))


async def get_overview(request: web.Request) -> web.Response:
    return web.json_response(
        api.overview(request.app[HOLD]["data"], _profile(request), request.query.get("since", "30d"))
    )


async def get_sessions(request: web.Request) -> web.Response:
    q = request.query
    return web.json_response(
        api.sessions(request.app[HOLD]["data"], _profile(request), q.get("q", ""), q.get("sort", "recent"))
    )


async def get_resources(request: web.Request) -> web.Response:
    try:
        out = api.resources(
            request.app[HOLD]["data"], request.match_info["kind"], _profile(request), request.query.get("q", "")
        )
    except KeyError:
        raise web.HTTPNotFound(text="Unknown resource kind.") from None
    return web.json_response(out)


async def get_doctor(request: web.Request) -> web.Response:
    return web.json_response(api.doctor(request.app[HOLD]["data"], _profile(request)))


async def get_advice(request: web.Request) -> web.Response:
    return web.json_response(api.advice(request.app[HOLD]["data"], _profile(request)))


async def post_refresh(request: web.Request) -> web.Response:
    await _load(request.app, rebuild=bool((await request.json()).get("rebuild")))
    return web.json_response(api.meta(request.app[HOLD]["data"]))


async def post_toggle(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        kind, name, profile = str(body["kind"]), str(body["name"]), str(body["profile"])
        enabled, apply = bool(body["enabled"]), bool(body.get("apply"))
    except (KeyError, TypeError):
        raise web.HTTPBadRequest(text="kind, name, profile and enabled are required.") from None
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: api.toggle(request.app[HOLD]["data"], kind, name, profile, enabled, apply)
        )
    except api.ToggleError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    if result["applied"]:
        await _load(request.app)
    return web.json_response(result)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


def make_app(port: int = DEFAULT_PORT, data: ResourceData | None = None) -> web.Application:
    app = web.Application(middlewares=[guard])
    app[PORT] = port
    app[LOCK] = asyncio.Lock()
    app[HOLD] = {} if data is None else {"data": data}

    async def startup(app: web.Application) -> None:
        if "data" not in app[HOLD]:
            await _load(app)

    app.on_startup.append(startup)
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/meta", get_meta),
            web.get("/api/overview", get_overview),
            web.get("/api/sessions", get_sessions),
            web.get("/api/resources/{kind}", get_resources),
            web.get("/api/doctor", get_doctor),
            web.get("/api/advice", get_advice),
            web.post("/api/refresh", post_refresh),
            web.post("/api/toggle", post_toggle),
            web.static("/static", STATIC, follow_symlinks=False),
        ]
    )
    return app


def serve(port: int = DEFAULT_PORT) -> None:
    web.run_app(make_app(port), host=HOST, port=port, print=None)
