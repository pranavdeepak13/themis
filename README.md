# Themis

Themis is a local manager for several [Claude Code](https://claude.com/claude-code) profiles on one machine
(`~/.claude`, `~/.claude-personal`, `~/.claude-work`, and so on, selected with `CLAUDE_CONFIG_DIR`). It shows
sessions, skills, agents, commands, plugins, MCP servers, hooks and settings across profiles, tracks tokens and
context fill per session, and advises where tokens are being wasted. It runs in the terminal and, from the same
code, in the browser on `127.0.0.1`.

Everything is local. Themis is **read-only by default**; the few write actions are explicit, confirmed, backed
up and audited (see [Safety model](#safety-model)).

> Screenshots: _placeholder, add `docs/img/sessions.png`, `skills.png`, `insights.png`._

## What you get

| Section | What it does |
|---|---|
| **Sessions** | Table of every session: last active, topic, project, model, tokens, context %, memory writes, size. Filter, sort, full-text search, detail with token breakdown and timeline. Resume command, rename, tag, export, archive and restore, copy or move between profiles, delete, bulk actions, retention warnings. |
| **Skills, Agents, Commands** | Per-profile membership, scope (profile-only, shared, global), symlink / copy / drift detection with diffs, token cost estimates, usage from your transcripts, tags. Promote a skill to a shared directory with a symlink. |
| **Plugins, MCP, Hooks** | Installed and enabled state per profile, sources and versions, redacted MCP definitions, on-demand MCP health check, enable or disable where Claude Code has a native mechanism. |
| **Settings** | Merged view with precedence and origin per value, secrets masked; validated editor with diff and backup. |
| **Compare** | One matrix of every resource against every profile. |
| **Doctor** | Broken symlinks, bad frontmatter, duplicate names, invalid JSON, missing marketplaces or scripts, orphan project folders, huge transcripts, unreadable profiles. |
| **Insights** | Tokens by profile, project, model and day, week or month; cache hit rate; API-equivalent cost estimate (labelled, since profiles are on a subscription); context behaviour; top sessions and tools; skill, command, MCP and subagent usage; the optimisation **advisor** with a "Copy Claude Code prompt" button per finding. |
| **Reports** | `themis report` writes a redacted markdown or HTML report with no transcript content. |
| **Registry** | Every skill, agent, command, MCP server and plugin gets a stable `urn:air:` identifier (ARD v0.91); `themis registry` writes a manifest and searches it. |

## Install

Requires Python 3.12 or newer and macOS (Linux should work; it is not tested).

```bash
# pipx
pipx install .                      # from a checkout
# uv
uv tool install .                   # from a checkout
# or run from the source tree
uv sync && uv run themis
```

The first start of `themis web` can take several seconds while Python compiles bytecode.

## Use

```bash
themis                      # terminal UI
themis web [--port 8765]    # the same UI in the browser, http://127.0.0.1:8765 (localhost only)
themis profiles             # discovered profiles
themis sessions --sort tokens --profile pcl
themis session <id-prefix>
themis resources skill      # skills, commands, agents, hooks, mcp, plugins
themis doctor
themis advise [--profile X] [--format md|json] [--prompts]
themis report --profile X --since 30d --format md|html
themis registry manifest | search "query" [-f type=application/agent-skills+md] | inspect <urn>
themis scan --rebuild       # rebuild the per-session summary cache
themis version
```

Press `?` in the app for the keyboard shortcuts. Everything is usable with the mouse too.

## Configuration

Optional `~/.themis/config.toml`:

```toml
[aliases]              # display names for profiles
pcl = "client-pcl"

resume_launcher = "osascript -e 'tell app \"Terminal\" to do script \"{command}\"'"  # default: copy only
retention_warn_days = 7        # flag sessions this close to Claude Code's cleanup (cleanupPeriodDays, default 30)
shared_dir = "~/.claude-shared"   # canonical dir for promoted skills; default: detected from existing symlinks
large_transcript_mb = 100

[context_windows]      # substring of the model id -> window in tokens
"[1m]" = 1000000

[prices.sonnet]        # USD per million tokens, for the API-equivalent estimate only
input = 3
output = 15
cache_read = 0.3
cache_write_5m = 3.75
cache_write_1h = 6

[advisor]              # thresholds for `themis advise` and the Advisor tab
unused_days = 60
claude_md_max_tokens = 2000
skill_desc_max_chars = 500
description_overlap = 0.6
near_duplicate = 0.85
high_context_percent = 80
low_cache_hit_rate = 0.5
```

Environment: `THEMIS_PROFILES=dir1:dir2` overrides discovery, `THEMIS_HOME` overrides `~/.themis`.

## Safety model

- **Read-only by default.** Scanning, searching, the dashboards, the advisor and reports never write under a
  `~/.claude*` directory. Themis keeps its own state in `~/.themis` (cache, metadata, archives, exports, reports,
  backups, audit log).
- **The complete list of writes** (each lives in `sessions/actions.py`, `resources/settings.py` or
  `resources/symlinks.py`; each supports a dry run, needs a confirmation, and appends to `~/.themis/audit.log`):
  - sessions: delete (typed id prefix, refuses recently modified files and paths outside the profile), archive,
    restore, copy, move; export to `~/.themis/exports`; rename and tags (stored in `~/.themis/meta.json`, never in
    the transcript)
  - resources: enable or disable a plugin (`enabledPlugins` in `settings.json`), enable or disable a user-scope MCP
    server (`disabledMcpServers` in `.claude.json`), the settings editor, promote to a shared symlink
  - Files that are edited are backed up first to `~/.themis/backups`. Replaced copies are moved aside, not deleted.
- **Transcripts are never modified.** They are streamed line by line and never loaded whole.
- **Advisor is advisory.** It never edits anything; each finding carries a prompt you paste into Claude Code, which
  asks for confirmation before touching anything inside `~/.claude*`.

## How data is read

Themis reads the same files Claude Code writes: `projects/<cwd>/<session>.jsonl` (plus subagent transcripts),
`settings.json`, `.claude.json` (only the MCP server entries), `plugins/*.json`, `skills/`, `commands/`, `agents/`.
Per-session summaries are cached as JSON in `~/.themis/cache`, keyed by file size and modification time. There is
no database. Real file shapes and the quirks found are in [docs/DATA_NOTES.md](docs/DATA_NOTES.md); design
decisions are in [docs/DECISIONS.md](docs/DECISIONS.md).

## Privacy

- No network access at runtime, with one exception: the MCP health check you trigger by hand, which runs
  `claude mcp list` (the `claude` CLI does the talking).
- Credentials are never read or shown: `.credentials.json` is ignored, secrets in MCP `env`/`headers`, settings and
  free text are masked at the reader layer.
- Reports contain counts and configuration metadata only: no session topics, prompts, snippets or tool output.
- Real transcripts never enter this repository; a pre-commit guard (`sh scripts/install-hooks.sh`) blocks them and
  tests use synthetic fixtures only.

## Contributing

```bash
uv sync
sh scripts/install-hooks.sh       # transcript guard
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Read [CLAUDE.md](CLAUDE.md) first: the rules there (read-only by default, no credentials, synthetic fixtures,
core package without UI imports) apply to every change. Log design decisions in `docs/DECISIONS.md`.
`scripts/smoke_real_copy.py` exercises the write paths on a redacted temp copy of your own configuration.

## Roadmap (not implemented)

- Semantic search over resources with a local embedding model, plugged into `registry/search.py`
- A2A delegation across profiles, once the credential question for headless runs is settled
- Optional LLM-generated session summaries through a local model
- Exposing Themis as an MCP server so a Claude Code session can query other profiles' resources
- Optional Observal OTLP ingest

Licensed under Apache 2.0.
