"""Export a session to redacted markdown. Main chain only; tool output is opt-in."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import orjson

from themis import audit
from themis.format import when
from themis.paths import exports_dir
from themis.profiles.model import Profile
from themis.redact import redact_obj, redact_text
from themis.sessions.actions import CONFIRM, ActionError, ActionResult, _check_token
from themis.sessions.model import SessionSummary
from themis.sessions.timeline import iter_blocks, tool_result_text

TOOL_OUTPUT_MAX = 2000
TOOL_INPUT_MAX = 400


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)"


def _fence(text: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}\n{text}\n{fence}"


def export_markdown(profile: Profile, summary: SessionSummary, *, include_tool_output: bool = False) -> str:
    """Return the markdown text. Every string passes through redaction."""
    out: list[str] = [
        f"# {redact_text(summary.topic or summary.session_id)}",
        "",
        f"- Profile: {profile.name}",
        f"- Session: `{summary.session_id}`",
        f"- Project: `{summary.cwd or '-'}`",
        f"- Period: {when(summary.started_at)} to {when(summary.ended_at)}",
        f"- Tokens: {summary.tokens.total:,}",
        "",
    ]
    seen: set[str] = set()
    tool_names: dict[str, str] = {}
    transcript = profile.path / summary.transcript
    with transcript.open("rb") as fh:
        for line in fh:
            try:
                o = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(o, dict) or o.get("isSidechain") or o.get("isMeta"):
                continue
            kind, msg = o.get("type"), o.get("message")
            if kind not in ("user", "assistant") or not isinstance(msg, dict):
                continue
            if kind == "assistant":
                if msg.get("model") == "<synthetic>":
                    continue
                key = f"{msg.get('id')}:{o.get('uuid')}"
                if key in seen:
                    continue
                seen.add(key)
            for b in iter_blocks(msg.get("content")):
                _render_block(out, kind, b, include_tool_output, tool_names)
    return "\n".join(out).rstrip() + "\n"


def _render_block(out: list[str], kind: str, b: dict[str, Any], include_output: bool, names: dict[str, str]) -> None:
    t = b.get("type")
    if t == "text":
        text = str(b.get("text", "")).strip()
        if text and not text.startswith(("<system-reminder", "<local-command")):
            out += [f"## {'User' if kind == 'user' else 'Assistant'}", "", redact_text(text), ""]
    elif t == "tool_use":
        names[str(b.get("id"))] = str(b.get("name"))
        args = redact_text(orjson.dumps(redact_obj(b.get("input", {}))).decode())
        out += [f"**Tool call** `{b.get('name')}` {_fence(_clip(args, TOOL_INPUT_MAX))}", ""]
    elif t == "tool_result" and include_output:
        name = names.get(str(b.get("tool_use_id")), "tool")
        out += [f"**Tool output** (`{name}`)", _fence(_clip(redact_text(tool_result_text(b)), TOOL_OUTPUT_MAX)), ""]


def export_session(
    profile: Profile,
    summary: SessionSummary,
    *,
    include_tool_output: bool = False,
    confirm: str | None = None,
    dry_run: bool = False,
) -> ActionResult:
    """Write the markdown to ~/.themis/exports/<profile>/. Nothing is written under the profile."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", summary.topic or "session").strip("-").lower()[:40] or "session"
    dest = exports_dir() / profile.name / f"{summary.session_id[:8]}-{slug}.md"
    if dry_run:
        return ActionResult("export", True, True, "Dry run: nothing written.", path=str(dest))
    _check_token(confirm, CONFIRM)
    try:
        text = export_markdown(profile, summary, include_tool_output=include_tool_output)
    except OSError as e:
        raise ActionError(f"Cannot read transcript: {e}") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{dest}.tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, dest)
    audit.record(
        "session.export",
        profile=profile.name,
        session_id=summary.session_id,
        path=str(dest),
        tool_output=include_tool_output,
    )
    return ActionResult("export", True, False, "Exported.", bytes=len(text.encode()), path=str(dest))
