"""Synthetic profile-tree generator. Nothing here is real session data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

SID_MAIN = "11111111-aaaa-4bbb-8ccc-000000000001"
SID_EMPTY = "22222222-aaaa-4bbb-8ccc-000000000002"
SID_OTHER = "33333333-aaaa-4bbb-8ccc-000000000003"
CWD = "/work/demo-app"
FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE"


class Writer:
    """Builds transcript lines; common envelope fields are added here, not spelled out."""

    def __init__(self, session_id: str, sidechain: bool = False, agent: str | None = None) -> None:
        self.session_id, self.sidechain, self.agent = session_id, sidechain, agent
        self.lines: list[bytes] = []
        self.n = 0

    def _env(self, kind: str, ts: str) -> dict[str, Any]:
        self.n += 1
        env: dict[str, Any] = {
            "type": kind,
            "timestamp": ts,
            "uuid": f"u{self.session_id[:2]}{self.n}",
            "sessionId": self.session_id,
            "cwd": CWD,
            "gitBranch": "main",
            "version": "9.9.9",
            "isSidechain": self.sidechain,
            "entrypoint": "cli",
        }
        if self.agent:
            env["agentId"] = self.agent
        return env

    def raw(self, line: bytes) -> None:
        self.lines.append(line)

    def entry(self, kind: str, ts: str, **fields: Any) -> None:
        self.lines.append(orjson.dumps({**self._env(kind, ts), **fields}))

    def user(self, ts: str, text: Any, **extra: Any) -> None:
        self.entry("user", ts, message={"role": "user", "content": text}, **extra)

    def assistant(
        self,
        ts: str,
        mid: str,
        blocks: list[dict[str, Any]],
        usage: dict[str, Any],
        model: str = "claude-sonnet-5-5",
    ) -> None:
        """Mimic Claude Code: one transcript line per content block, same message id."""
        for block in blocks:
            self.entry(
                "assistant",
                ts,
                message={
                    "id": mid,
                    "model": model,
                    "role": "assistant",
                    "content": [block],
                    "usage": usage,
                },
            )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\n".join(self.lines) + b"\n")


def usage(inp: int, out: int, cread: int, cwrite: int, one_hour: int = 0) -> dict[str, Any]:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cread,
        "cache_creation_input_tokens": cwrite,
        "cache_creation": {
            "ephemeral_1h_input_tokens": one_hour,
            "ephemeral_5m_input_tokens": cwrite - one_hour,
        },
    }


def tool_use(tid: str, name: str, **inp: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def build_main_session(project: Path) -> Path:
    w = Writer(SID_MAIN)
    w.user(
        "2026-01-01T10:00:00.000Z",
        "<command-name>/init</command-name><command-args></command-args>",
    )
    w.user("2026-01-01T10:00:05.000Z", f"Please fix the login bug, my key is {FAKE_KEY}")
    w.assistant(
        "2026-01-01T10:00:10.000Z",
        "msg_1",
        [
            {"type": "text", "text": "ok"},
            tool_use("t1", "Skill", skill="tdd"),
            tool_use("t2", "mcp__github__list_prs", repo="x"),
        ],
        usage(10, 100, 1000, 500, one_hour=200),
    )
    w.user("2026-01-01T10:00:20.000Z", [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}])
    w.assistant(
        "2026-01-01T10:01:00.000Z",
        "msg_2",
        [
            tool_use("t3", "Write", file_path="/work/demo-app/CLAUDE.md", content="x"),
            tool_use("t4", "Edit", file_path="/work/demo-app/src/app.py"),
            tool_use("t5", "Agent", subagent_type="Explore", prompt="p"),
            tool_use("t6", "Write", file_path="/home/u/.claude/projects/p/memory/note.md"),
        ],
        usage(20, 200, 4000, 100),
    )
    w.entry(
        "system",
        "2026-01-01T10:02:00.000Z",
        subtype="compact_boundary",
        compactMetadata={"trigger": "auto", "preTokens": 4120, "postTokens": 900},
    )
    w.assistant(
        "2026-01-01T10:03:00.000Z",
        "msg_3",
        [{"type": "text", "text": "done"}],
        usage(5, 50, 900, 0),
        model="claude-opus-5-5",
    )
    w.assistant(
        "2026-01-01T10:03:30.000Z",
        "msg_err",
        [{"type": "text", "text": "err"}],
        usage(0, 0, 0, 0),
        model="<synthetic>",
    )
    w.entry("ai-title", "2026-01-01T10:04:00.000Z", aiTitle="Fix login bug")
    w.raw(b"{this is not json")
    path = project / f"{SID_MAIN}.jsonl"
    w.write(path)

    sub = Writer(SID_MAIN, sidechain=True, agent="agent-abc")
    sub.user("2026-01-01T10:01:10.000Z", "explore")
    sub.assistant(
        "2026-01-01T10:01:20.000Z",
        "msg_sub1",
        [{"type": "text", "text": "found"}],
        usage(7, 70, 700, 70),
        model="claude-haiku-5-5",
    )
    sub.write(project / SID_MAIN / "subagents" / "agent-abc.jsonl")
    (project / SID_MAIN / "subagents" / "agent-abc.meta.json").write_bytes(b'{"agentType":"Explore"}')
    return path


def build_empty_session(project: Path) -> Path:
    w = Writer(SID_EMPTY)
    w.entry("mode", "2026-01-02T09:00:00.000Z", mode="normal")
    path = project / f"{SID_EMPTY}.jsonl"
    w.write(path)
    return path


def build_other_session(project: Path) -> Path:
    w = Writer(SID_OTHER)
    w.user("2026-01-03T09:00:00.000Z", "<system-reminder>ignore me</system-reminder>")
    w.user("2026-01-03T09:00:01.000Z", "What does\n  <b>this</b> repo do?")
    w.assistant(
        "2026-01-03T09:00:05.000Z",
        "msg_9",
        [{"type": "text", "text": "it works"}],
        usage(1, 2, 3, 4),
    )
    path = project / f"{SID_OTHER}.jsonl"
    w.write(path)
    return path


def build_profile_tree(root: Path) -> dict[str, Path]:
    """Create <root>/.claude-alpha and <root>/.claude-beta; return name -> profile dir."""
    alpha, beta = root / ".claude-alpha", root / ".claude-beta"
    proj = alpha / "projects" / "-work-demo-app"
    build_main_session(proj)
    build_empty_session(proj)
    (alpha / "file-history" / SID_MAIN).mkdir(parents=True)
    (alpha / "file-history" / SID_MAIN / "abc@v1").write_bytes(b"x" * 100)
    (alpha / "settings.json").write_bytes(b"{}")
    build_other_session(beta / "projects" / "-work-demo-app")
    return {"alpha": alpha, "beta": beta}
