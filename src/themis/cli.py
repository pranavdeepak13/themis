"""Command line interface. Phase 1 proves the data layer; the Textual UI comes next."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Annotated

import orjson
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from themis.config import Config, load_config
from themis.format import human, human_bytes
from themis.format import when as _when
from themis.profiles.discover import discover_profiles
from themis.profiles.model import Profile
from themis.sessions.cache import find_transcripts, load_sessions
from themis.sessions.metrics import context_percent, context_window, estimate_cost
from themis.sessions.model import SessionSummary

app = typer.Typer(add_completion=False, help="Manage Claude Code profiles. Run without a command for the UI.")
console = Console()
err = Console(stderr=True)


class SortKey(enum.StrEnum):
    tokens = "tokens"
    recent = "recent"
    context = "context"


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


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Open the terminal UI when no command is given."""
    if ctx.invoked_subcommand is None:
        from themis.ui.app import run

        run()


@app.command()
def web(port: Annotated[int, typer.Option("--port", "-p", help="Port to serve on (127.0.0.1 only).")] = 8765) -> None:
    """Serve the same UI in the browser at http://127.0.0.1:PORT."""
    from themis.ui.serve import HOST, serve

    err.print(f"Themis web UI on http://{HOST}:{port}  (Ctrl+C to stop)")
    serve(port)


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


@app.command()
def resources(
    kind: Annotated[str | None, typer.Argument(help="skill, command, agent, hook, mcp, plugin, claude_md")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List skills, commands, agents, hooks, MCP servers and plugins with scope and usage."""
    from themis.resources.store import load_all

    data = load_all()
    rows = [
        r for r in data.inventory.resources if (not kind or r.kind == kind) and (not profile or r.profile == profile)
    ]
    if as_json:
        out = []
        for r in rows:
            u = data.usage.of(r)
            out.append(
                {**r.model_dump(mode="json", exclude={"extra"}), "invocations": u.invocations, "last_used": u.last_used}
            )
        typer.echo(orjson.dumps(out, option=orjson.OPT_INDENT_2).decode())
        return
    table = Table(
        "kind", "name", "profile", "origin", "scope", "link", "on", "tok(start)", "uses", title=f"{len(rows)} resources"
    )
    for r in sorted(rows, key=lambda r: (r.kind, r.name, r.profile)):
        table.add_row(
            r.kind,
            r.name[:44],
            r.profile,
            r.origin,
            r.scope or "-",
            r.link_state or "-",
            {True: "yes", False: "no", None: "-"}[r.enabled],
            str(r.tokens_startup),
            str(data.usage.of(r).invocations),
        )
    console.print(table)
    console.print("[dim]Token figures are estimates (about 4 characters per token).[/dim]")


@app.command()
def doctor(profile: Annotated[str | None, typer.Option("--profile", "-p")] = None) -> None:
    """Run read-only health checks."""
    from themis.resources.store import load_all

    findings = [f for f in load_all().findings if not profile or f.profile == profile]
    colour = {"error": "red", "warning": "yellow", "info": "blue"}
    for f in findings:
        console.print(f"[{colour[f.severity]}]{f.severity:7}[/] {f.profile or '-':10} {f.code}: {f.message}")
    console.print(f"{len(findings)} finding(s)" if findings else "[green]No findings.[/green]")


registry_app = typer.Typer(help="ARD-style local resource registry.", add_completion=False)
app.add_typer(registry_app, name="registry")


def _manifest(publisher: str) -> dict:
    from themis.registry.manifest import build_manifest
    from themis.resources.store import load_all

    data = load_all()
    return build_manifest(data.inventory.resources, publisher, data.meta)


@registry_app.command("manifest")
def registry_manifest(
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Default: ~/.themis/registry/ard.json")] = None,
    publisher: Annotated[str, typer.Option(help="Publisher domain in the urn:air: identifiers.")] = "themis.local",
    stdout: Annotated[bool, typer.Option("--stdout", help="Print instead of writing a file.")] = False,
) -> None:
    """Write an ARD v0.91 manifest of every skill, agent, command, MCP server and plugin."""
    from themis import audit
    from themis.paths import themis_home

    body = orjson.dumps(_manifest(publisher), option=orjson.OPT_INDENT_2)
    if stdout:
        typer.echo(body.decode())
        return
    path = out or themis_home() / "registry" / "ard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + b"\n")
    audit.record("registry.manifest", path=str(path))
    console.print(f"Wrote {path}")


@registry_app.command("search")
def registry_search(
    query: str,
    filter: Annotated[
        list[str] | None, typer.Option("--filter", "-f", help="term=value, repeatable (OR per term)")
    ] = None,
    type: Annotated[
        str | None, typer.Option("--type", help="Media type filter, e.g. application/agent-skills+md")
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    scope: Annotated[str | None, typer.Option("--scope")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 10,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    publisher: Annotated[str, typer.Option()] = "themis.local",
) -> None:
    """Keyword search shaped like ARD POST /search (text + filter)."""
    from themis.registry.search import search

    filt: dict[str, list[str]] = {}
    for item in filter or []:
        term, _, value = item.partition("=")
        filt.setdefault(term, []).append(value)
    for term, value in (("type", type), ("metadata.themis.profiles", profile), ("metadata.themis.scope", scope)):
        if value:
            filt.setdefault(term, []).append(value)
    resp = search(_manifest(publisher), query, filt, limit)
    if as_json:
        typer.echo(orjson.dumps(resp, option=orjson.OPT_INDENT_2).decode())
        return
    table = Table("score", "identifier", "type", "description")
    for r in resp["results"]:
        table.add_row(
            f"{r['score']:.0f}",
            r["identifier"],
            r["type"].removeprefix("application/"),
            (r.get("description") or "")[:50],
        )
    console.print(table)
    if "pageToken" in resp:
        console.print("[dim]More results available (use --limit).[/dim]")


@registry_app.command("inspect")
def registry_inspect(urn: str, publisher: Annotated[str, typer.Option()] = "themis.local") -> None:
    """Show one registry entry."""
    from themis.registry.search import inspect

    entry = inspect(_manifest(publisher), urn)
    if entry is None:
        err.print(f"[red]No resource {urn!r}[/red]")
        raise typer.Exit(1)
    typer.echo(orjson.dumps(entry, option=orjson.OPT_INDENT_2).decode())


if __name__ == "__main__":
    app()
