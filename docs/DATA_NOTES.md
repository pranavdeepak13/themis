# Data notes: what the real profile data looks like

Inspected read-only on 2026-10-27 across four profile directories. Field names and shapes only; no transcript content is recorded here. Counts are from this machine and are only illustrative of proportions.

## Profile directories

- Profiles found: `~/.claude` (name `default`), `~/.claude-personal`, `~/.claude-stellarai`, `~/.claude-pcl`. `~/.claude-shared` also matches the glob but has neither `projects/` nor `settings.json` (it holds shared `agents/`, `commands/`, `skills/`), so it is correctly rejected as a profile. It is a hint for the canonical shared directory used in Phase 3.
- Each profile directory also contains a hidden `.claude.json` (profile-local) and `.last-cleanup`. The home-level `~/.claude.json` is a separate file. MCP servers appear under `mcpServers` in `.claude.json`, at top level and per project; this is for Phase 3 and must be redacted.
- Common children: `projects/`, `sessions/`, `session-env/`, `file-history/`, `shell-snapshots/`, `settings.json`, `plugins/`, `skills/`, `commands/`, `agents/`, `plans/`, `backups/`, `cache/`, `telemetry/`, `history.jsonl`.
- Not present on this machine (older Claude Code layouts): `todos/`, `debug/`. Themis still looks for `todos/<session-id>*` and `debug/<session-id>*` in case they exist.
- Sizes: 63 main transcripts (about 197 MB) plus 165 subagent transcripts (about 123 MB). Sessions per profile: default 5, personal 4, stellarai 36, pcl 18.

## Session file set

- Main transcript: `projects/<encoded-cwd>/<session-id>.jsonl`. The encoded cwd replaces `/` and `.` with `-`. The file name always equals the `sessionId` field inside it (63 of 63).
- Per-session directory `projects/<encoded-cwd>/<session-id>/` holds `subagents/agent-<id>.jsonl`, `subagents/agent-<id>.meta.json` and `tool-results/*.txt`.
- Other per-session locations: `file-history/<session-id>/`, `session-env/<session-id>/`.
- `sessions/<pid>.json` is keyed by process id, not session id, so it is not part of a session's file set.
- `projects/<encoded-cwd>/memory/*.md` is project-level auto memory, shared by all sessions of that project. It is not counted as session disk size.

## Transcript entry types

Every line is one JSON object with a `type`. No unparseable lines were seen. Types seen: `user`, `assistant`, `attachment`, `system`, `api-request`, `api-request-blob`, `api-request-shape`, `last-prompt`, `ai-title`, `custom-title`, `agent-name`, `mode`, `permission-mode`, `atis-latch`, `bridge-session`, `pr-link`, `relocated`, `worktree-state`, `queue-operation`, `file-history-snapshot`, `file-history-delta`, `cost-state`, `tools-used`, `continued-in`. A `summary` type was not seen but is handled if present. Unknown types are ignored.

Envelope fields on `user`, `assistant`, `attachment` and `system` entries: `parentUuid`, `uuid`, `timestamp` (ISO 8601, UTC, `Z`), `sessionId`, `cwd`, `gitBranch`, `version`, `entrypoint` (`cli`, `claude-desktop`, `sdk-cli`), `isSidechain`, `userType`, plus `agentId` and `slug` in some cases.

- `ai-title`: `aiTitle`. Many per session; the last wins. `custom-title`: `customTitle` (set by rename). Neither carries a timestamp.
- `system`: `subtype` values seen are `stop_hook_summary`, `turn_duration`, `away_summary`, `local_command`, `informational`, `api_error`, `compact_boundary`, `scheduled_task_fire`, `bridge_status`. Compaction is `compact_boundary` with `compactMetadata` containing `trigger` (`auto` or `manual`), `preTokens`, `postTokens`, `durationMs`, `preservedSegment`, `cumulativeDroppedTokens`.
- `cost-state`: `totalCostUSD`, `modelUsage`, durations. Not used (Themis computes its own labelled estimate).
- Prompts: `user` entries whose `message.content` is a string (typed prompt, slash command wrapper or system wrapper) or a list. A list of only `tool_result` blocks is a tool response, not a prompt. Flags `isMeta` and `isCompactSummary` mark non-prompts.
- Slash commands appear inside a user string as `<command-name>/x</command-name>` with `<command-message>` and `<command-args>`. Other wrappers seen: `local-command-caveat`, `local-command-stdout`, `system-reminder`, `task-notification`.

## Assistant messages and usage (important)

- **One API message is written as several transcript lines**, one per content block (`apiBlockIndex`), all sharing `message.id` and `requestId` and carrying identical `usage`. Of 5.9k assistant lines in the largest profile, 3.2k were repeats. Summing usage per line double counts; Themis counts usage once per `message.id`. Usage was identical across repeats in every case checked.
- `message.usage`: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `cache_creation` (`ephemeral_5m_input_tokens`, `ephemeral_1h_input_tokens`), `server_tool_use` (`web_search_requests`, `web_fetch_requests`), `service_tier`, `inference_geo`, `speed`, `iterations`, `output_tokens_details` (`thinking_tokens`).
- `message.model` values seen: `claude-sonnet-5-5`, `claude-sonnet-5`, `claude-opus-5-5`, `claude-haiku-5-5`, `claude-haiku-4-5-20251001`, and `<synthetic>` (local error messages with zero usage; skipped).
- Content blocks: `text`, `thinking`, `tool_use` (`id`, `name`, `input`), `tool_result`, `image`, `tool_addition`, `tool_removal`.
- Assistant entries also carry attribution fields: `attributionSkill`, `attributionPlugin`, `attributionAgent`, `attributionMcpServer`, `attributionMcpTool`. Themis currently uses the `tool_use` blocks instead (explicit, one per call). The attribution fields are a candidate cross-check for Phase 3.

## Tool usage shapes

- Skill: tool `Skill`, input `skill` (and optional `args`).
- Subagent: tool `Agent`, input `subagent_type`, `description`, `prompt`, optional `model`. Subagent transcripts also have `.meta.json` with `agentType`, `description`, `toolUseId`, `spawnDepth`, `finished`.
- MCP: tool name `mcp__<server>__<tool>`. Server names can themselves contain `-`, so splitting on `__` into at most three parts is safe.
- Memory writes: `Write` and `Edit` (also `MultiEdit`, `NotebookEdit`) with `input.file_path`. Targets seen: `CLAUDE.md`, `memory/*.md` under project memory directories.

## Subagents and sidechains

- Subagent transcripts are separate files, with `isSidechain: true` and an `agentId`. Their usage is real consumption and is added to session totals, and also reported separately (`subagent_tokens`).
- Context percent is taken from the last non-sidechain assistant message only.

## Context window and compaction

- Auto-compaction was seen at about 195k to 202k tokens and at about 968k tokens, so both 200k and 1M windows are in use, sometimes with the same model id. The model id does not say which window applies. Themis uses the configured window for the model (default 200k) and switches to 1M when observed context exceeds it. See DECISIONS.md.
- Peak context is the maximum of `input + cache_read + cache_creation` over main-chain assistant messages.

## Settings

- `settings.json` keys seen: `enabledPlugins`, `extraKnownMarketplaces`, `hooks`, `permissions`, `model`, `modelSettings`, `statusLine`, `theme`, `tui`, `skillOverrides`, `autoMode`, `autoCompactEnabled`, notification flags.
- `cleanupPeriodDays` is **not set in any profile**, so retention falls back to Claude Code's default. The default must be verified against the docs in Phase 2 before the retention warning ships.

## Edge cases observed

- Ten sessions have zero tokens and between 6 and 38 lines: sessions opened and abandoned, or containing only bookkeeping entries. They are valid, not parse failures.
- A resumed or forked session can contain `sessionId`-equal lines only; no mismatching ids were found.
- Some top-level entries carry no `timestamp` (titles, modes), so start and end times come from entries that do.
