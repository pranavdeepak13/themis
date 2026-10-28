"""`themis web`: the browser frontend (`themis.web`); `themis web --terminal` serves the Textual app instead."""

from __future__ import annotations

import shlex
import sys

HOST = "127.0.0.1"  # not configurable: Themis shows local session data
DEFAULT_PORT = 8765


def serve(port: int = DEFAULT_PORT) -> None:
    from themis.web.server import serve as serve_web

    serve_web(port)


def serve_terminal(port: int = DEFAULT_PORT) -> None:
    from textual_serve.server import Server

    command = f"{shlex.quote(sys.executable)} -m themis.cli"
    Server(command, host=HOST, port=port, title="Themis").serve()
