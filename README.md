# Themis

Local manager for multiple Claude Code profiles (`~/.claude`, `~/.claude-*`).
Shows sessions, token usage and context fill per profile. Read-only by default.

Status: Phase 1 (profile discovery, session data layer, CLI). UI follows.

```bash
uv sync
uv run themis profiles
uv run themis sessions --sort tokens
```

Licensed under Apache 2.0.
