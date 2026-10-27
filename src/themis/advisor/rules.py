"""Advisor rules. Each rule reads the loaded data and returns findings; none of them writes anything."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from themis.advisor.model import Advice
from themis.advisor.prompts import build_prompt
from themis.analytics import parse_when
from themis.config import AdvisorConfig
from themis.redact import redact_text
from themis.resources.fsutil import estimate_tokens, read_text
from themis.resources.model import Resource
from themis.resources.store import ResourceData
from themis.sessions.metrics import context_percent
from themis.sessions.model import SessionSummary

_WORDS = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "use",
        "when",
        "this",
        "that",
        "you",
        "your",
        "are",
        "from",
        "will",
        "can",
        "not",
        "has",
        "have",
        "any",
        "all",
        "into",
        "using",
        "used",
        "should",
        "must",
        "also",
        "such",
        "than",
        "then",
        "them",
        "they",
        "their",
        "about",
        "after",
        "before",
        "while",
        "where",
        "which",
        "what",
        "who",
        "how",
    }
)
SHORT = 12  # evidence lines per list before "and N more"


@dataclass
class Ctx:
    data: ResourceData
    now: datetime

    @property
    def cfg(self) -> AdvisorConfig:
        return self.data.cfg.advisor

    def sessions_of(self, profile: str) -> list[SessionSummary]:
        return [s for s in self.data.sessions if s.profile == profile]

    def recent(self, profile: str, days: int) -> list[SessionSummary]:
        cutoff = self.now - timedelta(days=days)
        out = []
        for s in self.sessions_of(profile):
            end = parse_when(s.ended_at or s.started_at)
            if end is not None and end >= cutoff:
                out.append(s)
        return out

    def cutoff_iso(self, days: int) -> str:
        return (self.now - timedelta(days=days)).isoformat()


def _conf(n_sessions: int) -> str:
    return "high" if n_sessions >= 30 else "medium" if n_sessions >= 10 else "low"


def _words(text: str | None) -> frozenset[str]:
    return frozenset(w for w in _WORDS.findall((text or "").lower()) if w not in _STOP)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _listing(items: list[str], limit: int = SHORT) -> list[str]:
    return items[:limit] + ([f"... and {len(items) - limit} more"] if len(items) > limit else [])


def _skill_like(c: Ctx, profile: str | None = None) -> list[Resource]:
    return [
        r
        for r in c.data.inventory.resources
        if r.kind == "skill" and r.origin != "project" and not r.broken and (profile is None or r.profile == profile)
    ]


def _used_since(c: Ctx, kind: str, name: str, profile: str, days: int) -> bool:
    cutoff = c.cutoff_iso(days)
    for k in [kind, "command"] if kind == "skill" else [kind]:
        st = c.data.usage.lookup(k, name, profile)
        last = st.last_by_profile.get(profile) or st.last_used
        if st.invocations and last and last >= cutoff:
            return True
    return False


# ------------------------------------------------------------------ 1. unused skills


def unused_skills(c: Ctx) -> list[Advice]:
    days = c.cfg.unused_days
    out: list[Advice] = []
    for p in c.data.profiles:
        window = c.recent(p.name, days)
        if not window:
            continue  # no sessions in the window: absence of use proves nothing
        monthly = len(c.recent(p.name, 30))
        groups: dict[str, list[Resource]] = defaultdict(list)
        for r in _skill_like(c, p.name):
            if r.enabled is False or _used_since(c, "skill", r.name, p.name, days):
                continue
            groups[r.origin_name or ("synced" if r.origin == "synced" else "user")].append(r)
        for group, skills in sorted(groups.items()):
            tokens = sum(r.tokens_startup for r in skills)
            if tokens < 60:
                continue
            skills.sort(key=lambda r: -r.tokens_startup)
            plugin = skills[0].origin == "plugin"
            out.append(
                Advice(
                    rule="unused-skills",
                    severity="medium" if tokens >= 1500 else "low",
                    confidence=_conf(len(window)),
                    title=f"{len(skills)} skill(s) from {group} unused for {days}+ days in {p.name}",
                    summary=f"Their names and descriptions are listed at every session start (about {tokens} tokens).",
                    profiles=[p.name],
                    files=sorted({str(p.path / "settings.json")} if plugin else {r.path for r in skills if r.path}),
                    evidence=[
                        f"{len(window)} session(s) in {p.name} in the last {days} days; none used these skills",
                        f"startup cost of the group: about {tokens} tokens (estimate)",
                        *_listing([f"{r.name}: ~{r.tokens_startup} tokens" for r in skills]),
                        "Use is judged from Skill tool calls and typed slash commands only.",
                    ],
                    goal=(
                        'Disable the plugin or set unused skills to "off" in skillOverrides in settings.json.'
                        if plugin
                        else 'Remove or disable the unused skills (set them to "off" in skillOverrides).'
                    ),
                    tokens_per_session=tokens,
                    tokens_per_month=tokens * monthly,
                )
            )
    return out


# ------------------------------------------------------------------ 2. unused MCP servers


def unused_mcp(c: Ctx) -> list[Advice]:
    days = c.cfg.unused_days
    out: list[Advice] = []
    for p in c.data.profiles:
        window = c.recent(p.name, days)
        if not window:
            continue
        idle = [
            r
            for r in c.data.inventory.resources
            if r.kind == "mcp"
            and r.profile == p.name
            and r.enabled is not False
            and not _used_since(c, "mcp", r.name, p.name, days)
        ]
        if not idle:
            continue
        files = sorted({r.path for r in idle if r.path})
        out.append(
            Advice(
                rule="unused-mcp",
                severity="low",
                confidence=_conf(len(window)),
                title=f"{len(idle)} MCP server(s) never used in {days} days in {p.name}",
                summary="Enabled servers connect at startup and register tools even when nothing calls them.",
                profiles=[p.name],
                files=files,
                evidence=[
                    f"{len(window)} session(s) in the last {days} days; no calls to these servers",
                    *_listing([f"{r.name} ({r.origin}{':' + r.origin_name if r.origin_name else ''})" for r in idle]),
                    "Tool schema size is not recorded locally, so the token saving is not measurable here.",
                ],
                goal=(
                    "Disable these servers: user servers through disabledMcpServers in .claude.json, plugin "
                    "servers by disabling the plugin."
                ),
            )
        )
    return out


# ------------------------------------------------------------------ 3. startup overhead


def startup_overhead(c: Ctx) -> list[Advice]:
    out: list[Advice] = []
    for p in c.data.profiles:
        items = [
            r
            for r in c.data.inventory.resources
            if r.profile == p.name
            and r.enabled is not False
            and not r.broken
            and (
                (r.kind in ("skill", "agent", "command") and r.origin != "project")
                or (r.kind == "claude_md" and r.origin == "user")
            )
        ]
        parts: dict[str, int] = defaultdict(int)
        for r in items:
            bucket = (
                f"plugin {r.origin_name}"
                if r.origin == "plugin"
                else ("CLAUDE.md" if r.kind == "claude_md" else f"{r.kind}s ({r.origin})")
            )
            parts[bucket] += r.tokens_startup
        total = sum(parts.values())
        if not total:
            continue
        top = sorted(items, key=lambda r: -r.tokens_startup)[:10]
        ranked = sorted(parts.items(), key=lambda kv: -kv[1])
        warn = c.cfg.startup_overhead_warn_tokens
        out.append(
            Advice(
                rule="startup-overhead",
                severity="medium" if total >= warn else "info",
                confidence="medium",
                title=f"Startup context overhead of {p.name}: about {total} tokens",
                summary="Skill/agent/command names and descriptions plus user CLAUDE.md, before any prompt.",
                profiles=[p.name],
                files=[str(p.path)],
                evidence=[
                    "Breakdown (estimate, 4 characters per token):",
                    *[f"  {name}: ~{n} tokens ({n * 100 // total}%)" for name, n in ranked[:12]],
                    "Largest single contributors:",
                    *[f"  {r.kind} {r.name} ({r.origin}): ~{r.tokens_startup}" for r in top],
                    "MCP tool schemas and project CLAUDE.md files are not included (not measurable here).",
                    "Claude Code caps the skill listing (skillListingBudgetFraction, default 1% of the window), "
                    "so the real cost can be lower than this sum.",
                ],
                goal="Cut the startup context by trimming or disabling the largest contributors I do not need.",
            )
        )
    return out


# ------------------------------------------------------------------ 4. duplicates


def duplicates(c: Ctx) -> list[Advice]:
    out: list[Advice] = []
    copies: dict[tuple[str, str], list[Resource]] = defaultdict(list)
    for r in c.data.inventory.resources:  # app-synced skills are managed by the desktop app and re-synced
        if r.identical_copy and r.origin == "user" and r.kind in ("skill", "command", "agent"):
            copies[(r.kind, r.name)].append(r)
    if copies:
        out.append(
            Advice(
                rule="identical-copies",
                severity="low",
                confidence="high",
                title=f"{len(copies)} resource(s) are identical copies that could be symlinks",
                summary="Identical content stored separately drifts apart over time; a symlink keeps one source.",
                profiles=sorted({r.profile for rs in copies.values() for r in rs}),
                files=sorted({r.path for rs in copies.values() for r in rs if r.path}),
                evidence=_listing(
                    [
                        f"{kind} {name}: {', '.join(sorted({r.profile for r in rs}))}"
                        for (kind, name), rs in sorted(copies.items())
                    ]
                ),
            )
        )
    skills = [r for r in _skill_like(c) if r.description]
    by_name: dict[str, Resource] = {}
    for r in skills:
        by_name.setdefault(r.name, r)
    uniq = list(by_name.values())
    words = {r.name: _words(r.description) for r in uniq}
    pairs: list[tuple[float, Resource, Resource]] = []
    for i, a in enumerate(uniq):
        for b in uniq[i + 1 :]:
            same_hash = a.content_hash and a.content_hash == b.content_hash
            sim = 1.0 if same_hash else _jaccard(words[a.name], words[b.name])
            if sim >= c.cfg.near_duplicate:
                pairs.append((sim, a, b))
    pairs.sort(key=lambda t: -t[0])
    if pairs:
        profiles = sorted({r.profile for _, a, b in pairs for r in (a, b)})
        out.append(
            Advice(
                rule="near-duplicate",
                severity="low",
                confidence="medium",
                title=f"{len(pairs)} near-duplicate skill pair(s) with different names",
                summary="Skills whose descriptions are almost identical compete to trigger and cost startup tokens.",
                profiles=profiles,
                files=sorted({r.path for _, a, b in pairs[:20] for r in (a, b) if r.path}),
                evidence=_listing(
                    [f"{a.name} ({a.origin}) ~ {b.name} ({b.origin}): {sim:.0%} similar" for sim, a, b in pairs], 20
                ),
                tokens_per_session=sum(min(a.tokens_startup, b.tokens_startup) for _, a, b in pairs),
            )
        )
    return out


# ------------------------------------------------------------------ 5. drift


def synced_note(rs: list[Resource]) -> str:
    return (
        " These are app-synced copies: re-sync from claude.ai rather than editing." if rs[0].origin == "synced" else ""
    )


def drift(c: Ctx) -> list[Advice]:
    groups: dict[tuple[str, str], list[Resource]] = defaultdict(list)
    for r in c.data.inventory.resources:
        if r.drifted and r.kind in ("skill", "command", "agent"):
            groups[(r.kind, r.name)].append(r)
    out: list[Advice] = []
    for (kind, name), rs in sorted(groups.items()):
        by_hash: dict[str, list[Resource]] = defaultdict(list)
        for r in rs:
            by_hash[r.content_hash or "?"].append(r)
        out.append(
            Advice(
                rule="drift",
                severity="medium",
                confidence="high",
                title=f"{kind} {name!r} has {len(by_hash)} different versions across profiles",
                summary="Profiles that should share this resource hold different content."
                + (
                    " These are app-synced copies: re-sync from claude.ai rather than editing."
                    if rs[0].origin == "synced"
                    else ""
                ),
                profiles=sorted({r.profile for r in rs}),
                files=sorted({r.path for r in rs if r.path}),
                evidence=[
                    f"version {h[:8]}: {', '.join(sorted({r.profile for r in v}))} ({v[0].path})"
                    for h, v in sorted(by_hash.items())
                ],
            )
        )
    return out


# ------------------------------------------------------------------ 6. CLAUDE.md


def _paragraphs(text: str) -> dict[str, str]:
    out = {}
    for block in re.split(r"\n\s*\n", text):
        norm = " ".join(block.split())
        if len(norm) >= 60:
            out[norm.lower()] = norm
    return out


def claude_md(c: Ctx) -> list[Advice]:
    files = [r for r in c.data.inventory.resources if r.kind == "claude_md" and not r.broken and r.path]
    out: list[Advice] = []
    limit = c.cfg.claude_md_max_tokens
    for r in files:
        if r.tokens_full > limit:
            out.append(
                Advice(
                    rule="claude-md",
                    severity="medium" if r.origin == "user" else "low",
                    confidence="high",
                    title=f"{r.name} is large: about {r.tokens_full} tokens ({r.origin})",
                    summary="CLAUDE.md is loaded whole into every session that uses it.",
                    profiles=[r.profile],
                    files=[r.path or ""],
                    evidence=[f"~{r.tokens_full} tokens vs the {limit} advisory limit (estimate)"],
                    tokens_per_session=r.tokens_full - limit,
                )
            )
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # paragraph -> realpath -> profiles
    text_of: dict[str, str] = {}
    for r in files:
        body = read_text(Path(r.path or ""))
        for key, norm in _paragraphs(body or "").items():
            seen[key][r.realpath or r.path or ""].add(r.profile)
            text_of[key] = norm
    repeated = {k: v for k, v in seen.items() if len(v) >= 2}
    if repeated:
        saved = sum(estimate_tokens(text_of[k]) * (len(v) - 1) for k, v in repeated.items())
        profiles = sorted({p for v in repeated.values() for ps in v.values() for p in ps})
        paths = sorted({path for v in repeated.values() for path in v})
        out.append(
            Advice(
                rule="claude-md",
                severity="low",
                confidence="medium",
                title=f"{len(repeated)} paragraph(s) repeated across CLAUDE.md files",
                summary="The same instructions appear in more than one CLAUDE.md.",
                profiles=profiles,
                files=paths,
                evidence=_listing([f"in {len(v)} files: {text_of[k][:70]}..." for k, v in sorted(repeated.items())], 8),
                tokens_per_session=saved,
            )
        )
    return out


# ------------------------------------------------------------------ 7. high context without compaction


def high_context(c: Ctx) -> list[Advice]:
    out: list[Advice] = []
    for p in c.data.profiles:
        hot = [
            (context_percent(s, c.data.cfg, peak=True), s)
            for s in c.sessions_of(p.name)
            if s.compactions == 0 and context_percent(s, c.data.cfg, peak=True) >= c.cfg.high_context_percent
        ]
        if not hot:
            continue
        hot.sort(key=lambda t: -t[0])
        out.append(
            Advice(
                rule="high-context",
                severity="medium" if len(hot) >= 3 else "low",
                confidence="medium",
                title=f"{len(hot)} session(s) in {p.name} passed {c.cfg.high_context_percent:.0f}% context uncompacted",
                summary="Long uncompacted sessions re-send a very large context every turn and risk losing detail.",
                profiles=[p.name],
                files=[str(p.path / s.transcript) for _, s in hot[:SHORT]],
                evidence=[
                    *_listing([f"{s.session_id[:8]} {s.cwd or s.project_dir}: peak {pct:.0f}%" for pct, s in hot]),
                    "The window is inferred (200k unless the session exceeded it), so a 1M-window session that "
                    "stayed under 200k can look fuller than it was.",
                ],
            )
        )
    return out


# ------------------------------------------------------------------ 8. low cache hit rate


def _cache_pattern(s: SessionSummary) -> str:
    gap = (s.duration_s or 0) / max(s.assistant_messages, 1)
    if gap > 300:
        return "long gaps between replies (over 5 minutes on average), so a 5-minute cache can expire"
    if s.compactions:
        return "compaction rewrote the cached prefix"
    if s.assistant_messages <= 3:
        return "very short session: the first request writes the cache and little follows"
    return "no clear pattern in the summary data"


def low_cache(c: Ctx) -> list[Advice]:
    out: list[Advice] = []
    for p in c.data.profiles:
        bad = []
        for s in c.sessions_of(p.name):
            denom = s.tokens.input + s.tokens.cache_read + s.tokens.cache_creation
            rate = s.tokens.cache_hit_rate
            if rate is not None and denom >= c.cfg.min_cache_tokens and rate < c.cfg.low_cache_hit_rate:
                bad.append((s, rate, denom))
        if not bad:
            continue
        patterns: dict[str, int] = defaultdict(int)
        for s, _, _ in bad:
            patterns[_cache_pattern(s)] += 1
        avoidable = [max(0, int(0.8 * d) - s.tokens.cache_read) for s, _, d in bad]
        recent = {s.session_id for s in c.recent(p.name, 30)}
        out.append(
            Advice(
                rule="low-cache",
                severity="low",
                confidence="low",
                title=f"{len(bad)} session(s) in {p.name} with a cache hit rate under {c.cfg.low_cache_hit_rate:.0%}",
                summary="Input that could have been read from the prompt cache was processed again.",
                profiles=[p.name],
                files=[str(p.path / s.transcript) for s, _, _ in bad[:SHORT]],
                evidence=[
                    *_listing([f"{s.session_id[:8]}: hit rate {r:.0%} of ~{d} input-side tokens" for s, r, d in bad]),
                    "Patterns seen:",
                    *[f"  {n}x {text}" for text, n in sorted(patterns.items(), key=lambda kv: -kv[1])],
                    "Avoidable tokens are a rough estimate against an 80% target hit rate.",
                ],
                tokens_per_session=sum(avoidable) // len(bad),
                tokens_per_month=sum(a for (s, _, _), a in zip(bad, avoidable, strict=True) if s.session_id in recent),
            )
        )
    return out


# ------------------------------------------------------------------ 9. descriptions


def descriptions(c: Ctx) -> list[Advice]:
    out: list[Advice] = []
    limit = c.cfg.skill_desc_max_chars
    skills = _skill_like(c)
    long: dict[str, list[Resource]] = defaultdict(list)
    for r in skills:
        if r.description and len(r.description) > limit:
            long[f"{r.name}|{r.content_hash}"].append(r)
    if long:
        firsts = sorted((rs[0] for rs in long.values()), key=lambda r: -len(r.description or ""))
        saved = sum(estimate_tokens((r.description or "")[limit:]) for r in firsts)
        out.append(
            Advice(
                rule="long-description",
                severity="low",
                confidence="high",
                title=f"{len(firsts)} skill description(s) longer than {limit} characters",
                summary="Descriptions are listed at every session start; long ones cost tokens and blur triggers.",
                profiles=sorted({r.profile for rs in long.values() for r in rs}),
                files=sorted({r.path for r in firsts if r.path}),
                evidence=_listing([f"{r.name} ({r.origin}): {len(r.description or '')} characters" for r in firsts]),
                tokens_per_session=saved,
            )
        )
    by_name: dict[str, Resource] = {}
    for r in skills:
        if r.description:
            by_name.setdefault(r.name, r)
    uniq = list(by_name.values())
    words = {r.name: _words(r.description) for r in uniq}
    pairs = []
    for i, a in enumerate(uniq):
        for b in uniq[i + 1 :]:
            sim = _jaccard(words[a.name], words[b.name])
            if c.cfg.description_overlap <= sim < c.cfg.near_duplicate:
                pairs.append((sim, a, b))
    pairs.sort(key=lambda t: -t[0])
    if pairs:
        out.append(
            Advice(
                rule="overlap",
                severity="low",
                confidence="medium",
                title=f"{len(pairs)} pair(s) of skills with overlapping trigger wording",
                summary="Skills described in similar words can load for the same request.",
                profiles=sorted({r.profile for _, a, b in pairs for r in (a, b)}),
                files=sorted({r.path for _, a, b in pairs[:20] for r in (a, b) if r.path}),
                evidence=_listing([f"{a.name} ~ {b.name}: {sim:.0%} shared wording" for sim, a, b in pairs], 20),
            )
        )
    return out


RULES: list[Callable[[Ctx], list[Advice]]] = [
    unused_skills,
    unused_mcp,
    startup_overhead,
    duplicates,
    drift,
    claude_md,
    high_context,
    low_cache,
    descriptions,
]
_ORDER = {s: i for i, s in enumerate(("high", "medium", "low", "info"))}


def run_advisor(data: ResourceData, now: datetime | None = None, profile: str | None = None) -> list[Advice]:
    """All findings, most severe first. Pure: reads `data`, writes nothing."""
    ctx = Ctx(data, now or datetime.now(UTC))
    profiles = {p.name: p for p in data.profiles}
    findings: list[Advice] = []
    for rule in RULES:
        findings += rule(ctx)
    if profile:
        findings = [f for f in findings if profile in f.profiles]
    for f in findings:
        f.evidence = [redact_text(e) for e in f.evidence]  # evidence can quote config text
        f.title, f.summary = redact_text(f.title), redact_text(f.summary)
        f.prompt = build_prompt(f, profiles)
    findings.sort(
        key=lambda f: (_ORDER.get(f.severity, 9), -(f.tokens_per_month or f.tokens_per_session or 0), f.title)
    )
    return findings
