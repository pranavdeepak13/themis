# Themis

Local manager for multiple Claude Code profiles (`~/.claude`, `~/.claude-*`).
Shows sessions, token usage and context fill per profile. Read-only by default.

Status: Phase 2 (Textual UI and session manager). Skills, plugins, MCP, hooks, settings and insights follow.

```bash
uv sync
uv run themis profiles
uv run themis sessions --sort tokens
uv run themis            # terminal UI
uv run themis web        # same UI in the browser at http://127.0.0.1:8765 (localhost only)
```

The Sessions screen lists, filters, searches and sorts sessions across profiles, and can resume (shows the
command), rename, tag, export, archive and restore, copy or move between profiles, and delete. Every write
needs confirmation, supports a dry run, and is recorded in `~/.themis/audit.log`. Press `?` in the app for keys.

Licensed under Apache 2.0.
