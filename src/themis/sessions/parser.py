"""Stream a session transcript (plus its subagent transcripts) into a SessionSummary.

Field names come from real data, see docs/DATA_NOTES.md. Unknown fields and entry types are
ignored; unparseable lines are counted, never fatal.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from themis.redact import redact_text
from themis.sessions.files import session_files
from themis.sessions.model import MemoryWrite, SessionSummary, Tokens

MEMORY_FILES = {"CLAUDE.md", "CLAUDE.local.md", "MEMORY.md"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
TOPIC_MAX = 120
_SKIP_PROMPT = re.compile(r"^\s*<(local-command-caveat|local-command-stdout|system-reminder|task-notification)")
_COMMAND = re.compile(r"<command-name>\s*(/?[^<\s]+)\s*</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_TAG = re.compile(r"</?[a-zA-Z][\w-]*>")


def _iter_lines(path: Path) -> Iterator[bytes]:
    with path.open("rb") as fh:
        yield from fh


def _is_memory_path(raw: str) -> bool:
    p = Path(raw)
    return p.name in MEMORY_FILES or "memory" in p.parts


def _text_of(content: Any) -> str | None:
    """Plain user text from a message content, or None when it is only tool results."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def _clean_topic(text: str) -> str:
    text = _TAG.sub(" ", text)
    text = " ".join(text.split())
    return redact_text(text)[:TOPIC_MAX]


def _usage_tokens(usage: dict[str, Any]) -> Tokens:
    cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    return Tokens(
        input=int(usage.get("input_tokens") or 0),
        output=int(usage.get("output_tokens") or 0),
        cache_read=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation=int(usage.get("cache_creation_input_tokens") or 0),
        cache_creation_1h=int(cc.get("ephemeral_1h_input_tokens") or 0),
    )


class _Acc:
    def __init__(self) -> None:
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.cwd = self.branch = self.version = self.entrypoint = None
        self.custom_title = self.ai_title = self.summary = None
        self.first_prompt = self.first_command = None
        self.models: Counter[str] = Counter()
        self.last_model: str | None = None
        self.user_messages = 0
        self.assistant_ids: set[str] = set()
        self.tool_calls: Counter[str] = Counter()
        self.tool_ids: set[str] = set()
        self.agent_ids: set[str] = set()
        self.subagent_types: Counter[str] = Counter()
        self.tokens = Tokens()
        self.sub_tokens = Tokens()
        self.model_tokens: dict[str, Tokens] = {}
        self.ctx = 0
        self.ctx_peak = 0
        self.compactions = 0
        self.memory: list[MemoryWrite] = []
        self.skills: Counter[str] = Counter()
        self.commands: Counter[str] = Counter()
        self.mcp: Counter[str] = Counter()
        self.errors = 0
        self.lines = 0

    def feed(self, o: dict[str, Any], sidechain_file: bool) -> None:
        t = o.get("type")
        ts = o.get("timestamp")
        sidechain = sidechain_file or bool(o.get("isSidechain"))
        if isinstance(ts, str):
            if self.first_ts is None or ts < self.first_ts:
                self.first_ts = ts
            if self.last_ts is None or ts > self.last_ts:
                self.last_ts = ts
        if isinstance(o.get("agentId"), str):
            self.agent_ids.add(o["agentId"])
        if not sidechain:
            self.cwd = self.cwd or o.get("cwd")
            self.branch = o.get("gitBranch") or self.branch
            self.version = o.get("version") or self.version
            self.entrypoint = self.entrypoint or o.get("entrypoint")

        if t == "assistant":
            self._assistant(o, sidechain, ts)
        elif t == "user":
            if not sidechain:
                self._user(o)
        elif t == "custom-title":
            self.custom_title = o.get("customTitle") or self.custom_title
        elif t == "ai-title":
            self.ai_title = o.get("aiTitle") or self.ai_title
        elif t == "summary":
            self.summary = o.get("summary") or self.summary
        elif t == "system" and o.get("subtype") == "compact_boundary":
            self.compactions += 1

    def _user(self, o: dict[str, Any]) -> None:
        if o.get("isMeta") or o.get("isCompactSummary"):
            return
        text = _text_of((o.get("message") or {}).get("content"))
        if text is None:
            return  # tool results only
        self.user_messages += 1
        if _SKIP_PROMPT.match(text):
            return
        m = _COMMAND.search(text)
        if m:
            name = m.group(1) if m.group(1).startswith("/") else "/" + m.group(1)
            self.commands[name] += 1
            if self.first_command is None:
                args = _COMMAND_ARGS.search(text)
                self.first_command = f"{name} {args.group(1).strip()}".strip() if args else name
            return
        if self.first_prompt is None:
            cleaned = _clean_topic(text)
            if cleaned:
                self.first_prompt = cleaned

    def _assistant(self, o: dict[str, Any], sidechain: bool, ts: Any) -> None:
        msg = o.get("message")
        if not isinstance(msg, dict):
            return
        mid = msg.get("id") or o.get("uuid")
        model = msg.get("model")
        # One API message is written once per content block; count its usage only once.
        if mid and mid not in self.assistant_ids:
            self.assistant_ids.add(mid)
            usage = msg.get("usage")
            if model != "<synthetic>":
                if model:
                    self.models[model] += 1
                if isinstance(usage, dict):
                    tok = _usage_tokens(usage)
                    self.tokens.add(tok)
                    if sidechain:
                        self.sub_tokens.add(tok)
                    if model:
                        self.model_tokens.setdefault(model, Tokens()).add(tok)
                    ctx = tok.input + tok.cache_read + tok.cache_creation
                    if not sidechain and ctx > 0:
                        self.ctx = ctx
                        self.ctx_peak = max(self.ctx_peak, ctx)
                        self.last_model = model or self.last_model
        content = msg.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            bid = block.get("id")
            if bid in self.tool_ids:
                continue
            if bid:
                self.tool_ids.add(bid)
            self._tool_use(block, ts)

    def _tool_use(self, block: dict[str, Any], ts: Any) -> None:
        name = block.get("name") or "?"
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        self.tool_calls[name] += 1
        if name == "Skill" and isinstance(inp.get("skill"), str):
            self.skills[inp["skill"]] += 1
        elif name == "Agent" and isinstance(inp.get("subagent_type"), str):
            self.subagent_types[inp["subagent_type"]] += 1
        elif name.startswith("mcp__"):
            parts = name.split("__", 2)
            self.mcp[f"{parts[1]}/{parts[2]}" if len(parts) == 3 else name] += 1
        if name in WRITE_TOOLS:
            target = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
            if isinstance(target, str) and _is_memory_path(target):
                self.memory.append(MemoryWrite(path=target, tool=name, timestamp=ts))


def _parse_file(path: Path, acc: _Acc, sidechain_file: bool) -> None:
    for line in _iter_lines(path):
        if not line.strip():
            continue
        acc.lines += 1
        try:
            o = orjson.loads(line)
        except orjson.JSONDecodeError:
            acc.errors += 1
            continue
        if isinstance(o, dict):
            acc.feed(o, sidechain_file)


def _duration(first: str | None, last: str | None) -> float | None:
    if not first or not last:
        return None
    try:
        a = datetime.fromisoformat(first.replace("Z", "+00:00"))
        b = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (b - a).total_seconds())


def parse_session(profile: str, profile_dir: Path, transcript: Path) -> SessionSummary:
    """Parse one session. `transcript` is projects/<encoded-cwd>/<session-id>.jsonl."""
    session_id = transcript.stem
    project_dir = transcript.parent
    acc = _Acc()
    _parse_file(transcript, acc, sidechain_file=False)
    files = session_files(profile_dir, project_dir, session_id)
    for rf, _mtime in files:
        if rf.kind == "subagent" and rf.path.endswith(".jsonl"):
            _parse_file(profile_dir / rf.path, acc, sidechain_file=True)

    if acc.custom_title:
        topic, source = acc.custom_title, "custom-title"
    elif acc.ai_title:
        topic, source = acc.ai_title, "ai-title"
    elif acc.summary:
        topic, source = acc.summary, "summary"
    elif acc.first_prompt:
        topic, source = acc.first_prompt, "first-prompt"
    elif acc.first_command:
        topic, source = acc.first_command, "command"
    else:
        topic, source = None, None
    if topic and source in ("custom-title", "ai-title", "summary"):
        topic = redact_text(" ".join(topic.split()))[:TOPIC_MAX]

    return SessionSummary(
        profile=profile,
        session_id=session_id,
        project_dir=project_dir.name,
        transcript=str(transcript.relative_to(profile_dir)),
        cwd=acc.cwd,
        git_branch=acc.branch,
        version=acc.version,
        entrypoint=acc.entrypoint,
        started_at=acc.first_ts,
        ended_at=acc.last_ts,
        duration_s=_duration(acc.first_ts, acc.last_ts),
        topic=topic,
        topic_source=source,
        models=dict(acc.models),
        last_model=acc.last_model,
        user_messages=acc.user_messages,
        assistant_messages=len(acc.assistant_ids),
        tool_calls=dict(acc.tool_calls),
        subagent_count=max(
            len(acc.agent_ids),
            sum(1 for f, _ in files if f.kind == "subagent" and f.path.endswith(".jsonl")),
        ),
        subagent_types=dict(acc.subagent_types),
        tokens=acc.tokens,
        subagent_tokens=acc.sub_tokens,
        model_tokens=acc.model_tokens,
        context_tokens=acc.ctx,
        context_peak=acc.ctx_peak,
        compactions=acc.compactions,
        memory_writes=acc.memory,
        skills=dict(acc.skills),
        commands=dict(acc.commands),
        mcp_tools=dict(acc.mcp),
        related_files=[f for f, _ in files],
        disk_bytes=sum(f.size for f, _ in files),
        parse_errors=acc.errors,
        lines=acc.lines,
    )
