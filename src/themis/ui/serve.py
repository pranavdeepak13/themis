"""`themis web`: serve the same Textual app in the browser, bound to localhost only."""

from __future__ import annotations

import shlex
import sys

HOST = "127.0.0.1"  # not configurable: Themis shows local session data
DEFAULT_PORT = 8765


def serve(port: int = DEFAULT_PORT) -> None:
    from textual_serve.server import Server

    command = f"{shlex.quote(sys.executable)} -m themis.cli"
    Server(command, host=HOST, port=port, title="Themis").serve()
