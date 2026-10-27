from __future__ import annotations

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class Tokens(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cache_creation_1h: int = 0  # subset of cache_creation billed at the 1h rate

    def add(self, other: Tokens) -> None:
        for f in self.__class__.model_fields:
            setattr(self, f, getattr(self, f) + getattr(other, f))

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_creation

    @property
    def cache_hit_rate(self) -> float | None:
        denom = self.input + self.cache_read + self.cache_creation
        return self.cache_read / denom if denom else None


class RelatedFile(BaseModel):
    kind: str  # transcript | subagent | tool-results | file-history | session-env | todos | debug
    path: str  # relative to the profile directory
    size: int


class MemoryWrite(BaseModel):
    path: str
    tool: str
    timestamp: str | None = None


class SessionSummary(BaseModel):
    schema_version: int = SCHEMA_VERSION
    profile: str
    session_id: str
    project_dir: str  # encoded cwd directory name under projects/
    transcript: str  # path relative to the profile directory
    cwd: str | None = None
    git_branch: str | None = None
    version: str | None = None
    entrypoint: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_s: float | None = None
    topic: str | None = None
    topic_source: str | None = None  # custom-title | ai-title | summary | first-prompt | command

    models: dict[str, int] = Field(default_factory=dict)  # assistant messages per model
    last_model: str | None = None
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: dict[str, int] = Field(default_factory=dict)
    subagent_count: int = 0
    subagent_types: dict[str, int] = Field(default_factory=dict)

    tokens: Tokens = Field(default_factory=Tokens)  # whole session including subagents
    subagent_tokens: Tokens = Field(default_factory=Tokens)  # subset: sidechain usage
    model_tokens: dict[str, Tokens] = Field(default_factory=dict)  # for cost estimates

    context_tokens: int = 0  # last main-chain usage: input + cache read + cache creation
    context_peak: int = 0
    compactions: int = 0

    memory_writes: list[MemoryWrite] = Field(default_factory=list)
    skills: dict[str, int] = Field(default_factory=dict)  # Skill tool invocations
    commands: dict[str, int] = Field(default_factory=dict)  # slash commands typed
    mcp_tools: dict[str, int] = Field(default_factory=dict)  # "server/tool" -> calls

    related_files: list[RelatedFile] = Field(default_factory=list)
    disk_bytes: int = 0
    parse_errors: int = 0
    lines: int = 0

    @property
    def mcp_servers(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for key, n in self.mcp_tools.items():
            server = key.split("/", 1)[0]
            out[server] = out.get(server, 0) + n
        return out
