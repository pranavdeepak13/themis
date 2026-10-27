# Themis: build plan and phased prompts

Themis is a local manager for multiple Claude Code profiles. It shows sessions, skills, plugins, MCP servers, hooks and settings across `claude-personal`, `stellarai` and `pcl`, tracks tokens and context per session, and advises on token waste.

Run the four prompts in order, each in a fresh Claude Code session opened at the repo root. Commit between phases.

## 1. Decisions taken from your answers

| Topic | Decision |
|---|---|
| Interface | One Python Textual app. It runs in the terminal and in the browser through `textual serve` (`themis web`, bound to 127.0.0.1). The core is UI-agnostic, so a FastAPI frontend can be added later without touching the core. |
| Stack | Python 3.12, uv, Textual, textual-serve, Typer, pydantic v2, orjson, ruff, pytest |
| Name, license, location | Themis, Apache 2.0, repo in your personal folder, public-ready |
| Profiles | Discovered by glob (`~/.claude`, `~/.claude-*`), selected by `CLAUDE_CONFIG_DIR`. Names are not hard-coded. Optional `~/.themis/config.toml` for aliases. |
| Billing | All profiles are subscription. Show token counts. Show cost only as a labelled "API-equivalent estimate". |
| Skill scopes | Profile-only (1 profile), shared (2 to N-1 profiles), global (all profiles). Membership is decided by identical realpath (symlink) or identical content hash (copy). Project-level skills and plugin-provided skills are flagged separately. |
| Sharing | v1 detects and displays symlinked, copied and drifted skills. Creating or promoting a symlink is a write action behind dry-run and confirmation, delivered in Phase 3. |
| Context used | Context window fill from the last main-chain assistant message usage (input + cache read + cache creation), shown as tokens and percent of window, plus peak over the session. Window sizes live in a config table. |
| Memory per session | Memory written during the session: Write/Edit calls in the transcript that target `CLAUDE.md`, `CLAUDE.local.md`, `MEMORY.md` or any `memory/` directory. Count, paths, timestamps. |
| Session topic | Heuristic only (summary entries if present, else first meaningful user prompt). LLM summaries are out of scope for now. |
| Cache | No SQLite. Per-session JSON summaries in `~/.themis/cache`, keyed by file size and mtime. Streaming parse, process pool, never load a whole transcript. |
| Delete | Permanent, with confirmation. Exact file list and byte total shown. Typed confirmation. Refuses recently modified files and paths outside the profile dir. |
| Live monitoring | Out of scope. Your cmux terminal covers it. |
| Observal | Not a dependency. Themis reads local JSONL directly. |
| ARD groundwork | Built from Phase 1: every resource gets a stable `urn:air:` identifier, a manifest export and a local search API shape that follow the ARD spec in the Observal ADR. Semantic search and A2A delegation come after sessions are solid. |
| Optimisation | Advisory only. Each finding includes a ready-to-paste Claude Code prompt. |
| Scope | Single machine. Secrets always redacted. |
| Testing | Build against real data read-only, tests use synthetic fixtures only. Reason: the repo is public and real transcripts must never enter it. |

Nice-to-have items (MCP server exposure, keyboard customisation) are in the backlog at the end.

## 2. Target layout

```
themis/
  CLAUDE.md  README.md  LICENSE  pyproject.toml
  docs/DECISIONS.md  docs/DATA_NOTES.md
  src/themis/
    config.py  paths.py  redact.py  audit.py
    profiles/    discover.py  model.py
    sessions/    parser.py  metrics.py  cache.py  actions.py  search.py
    resources/   skills.py  plugins.py  mcp.py  hooks.py  agents.py
                 commands.py  settings.py  claude_md.py  symlinks.py
                 drift.py  usage.py  scopes.py
    registry/    urn.py  manifest.py  search.py
    advisor/     rules.py  prompts.py
    reports/     markdown.py  html.py
    ui/          app.py  screens/  widgets/  themis.tcss
    cli.py
  tests/  fixtures/   (synthetic only)
```

## 3. Rules block (Prompt 1 writes this into CLAUDE.md)

1. Read-only by default. Any write under a `~/.claude*` directory goes through `themis/sessions/actions.py` or `themis/resources/settings.py` write functions only. Each requires an explicit confirmation token, makes a backup where the file is edited, and appends to `~/.themis/audit.log`.
2. Never read, store or display credentials: `.credentials.json`, keychain entries, env values and tokens inside MCP config or settings. Redact in `redact.py` at the reader layer so the UI cannot show them.
3. Never modify transcript files.
4. No network calls at runtime, except the user-triggered MCP health check through the `claude` CLI. Network is allowed during development to read the ARD spec and ADR.
5. Do not trust documented file schemas. Inspect real data first, write what you find to `docs/DATA_NOTES.md`, tolerate unknown fields, count unparseable lines instead of failing.
6. Stream files line by line. Do not load a full transcript into memory.
7. Real session content never enters the repo, logs, tests or commit history. Fixtures are synthetic and generated by a script. Add a pre-commit check that fails on paths matching `~/.claude*` content or on long transcript-like JSONL.
8. The core package has no UI imports. The UI never touches the filesystem directly.
9. During development, never run delete, move, edit or toggle actions against the real profile directories. Use `THEMIS_PROFILES=<tmp copy>` for write-path testing.
10. Decide without asking and record each decision in `docs/DECISIONS.md`. Stop and ask only before anything destructive on real directories.
11. Type hints everywhere, ruff clean, small modules, pytest for parsers and actions. Commit at the end of each phase with a clear message.

## 4. Prompt 1: foundation, discovery and session data layer

```text
You are building Themis, an Apache 2.0 Python tool that manages multiple Claude Code profiles on one macOS machine. The profiles are directories selected by the CLAUDE_CONFIG_DIR environment variable, for example ~/.claude-personal, ~/.claude-stellarai and ~/.claude-pcl. This repo root is the working directory. Use uv, Python 3.12, pydantic v2, orjson, Typer, Textual, textual-serve, ruff, pytest.

Phase 1 goal: project skeleton, profile discovery, and a complete, fast, correct session data layer with a CLI to prove it. No UI yet.

Step 1. Set up the repo: git init, pyproject.toml with a `themis` console script, src layout from the plan, LICENSE (Apache 2.0), README stub, .gitignore. Write CLAUDE.md containing the eleven rules below verbatim, plus run commands.

[paste the Rules block from section 3 here]

Step 2. Inspect real data read-only before writing any parser. Look at each ~/.claude* directory: projects/<encoded-cwd>/*.jsonl, subagent transcripts, todos, file-history, shell-snapshots, debug, session-env, settings.json, .claude.json. Record the real entry types, usage fields, model fields, sidechain markers, summary or title entries, session id, cwd, git branch, version, and the file set that belongs to one session. Write findings to docs/DATA_NOTES.md. Do not print or copy any transcript content into the notes, only field names and shapes.

Step 3. Profile discovery. Glob ~/.claude and ~/.claude-*, accept a directory only if it has projects/ or settings.json. Support THEMIS_PROFILES (colon-separated paths) as an override for testing. Optional ~/.themis/config.toml for display aliases and a model context-window table.

Step 4. Session parser and metrics. For each session produce a SessionSummary: profile, session id, cwd, git branch, Claude Code version, start and end time, duration, models used, message counts, tool call counts by tool name, subagent count, input, output, cache read and cache creation tokens, cache hit rate, API-equivalent cost estimate (clearly labelled, configurable price table), context used (last main-chain usage sum, percent of window, and peak), compaction count if detectable, topic (summary entry if present, else first meaningful user prompt, truncated to 120 chars, with command and system wrappers stripped), memory written during the session (Write, Edit, MultiEdit or NotebookEdit calls targeting CLAUDE.md, CLAUDE.local.md, MEMORY.md or any memory/ directory: count, paths, timestamps), related files and their byte sizes, and disk size. Also extract skill invocations, slash command usage, MCP tool usage (mcp__server__tool) and subagent types used, stored per session for later usage stats.

Step 5. Cache. ~/.themis/cache/<profile>/<session-id>.json keyed on source file size and mtime plus a schema version. No SQLite. Use a process pool for the first scan. Stream each file. Warm start for listing all sessions must be under 2 seconds with about 5 GB of transcripts. Report progress through a callback.

Step 6. Redaction module with unit tests: tokens, API keys, bearer strings, env values in MCP definitions.

Step 7. CLI: `themis profiles`, `themis sessions [--profile X] [--sort tokens|recent|context] [--json]`, `themis session <id>`, `themis scan --rebuild`. Rich tables.

Step 8. Synthetic fixture generator in tests/ that builds a fake profile tree, and pytest tests for parser, metrics, cache invalidation and redaction. Run ruff and pytest.

Finish by running `themis sessions` against the real profiles (read-only) and sanity check the numbers: totals per profile, any session with context percent over 100, any session with zero tokens, parse error counts. Fix what you find. Commit. Summarise in under 15 lines what the real data looked like and any decision logged.
```

## 5. Prompt 2: Textual UI and the session manager

```text
Continue building Themis (read CLAUDE.md and docs/DATA_NOTES.md first). Phase 1 delivered discovery, parsing, metrics and the cache.

Phase 2 goal: the Textual app and a complete session manager. The same app must run in the terminal (`themis`) and in the browser (`themis web`, using textual-serve, bound to 127.0.0.1 only, default port 8765).

UI shell:
- Left navigation: Sessions, Skills, Plugins, MCP, Hooks, Settings, Insights. Only Sessions is functional in this phase; the others show a placeholder screen registered for Phase 3 and 4.
- Profile filter bar at the top (All, plus each discovered profile). Persistent footer with key hints. Dark theme in themis.tcss. Everything must be usable by mouse too, because the browser mode relies on clicks.
- Responsive loading: show scan progress, never block the event loop (use workers).

Sessions screen:
- Table columns: profile, last active, topic, project folder, model, total tokens, context used percent, memory writes, size on disk, tags. Sortable, filterable by profile, project, model, date range, tag, and a text filter.
- Full-text search across session content, streaming, run in a worker, showing matching snippets with the session. Snippets are shown only in the UI and are redacted. Cancelable.
- Detail pane: metadata, token breakdown (input, output, cache read, cache write, hit rate), context used with a bar and peak, memory-written file list, tool call counts, skills, commands and MCP tools used, subagents, related files with sizes, and a conversation timeline (message index, role, short preview, per-turn tokens).
- Actions, all going through sessions/actions.py with dry-run support, audit log entries and confirmation modals:
  1. Resume: show and copy the exact command `cd <cwd> && CLAUDE_CONFIG_DIR=<dir> claude --resume <id>`. Optional config key for a launcher template so it can open in another terminal; default is copy only.
  2. Delete: permanent. Modal lists every file and the total bytes. Requires typing the first 8 characters of the session id. Refuses if any file changed in the last 2 minutes, and refuses any path not inside the profile directory and not in the per-session file set.
  3. Archive: compress the session file set to ~/.themis/archive/<profile>/ with a manifest, and a restore action. Do not delete the original unless the user separately confirms.
  4. Rename and tag: stored in ~/.themis/meta.json keyed by profile and session id. Never write into transcripts.
  5. Export to markdown: redacted, with a toggle for including tool output.
  6. Move or copy to another profile: copy the file set into the target profile under the same encoded cwd directory so resume works there; move is copy plus confirmed delete of the source.
  7. Bulk select for delete, archive and tag.
- Retention warning: read cleanupPeriodDays from each profile's settings (verify the real key and the default from the data and the docs), flag sessions that are close to being purged, and offer "archive all at risk".

Tests: pytest for actions against the synthetic fixture tree under a temp THEMIS_PROFILES (delete refusal rules, move, copy, archive and restore round trip, audit log). Use Textual's pilot for a smoke test of launching the app and opening a session. Do not run any write action against the real profile directories.

Finish by launching `themis` and `themis web` and checking that both start, list real sessions read-only, and that search works. Commit. Summarise in under 15 lines.
```

## 6. Prompt 3: resources, settings and the registry groundwork

```text
Continue building Themis (read CLAUDE.md, docs/DATA_NOTES.md and docs/DECISIONS.md first). Sessions are done. Phase 3 covers everything that is not a session, plus the ARD-compatible resource registry.

Part A: resource scanners in themis/resources. Verify real file locations from the actual profile directories before coding and add them to docs/DATA_NOTES.md.
- Skills (name, description, frontmatter, body, token estimates: startup cost = name plus description, full cost = whole body, using a documented chars-per-token heuristic labelled as an estimate), slash commands, subagents, CLAUDE.md files, hooks (from settings at user, project and local level, and from plugins), MCP servers (from the real config file for each profile, with env values redacted), plugins and marketplaces (installed, enabled, source, version), permission rules, settings.json with precedence shown.
- Symlink-aware: record path, realpath, is_symlink, broken links, content hash.
- Scope classification across profiles: profile-only, shared (2 to N-1 profiles), global (all). Same realpath or same content hash counts as the same resource. Also flag: copies that are identical but not symlinked, copies that have drifted (same name, different hash) with a unified diff view, origin (user, project, plugin).
- Usage stats: join resources to the per-session usage extracted in Phase 1. Per resource: invocation count, sessions, last used, per profile.

Part B: screens.
- Skills screen: list with scope badge, profile membership matrix (rows resources, columns profiles), rendered markdown viewer, frontmatter, token estimates, usage, drift diff. Search box.
- Plugins, MCP, Hooks, Agents and Commands screens with the same pattern: list, detail, source, enabled state, which profiles have it.
- Profile comparison matrix across all resource types.
- Settings screen: merged view with precedence and origin per value, secrets redacted.
- MCP health: run `CLAUDE_CONFIG_DIR=<dir> claude mcp list` on demand with a timeout, parse the result per server, show status. Triggered by a button only, never automatically.
- Doctor: checks for broken symlinks, skills without valid frontmatter, duplicate names within a profile, invalid JSON in settings, plugins whose marketplace is missing, orphan project directories whose cwd no longer exists, very large transcripts, hooks pointing at missing scripts. Severity per finding.

Part C: write actions, all with dry-run, diff preview, backup to ~/.themis/backups, audit log and confirmation:
- Toggle plugin enabled state, using the same mechanism Claude Code uses (verify the real key). For MCP, toggle only where Claude Code has a native mechanism; if none exists, say so in the UI and do not invent one.
- Settings editor: edit the JSON with validation, show a diff, backup, then write.
- Promote to shared or global: create a symlink from a profile skill to a canonical shared directory (path from config, default ~/.themis/shared or the location the existing symlinks already point to; detect it from real data). Replace an identical copy with a symlink. Never overwrite a drifted copy without showing the diff and an explicit choice of which side wins.
- Tags on resources, stored in ~/.themis/meta.json.

Part D: registry groundwork. Fetch the ARD specification and the Observal ADR (docs/adr/0001-agentic-resource-discovery.md and docs/adr/0002-a2a-delegation.md in github.com/Observal/Observal) and follow the spec version they cite. Implement in themis/registry: stable urn:air: identifiers for every skill, agent, command, MCP server and plugin (deterministic, unaffected by path changes where possible, host segment configurable), a manifest generator (`themis registry manifest` writes an ARD-style manifest JSON), and a local search function with keyword and field filters behind an interface that can later take an embedding backend. Add `themis registry search "<query>"` and `themis registry inspect <urn>`. Do not implement semantic search or A2A delegation yet. Record the exact spec version and any deviations in docs/DECISIONS.md.

Tests: scanners and scope classification against the synthetic fixtures (including symlinks, copies and drift), toggle and edit round trips under a temp THEMIS_PROFILES, URN stability test. Never run write actions on real directories.

Commit. Summarise in under 15 lines, including what real data revealed that differed from assumptions.
```

## 7. Prompt 4: dashboards, optimisation advisor, reports, release

```text
Continue building Themis (read CLAUDE.md, docs/DATA_NOTES.md and docs/DECISIONS.md first). Phase 4 adds analytics, the advisory optimiser, reports and packaging.

Part A: dashboards (Insights screen).
- Tokens by profile, project, model and day, week or month. Cache hit rate. API-equivalent cost estimate, clearly labelled as an estimate on a subscription.
- Context behaviour: distribution of final context percent, sessions that ran near the limit, compaction frequency.
- Top sessions by tokens and by context used. Top tools. Skill, command, MCP server and subagent usage by profile.
- Charts must render in both the terminal and the browser mode (use Textual widgets such as sparklines, bar charts or a plotext-based widget; pick one and note it in DECISIONS.md).

Part B: optimisation advisor in themis/advisor. Advisory only. It never edits anything. Rules to implement, each with severity, evidence, estimated tokens saved per session or per month, and confidence:
1. Skills with zero use over a configurable window (default 60 days) while still loaded at startup.
2. MCP servers never used, with an estimate of their tool schema cost where measurable.
3. Startup context overhead per profile: skill descriptions, MCP tools, CLAUDE.md files, plugins. Show a breakdown and rank contributors.
4. Duplicate or near-duplicate skills across profiles (hash and description similarity), and identical copies that should be symlinks.
5. Drifted copies of a shared skill.
6. Oversized CLAUDE.md files or ones with repeated content across profiles.
7. Sessions that ran above 80 percent context without compaction.
8. Low cache hit rate sessions and the pattern behind them if detectable.
9. Skill descriptions that are too long or overlap in trigger wording.
For each finding the UI shows a "Copy Claude Code prompt" button. The generated prompt must be self-contained: name the profile and the exact CLAUDE_CONFIG_DIR, list the exact files involved, state the goal and the evidence from Themis, ask Claude Code to propose the change and show a diff first, and instruct it to ask for confirmation before modifying anything outside the project directory or inside any ~/.claude* directory. Also `themis advise [--profile X] [--format md|json]` on the CLI.

Part C: reports. `themis report --profile X --since 30d --format md|html` and an Export button. Contents: usage totals, top sessions, skill usage, advisor findings, doctor findings. Redacted. No transcript content.

Part D: polish and release.
- Keyboard shortcuts documented in a help screen. Empty states, error states and loading states everywhere. Handle a profile directory disappearing or being unreadable.
- README: what it is, screenshots placeholders, install with pipx and uv, `themis`, `themis web`, config file, safety model (read-only by default, what writes exist, audit log), how data is read, privacy statement, how to contribute, and the roadmap section below.
- Packaging: console script, `pipx install .` works, version command, ruff and pytest in CI config (GitHub Actions).
- Performance check on real data (read-only): cold scan time, warm start time, memory use. Fix anything slow.
- Final end-to-end smoke test in a temp copy of the profiles using the write actions, then a read-only pass on the real profiles.

Roadmap section for the README and DECISIONS.md, not to be implemented now: semantic search over resources, A2A delegation across profiles, optional LLM session summaries, exposing Themis as an MCP server, optional Observal OTLP ingest.

Commit and tag v0.1.0. Summarise in under 15 lines with the open issues you saw.
```

## 8. Backlog (not in these four prompts)

- Semantic "which skill does X" search with a local embedding model, plugged into `registry/search.py`.
- A2A cross-profile delegation, after the credential question for headless runs is answered.
- LLM-generated session summaries through a local model.
- Themis exposed as an MCP server so a Claude Code session can query other profiles' resources.
- Optional Observal or OTLP ingest.
- Keyboard remapping and themes.
