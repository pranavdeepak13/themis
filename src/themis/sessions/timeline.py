"""Conversation timeline: one row per main-chain message, streamed from the transcript."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from themis.profiles.model import Profile
from themis.redact import redact_text
from themis.sessions.model import SessionSummary

PREVIEW_MAX = 100
MAX_ITEMS = 5000
_WRAPPER = ("<local-command-caveat", "<local-command-stdout", "<system-reminder", "<task-notification")


@dataclass
class TimelineItem:
    index: int
    role: str  # user | assistant | tool_result
    preview: str
    timestamp: str | None = None
    tokens_in: int = 0  # context sent for this turn: input + cache read + cache creation
    tokens_out: int = 0
    tools: int = 0  # tool calls in this assistant message


def iter_blocks(content: Any) -> Iterator[dict[str, Any]]:
    """Content blocks of a message; a plain string becomes one text block."""
    if isinstance(content, str):
        yield {"type": "text", "text": content}
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b


def tool_result_text(block: dict[str, Any]) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(b.get("text", "")) for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _preview(text: str) -> str:
    return redact_text(" ".join(text.split()))[:PREVIEW_MAX]


def _usage_in(usage: Any) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    tin = sum(
        int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return tin, int(usage.get("output_tokens") or 0)


def build_timeline(transcript: Path, cancelled: Callable[[], bool] | None = None) -> list[TimelineItem]:
    """Main-chain messages in order. An assistant API message written as several lines is one row."""
    items: list[TimelineItem] = []
    by_msg: dict[str, TimelineItem] = {}
    with transcript.open("rb") as fh:
        for line in fh:
            if cancelled and cancelled():
                break
            if len(items) >= MAX_ITEMS:
                break
            try:
                o = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(o, dict) or o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
                continue
            msg = o.get("message")
            if not isinstance(msg, dict) or o.get("isMeta"):
                continue
            blocks = list(iter_blocks(msg.get("content")))
            if o["type"] == "user":
                texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                if texts:
                    text = "\n".join(texts)
                    if not text.lstrip().startswith(_WRAPPER):
                        items.append(TimelineItem(len(items) + 1, "user", _preview(text), o.get("timestamp")))
                elif any(b.get("type") == "tool_result" for b in blocks):
                    n = sum(1 for b in blocks if b.get("type") == "tool_result")
                    items.append(TimelineItem(len(items) + 1, "tool_result", f"{n} tool result(s)", o.get("timestamp")))
                continue
            if msg.get("model") == "<synthetic>":
                continue
            mid = msg.get("id") or o.get("uuid")
            item = by_msg.get(mid)
            if item is None:
                tin, tout = _usage_in(msg.get("usage"))
                item = TimelineItem(len(items) + 1, "assistant", "", o.get("timestamp"), tin, tout)
                by_msg[mid] = item
                items.append(item)
            for b in blocks:
                if b.get("type") == "text" and not item.preview:
                    item.preview = _preview(str(b.get("text", "")))
                elif b.get("type") == "tool_use":
                    item.tools += 1
                    if not item.preview:
                        item.preview = f"[{b.get('name', 'tool')}]"
    return items


def session_timeline(
    profile: Profile, summary: SessionSummary, cancelled: Callable[[], bool] | None = None
) -> list[TimelineItem]:
    """Timeline for a session of a profile (resolves the transcript path for the UI)."""
    return build_timeline(profile.path / summary.transcript, cancelled)
