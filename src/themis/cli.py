"""Command line interface. Phase 1 proves the data layer; the Textual UI comes next."""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from themis.config import Config, load_config
from themis.profiles.discover import discover_profiles
from themis.profiles.model import Profile
from themis.sessions.cache import find_transcripts, load_sessions
from themis.sessions.metrics import context_percent, context_window, estimate_cost
from themis.sessions.model import SessionSummary

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Manage Claude Code profiles.")
console = Console()
err = Console(stderr=True)


class SortKey(enum.StrEnum):
    tokens = "tokens"
    recent = "recent"
    context = "context"


def human(n: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{n}B"


def _when(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16]


def _select(profile: str | None) -> list[Profile]:
    profiles = discover_profiles()
    if profile:
        profiles = [p for p in profiles if profile in (p.name, p.alias)]
        if not profiles:
            err.print(f"[red]No profile named {profile!r}[/red]")
            raise typer.Exit(2)
    return profiles


def _load(profiles: list[Profile], rebuild: bool = False) -> list[SessionSummary]:
    if not console.is_terminal:
        return load_sessions(profiles, rebuild=rebuild)
    with Progress(
        TextColumn("Scanning"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    ) as bar:
        task = bar.add_task("", total=None)
        return load_sessions(
            profiles,
            rebuild=rebuild,
            progress=lambda d, t: bar.update(task, completed=d, total=t),
        )


def _row_dict(s: SessionSummary, cfg: Config) -> dict:
    cost, incomplete = estimate_cost(s, cfg)
    d = s.model_dump(mode="json")
    d["total_tokens"] = s.tokens.total
    d["cache_hit_rate"] = s.tokens.cache_hit_rate
    d["context_window"] = context_window(s.last_model, s.context_peak, cfg)
    d["context_percent"] = context_percent(s, cfg)
    d["context_peak_percent"] = context_percent(s, cfg, peak=True)
    d["cost_estimate_usd"] = round(cost, 4)
    d["cost_estimate_incomplete"] = incomplete
    d["cost_estimate_note"] = "API-equivalent estimate; all profiles are on subscription"
    return d


@app.command()
def profiles() -> None:
    """List discovered profiles."""
    found = discover_profiles()
    table = Table("name", "alias", "path", "sessions", "disk")
    for p in found:
        n = len(find_transcripts(p))
        table.add_row(p.name, p.alias or "", str(p.path), str(n), "")
    if not found:
        err.print("No profiles found.")
    console.print(table)


@app.command()
def sessions(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    sort: Annotated[SortKey, typer.Option("--sort")] = SortKey.recent,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    limit: Annotated[int, typer.Option("--limit", "-n", help="0 = all")] = 50,
) -> None:
    """List sessions."""
    cfg = load_config()
    found = _load(_select(profile))
    keys = {
        SortKey.tokens: lambda s: s.tokens.total,
        SortKey.recent: lambda s: s.ended_at or "",
        SortKey.context: lambda s: context_percent(s, cfg, peak=True),
    }
    found.sort(key=keys[sort], reverse=True)
    shown = found[:limit] if limit else found
    if as_json:
        typer.echo(orjson.dumps([_row_dict(s, cfg) for s in shown], option=orjson.OPT_INDENT_2).decode())
        return

    table = Table(title=f"{len(shown)} of {len(found)} sessions", show_lines=False)
    for col in (
        "profile",
        "id",
        "last active",
        "topic",
        "project",
        "model",
        "tokens",
        "ctx%",
        "peak%",
    ):
        table.add_column(col, justify="right" if col in ("tokens", "ctx%", "peak%") else "left")
    for s in shown:
        table.add_row(
            s.profile,
            s.session_id[:8],
            _when(s.ended_at),
            (s.topic or "-")[:48],
            Path(s.cwd).name if s.cwd else "-",
            (s.last_model or "-").removeprefix("claude-"),
            human(s.tokens.total),
            f"{context_percent(s, cfg):.0f}",
            f"{context_percent(s, cfg, peak=True):.0f}",
        )
    console.print(table)
    for name in sorted({s.profile for s in found}):
        mine = [s for s in found if s.profile == name]
        console.print(f"[dim]{name}: {len(mine)} sessions, {human(sum(s.tokens.total for s in mine))} tokens[/dim]")


@app.command()
def session(
    session_id: str,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one session in detail (id prefix is enough)."""
    cfg = load_config()
    matches = [s for s in _load(discover_profiles()) if s.session_id.startswith(session_id)]
    if not matches:
        err.print(f"[red]No session starting with {session_id!r}[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        err.print("[red]Ambiguous id:[/red] " + ", ".join(f"{m.profile}/{m.session_id[:12]}" for m in matches))
        raise typer.Exit(1)
    s = matches[0]
    d = _row_dict(s, cfg)
    if as_json:
        typer.echo(orjson.dumps(d, option=orjson.OPT_INDENT_2).decode())
        return
    t = s.tokens
    hit = t.cache_hit_rate
    console.print(f"[bold]{s.topic or '(no topic)'}[/bold]  [dim]({s.topic_source})[/dim]")
    console.print(f"{s.profile} / {s.session_id}\ncwd {s.cwd}  branch {s.git_branch}  claude {s.version}")
    console.print(f"{_when(s.started_at)} -> {_when(s.ended_at)}  ({(s.duration_s or 0) / 60:.0f} min)")
    console.print(f"models {s.models}")
    console.print(f"messages: {s.user_messages} user, {s.assistant_messages} assistant; subagents {s.subagent_count}")
    console.print(
        f"tokens: in {human(t.input)}  out {human(t.output)}  cache read {human(t.cache_read)}  "
        f"cache write {human(t.cache_creation)}  hit rate {'-' if hit is None else f'{hit:.0%}'}"
    )
    console.print(
        f"context: {human(s.context_tokens)} ({d['context_percent']}% of {human(d['context_window'])}), "
        f"peak {human(s.context_peak)} ({d['context_peak_percent']}%), compactions {s.compactions}"
    )
    mark = "+" if d["cost_estimate_incomplete"] else ""
    console.print(f"cost: ~${d['cost_estimate_usd']:.2f}{mark} API-equivalent estimate (subscription, not billed)")
    console.print(f"tools: {dict(sorted(s.tool_calls.items(), key=lambda kv: -kv[1])[:10])}")
    for label, data in (
        ("skills", s.skills),
        ("commands", s.commands),
        ("mcp", s.mcp_tools),
        ("subagent types", s.subagent_types),
    ):
        if data:
            console.print(f"{label}: {data}")
    console.print(f"memory writes: {len(s.memory_writes)}")
    for m in s.memory_writes[:10]:
        console.print(f"  {m.tool} {m.path}  [dim]{_when(m.timestamp)}[/dim]")
    console.print(f"files: {len(s.related_files)}, {human_bytes(s.disk_bytes)} on disk; parse errors {s.parse_errors}")


@app.command()
def scan(
    rebuild: Annotated[bool, typer.Option("--rebuild", help="Ignore the cache and re-parse everything.")] = False,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """Refresh the summary cache."""
    import time

    start = time.perf_counter()
    found = _load(_select(profile), rebuild=rebuild)
    console.print(f"{len(found)} sessions in {time.perf_counter() - start:.2f}s{' (rebuilt)' if rebuild else ''}")


if __name__ == "__main__":
    app()
