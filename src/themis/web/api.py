"""JSON views over loaded data for the web frontend. No HTTP and no UI imports; write calls go through
`resources/settings.py` only."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from themis import analytics
from themis.advisor.rules import run_advisor
from themis.profiles.model import Profile
from themis.resources import settings as st
from themis.resources.model import Resource
from themis.resources.store import ResourceData
from themis.resources.view import Group, build_groups
from themis.sessions.metrics import context_percent, estimate_cost
from themis.sessions.model import SessionSummary
from themis.sessions.query import Filters as SessionFilters
from themis.sessions.query import apply_filters, build_rows, sort_rows

KINDS: dict[str, tuple[str, ...]] = {
    "skills": ("skill",),
    "agents": ("agent",),
    "commands": ("command",),
    "plugins": ("plugin",),
    "mcp": ("mcp",),
    "hooks": ("hook",),
}
TOGGLEABLE = {"plugin", "mcp"}


def _in_window(data: ResourceData, profile: str | None, since: str) -> list[SessionSummary]:
    found = [s for s in data.sessions if profile in (None, s.profile)]
    if since and since != "all":
        found = analytics.within(found, analytics.parse_since(since))
    return found


def overview(data: ResourceData, profile: str | None, since: str) -> dict[str, Any]:
    cfg = data.cfg
    ss = _in_window(data, profile, since)
    tok = analytics.total_tokens(ss)
    cost, incomplete = analytics.total_cost(ss, cfg)
    day = analytics.tokens_by(ss, "day", cfg)
    usage = analytics.usage_by_profile

    def ranked(kind: str) -> list[dict[str, Any]]:
        rows = [{"name": n, "uses": sum(p.values()), "profiles": p} for n, p in usage(ss, kind).items()]
        return sorted(rows, key=lambda r: -r["uses"])[:8]

    inv = [r for r in data.inventory.resources if profile in (None, r.profile)]
    used_skills = {k for s in ss for k in s.skills}
    skills = {r.name for r in inv if r.kind == "skill" and r.origin != "project"}
    unused = sorted(n for n in skills if not any(u == n or u.endswith(":" + n) for u in used_skills))
    ctx = analytics.context_stats(ss, cfg)
    return {
        "sessions": len(ss),
        "tokens": tok.model_dump() | {"total": tok.total},
        "cache_hit": tok.cache_hit_rate,
        "cost": round(cost, 2),
        "cost_incomplete": incomplete,
        "daily": [{"label": b.label, "tokens": b.total, "sessions": b.sessions, "cost": round(b.cost, 2)} for b in day],
        "by_model": [
            {"label": b.label, "tokens": b.total, "cost": round(b.cost, 2)}
            for b in analytics.tokens_by(ss, "model", cfg)[:6]
        ],
        "by_profile": [
            {"label": b.label, "tokens": b.total, "sessions": b.sessions, "cost": round(b.cost, 2)}
            for b in analytics.tokens_by(ss, "profile", cfg)
        ],
        "top_skills": ranked("skills"),
        "top_commands": ranked("commands"),
        "top_mcp": ranked("mcp"),
        "top_agents": ranked("subagents"),
        "top_tools": [{"name": n, "uses": c} for n, c in analytics.top_tools(ss, 8)],
        "installed_skills": len(skills),
        "unused_skills": len(unused),
        "unused_sample": unused[:6],
        "context": {
            "compaction_rate": ctx.compaction_rate,
            "near_limit": len(ctx.near_limit),
            "sessions": ctx.sessions,
        },
        "findings": sum(1 for f in data.findings if f.severity != "info"),
        "advice": len(run_advisor(data, profile=profile)),
    }


def sessions(data: ResourceData, profile: str | None, q: str, sort: str, limit: int = 300) -> dict[str, Any]:
    rows = build_rows(data.sessions, data.cfg, data.meta)
    rows = apply_filters(rows, SessionFilters(profile=profile, text=q))
    rows = sort_rows(
        rows, {"recent": "last"}.get(sort, sort) if sort in {"recent", "tokens", "context"} else "last", True
    )
    out = []
    for r in rows[:limit]:
        s = r.summary
        out.append(
            {
                "id": s.session_id,
                "profile": s.profile,
                "topic": r.topic,
                "project": r.project,
                "model": r.model,
                "ended": s.ended_at,
                "tokens": s.tokens.total,
                "cost": round(estimate_cost(s, data.cfg)[0], 2),
                "context": round(context_percent(s, data.cfg, peak=True)),
                "messages": s.user_messages + s.assistant_messages,
                "tools": sum(s.tool_calls.values()),
                "subagents": s.subagent_count,
                "compactions": s.compactions,
                "cache_hit": s.tokens.cache_hit_rate,
                "branch": s.git_branch,
                "tags": r.tags,
                "top_tools": Counter(s.tool_calls).most_common(5),
                "skills": s.skills,
                "mcp": s.mcp_servers,
                "duration_s": s.duration_s,
            }
        )
    return {"total": len(rows), "rows": out}


def _member(m: Resource, data: ResourceData) -> dict[str, Any]:
    can = m.kind in TOGGLEABLE and (m.kind == "plugin" or m.origin == "user")
    return {
        "profile": m.profile,
        "enabled": m.enabled,
        "path": m.path,
        "drifted": m.drifted,
        "link": m.link_state,
        "toggleable": can and m.enabled is not None,
    }


def _group(g: Group, data: ResourceData) -> dict[str, Any]:
    f = g.first
    return {
        "key": g.key,
        "kind": g.kind,
        "name": g.name,
        "origin": g.origin_name or g.origin,
        "scope": g.scope,
        "description": f.description or "",
        "info": g.info,
        "profiles": g.profiles,
        "tags": g.tags,
        "uses": g.usage.invocations,
        "sessions": g.usage.sessions,
        "last_used": g.usage.last_used,
        "per_profile": g.usage.per_profile,
        "startup_tokens": f.tokens_startup,
        "full_tokens": f.tokens_full,
        "state": g.enabled_label,
        "drifted": g.drifted,
        "members": [_member(m, data) for m in g.members],
        "frontmatter": f.frontmatter if g.kind in ("skill", "agent", "command") else {},
    }


def resources(data: ResourceData, kind: str, profile: str | None, q: str) -> dict[str, Any]:
    kinds = KINDS.get(kind)
    if kinds is None:
        raise KeyError(kind)
    groups = build_groups(data.inventory.resources, kinds, data.usage, data.meta, profile, q)
    rows = [_group(g, data) for g in groups]
    rows.sort(key=lambda r: (-r["uses"], r["name"].lower()))
    return {"kind": kind, "total": len(rows), "unused": sum(1 for r in rows if not r["uses"]), "rows": rows}


def doctor(data: ResourceData, profile: str | None) -> dict[str, Any]:
    fs = [f for f in data.findings if profile in (None, f.profile)]
    return {
        "findings": [
            {"severity": f.severity, "code": f.code, "message": f.message, "profile": f.profile, "path": f.path}
            for f in fs
        ]
    }


def advice(data: ResourceData, profile: str | None) -> dict[str, Any]:
    items = run_advisor(data, profile=profile)
    return {
        "items": [
            {
                "id": a.id,
                "rule": a.rule,
                "severity": a.severity,
                "confidence": a.confidence,
                "title": a.title,
                "summary": a.summary,
                "profiles": a.profiles,
                "evidence": a.evidence,
                "files": a.files,
                "tokens_per_session": a.tokens_per_session,
                "tokens_per_month": a.tokens_per_month,
                "prompt": a.prompt,
            }
            for a in items
        ]
    }


def meta(data: ResourceData) -> dict[str, Any]:
    return {
        "profiles": [{"name": p.name, "alias": p.alias} for p in data.profiles],
        "unavailable": [{"name": p.name, "problem": p.problem} for p in data.unavailable],
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "sessions": len(data.sessions),
    }


# ------------------------------------------------------------------ toggles (the only write path here)


class ToggleError(Exception):
    pass


def _target(data: ResourceData, kind: str, name: str, profile: str) -> tuple[Profile, Any]:
    prof = next((p for p in data.profiles if p.name == profile), None)
    if prof is None:
        raise ToggleError(f"Unknown profile {profile!r}.")
    if kind == "mcp":
        return prof, st.set_mcp_enabled
    if kind == "plugin":
        return prof, st.set_plugin_enabled
    raise ToggleError(f"{kind} cannot be toggled.")


def toggle(data: ResourceData, kind: str, name: str, profile: str, enabled: bool, apply: bool) -> dict[str, Any]:
    """Dry run unless `apply`; applying passes the confirmation token the write layer requires."""
    prof, fn = _target(data, kind, name, profile)
    try:
        res = fn(prof, name, enabled, confirm=st.CONFIRM if apply else None, dry_run=not apply)
    except st.SettingsError as exc:
        raise ToggleError(str(exc)) from exc
    plan = res.plan
    return {
        "ok": res.ok,
        "applied": not res.dry_run and res.ok,
        "message": res.message,
        "changed": bool(plan and plan.changed),
        "diff": plan.diff if plan else "",
        "note": plan.note if plan else "",
        "path": plan.path if plan else "",
        "backup": res.backup,
    }
