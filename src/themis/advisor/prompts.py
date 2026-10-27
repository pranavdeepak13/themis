"""Self-contained Claude Code prompts for advisor findings."""

from __future__ import annotations

from themis.advisor.model import Advice
from themis.profiles.model import Profile
from themis.redact import redact_secrets

MAX_FILES = 40
MAX_EVIDENCE = 25

GOALS = {
    "unused-skills": "Reduce the context loaded at session start by disabling or removing the unused skills below, "
    "without losing anything I still use.",
    "unused-mcp": "Stop loading the MCP servers below that I have not used in the review window, in the least "
    "invasive way (disable rather than delete).",
    "startup-overhead": "Cut the startup context of this profile by trimming or disabling the largest contributors "
    "listed below that I do not need.",
    "identical-copies": "Replace the identical, separately stored copies below with symlinks to one canonical copy.",
    "near-duplicate": "Merge or remove the near-duplicate skills below so one clear skill remains for each purpose.",
    "drift": "Reconcile the drifted copies below into one version and keep the other profiles in sync.",
    "claude-md": "Shorten the CLAUDE.md files below and remove content repeated across files, keeping every rule "
    "that changes behaviour.",
    "high-context": "Work out why these sessions ran near the context limit without compacting, and suggest "
    "habits or settings (for example compaction) that avoid it.",
    "low-cache": "Work out why prompt-cache hit rates were low in the sessions below and what I can change.",
    "long-description": "Shorten the skill descriptions below while keeping their trigger wording distinct.",
    "overlap": "Make the trigger wording of the overlapping skills below distinct so the right one loads.",
}


def build_prompt(a: Advice, profiles: dict[str, Profile]) -> str:
    lines = [
        "I want to improve my Claude Code setup. This finding comes from Themis, a read-only analyser of my local "
        "Claude Code profiles.",
        "",
        f"Finding: {a.title}  (rule {a.rule}, severity {a.severity}, confidence {a.confidence})",
        "",
        "Profile(s) and exact config directories:",
    ]
    for name in a.profiles:
        p = profiles.get(name)
        lines.append(f"- {name}: CLAUDE_CONFIG_DIR={p.path if p else '(unknown)'}")
    lines += ["", "Evidence from Themis:"]
    lines += [f"- {e}" for e in a.evidence[:MAX_EVIDENCE]]
    if len(a.evidence) > MAX_EVIDENCE:
        lines.append(f"- ... and {len(a.evidence) - MAX_EVIDENCE} more")
    if a.tokens_per_session:
        lines.append(
            f"- Estimated saving: about {a.tokens_per_session} tokens per session"
            + (f", about {a.tokens_per_month} per month" if a.tokens_per_month else "")
            + " (rough estimate, 4 characters per token)"
        )
    if a.files:
        lines += ["", "Files involved:"]
        lines += [f"- {f}" for f in a.files[:MAX_FILES]]
        if len(a.files) > MAX_FILES:
            lines.append(f"- ... and {len(a.files) - MAX_FILES} more")
    lines += [
        "",
        f"Goal: {a.goal or GOALS.get(a.rule, a.summary)}",
        "",
        "How to proceed:",
        "1. Read the files above (and only what you need beyond them) and check the evidence still holds.",
        "2. Propose the smallest change that achieves the goal. Show it as a diff first and do not apply it yet.",
        "3. Ask me for explicit confirmation before you modify anything outside this project directory or "
        "anything inside a ~/.claude* directory. Everything listed above lives in a ~/.claude* directory, so "
        "always ask first.",
        "4. Make backups of any file you edit, and never print secret values (MCP env and headers can hold tokens).",
    ]
    return redact_secrets("\n".join(lines))
