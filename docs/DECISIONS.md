# Decisions

Format: date, decision, reason. Newest last within each phase.

## Phase 1

1. **Branch name `main`.** The fresh repo started on `master`; renamed to `main` (no commits yet) to match the plan.
2. **Python 3.12, uv, hatchling build.** `requires-python >=3.12`; uv resolved a 3.12/3.13 interpreter. Ruff line length 120 because transcript field names and Rich strings are long.
3. **Profile naming.** `~/.claude` is named `default`; `~/.claude-X` is named `X`. Aliases come from `~/.themis/config.toml` `[aliases]`. A directory is a profile only if it has `projects/` or `settings.json` (so `~/.claude-shared` is excluded).
4. **Assistant usage is deduplicated by `message.id`.** One API message is written as one line per content block with identical usage. Summing per line roughly doubles token totals. Verified on real data (no inconsistent repeats).
5. **Subagent tokens count toward session totals** and are also exposed separately as `subagent_tokens`. Context percent ignores sidechains.
6. **Tool calls deduplicated by `tool_use.id`**, so resumed or repeated lines cannot inflate counts.
7. **Context window is inferred.** The model id cannot tell 200k from 1M (both auto-compaction thresholds appear for the same model). Window = config table match (default 200k); if observed context exceeds it the window is treated as 1M. Consequence: a 1M-window session that never passed 200k shows an inflated percent. Users can override per model in `config.toml` `[context_windows]`. Percent is computed at display time, not cached, so config changes apply immediately.
8. **Cost is computed at display time** from cached per-model token buckets and the price table (`[prices.<family>]` in config; defaults for opus, sonnet, haiku). It is always labelled an API-equivalent estimate. 1-hour cache writes use the 1h rate, the rest the 5-minute rate. The price table is a placeholder to be kept current by the user; unpriced models mark the estimate incomplete rather than guessing.
9. **Topic priority:** `custom-title` (rename) > last `ai-title` > `summary` entry > first meaningful prompt > first slash command. A typed prompt beats a bare command. Wrappers (`system-reminder`, `local-command-*`, `task-notification`) are skipped and tags stripped; all topics pass through redaction and are cut at 120 characters.
10. **Memory writes:** a Write, Edit, MultiEdit or NotebookEdit whose target file name is `CLAUDE.md`, `CLAUDE.local.md` or `MEMORY.md`, or whose path has a component named exactly `memory`. Known false positive: source directories named `memory/`. Accepted to match the spec.
11. **Compaction count** = `compact_boundary` system entries.
12. **Cache validity** = schema version plus a signature of the transcript (size, mtime) and the whole session file set (count, total bytes, newest mtime), so new subagent or file-history files invalidate it. No SQLite. Cache entries for vanished sessions are pruned (only inside `~/.themis/cache`). Writes are atomic (temp file then rename).
13. **Process pool only for 6 or more cache misses**; below that, parsing inline avoids spawn cost. Warm path is single-process cache reads.
14. **`THEMIS_HOME`** overrides `~/.themis` so tests never touch real state.
15. **Related files** include `todos/<id>*` and `debug/<id>*` even though they are absent on this machine, to stay correct for older layouts. Project-level `memory/` is shared across sessions and excluded from a session's file set (matters for delete in Phase 2).
16. **Pre-commit guard** is `scripts/check_no_transcripts.py`, installed by `scripts/install-hooks.sh` (also usable through `.pre-commit-config.yaml`). It rejects any `.jsonl`, any path inside a Claude profile directory, absolute home-directory paths into `.claude*` in content, and transcript-shaped lines. `*.jsonl` is also gitignored; fixtures are generated into temp dirs at test time.
17. **Deferred modules:** `sessions/actions.py`, `sessions/search.py`, resources, registry, advisor, reports and UI packages exist as empty packages only; they belong to later phases. `audit.py` is implemented now because rule 1 depends on it.
18. **CLI table** shows the same data as `--json`; `--json` emits the full summary plus derived fields (`context_percent`, `cost_estimate_usd`, `cost_estimate_note`).
