"""On-demand MCP health check through the `claude` CLI. The only runtime network use (rule 4).

Triggered by a button or an explicit command only, never automatically.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from themis.profiles.model import Profile
from themis.redact import redact_text

TIMEOUT_S = 60
_LINE = re.compile(r"^(?P<name>.+?):\s+(?P<target>.*?)\s+-\s+(?P<status>\S.*)$")


@dataclass(frozen=True)
class ServerHealth:
    name: str
    status: str  # connected | failed | needs-auth | unknown
    detail: str


def parse_mcp_list(text: str) -> list[ServerHealth]:
    out: list[ServerHealth] = []
    for line in redact_text(text).splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        raw = m["status"]
        low = raw.lower()
        status = (
            "connected"
            if "connected" in low and "not" not in low and "fail" not in low
            else "needs-auth"
            if "auth" in low
            else "failed"
            if "fail" in low or "error" in low or "✗" in raw
            else "unknown"
        )
        out.append(ServerHealth(m["name"].strip(), status, raw.strip()))
    return out


Runner = Callable[[list[str], dict[str, str], int], subprocess.CompletedProcess[str]]


def _run(cmd: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, cwd=os.path.expanduser("~"))


def check_profile(
    profile: Profile, timeout: int = TIMEOUT_S, runner: Runner = _run
) -> tuple[list[ServerHealth], str | None]:
    """(servers, error). Runs `CLAUDE_CONFIG_DIR=<dir> claude mcp list`."""
    if runner is _run and not shutil.which("claude"):
        return [], "The `claude` CLI was not found on PATH."
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(profile.path)}
    try:
        proc = runner(["claude", "mcp", "list"], env, timeout)
    except subprocess.TimeoutExpired:
        return [], f"Timed out after {timeout}s."
    except OSError as exc:
        return [], f"Could not run claude: {exc.__class__.__name__}"
    if proc.returncode != 0 and not proc.stdout.strip():
        return [], redact_text((proc.stderr or "claude mcp list failed").strip())[:300]
    return parse_mcp_list(proc.stdout), None
