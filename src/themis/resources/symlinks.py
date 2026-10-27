"""Promote a user skill, command or agent to a shared canonical directory (symlinks).

Write functions (rule 1): dry-run first, backup of everything replaced under ~/.themis/backups, audit
log entry, confirmation token. A drifted copy is never overwritten unless the caller names which side
wins (`prefer` = a profile name, or "shared" to keep the canonical version).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from themis import audit
from themis.config import Config
from themis.paths import themis_home
from themis.profiles.model import Profile
from themis.resources.drift import diff_resources
from themis.resources.fsutil import dir_hash, file_hash
from themis.resources.model import Resource

CONFIRM = "confirm"
KIND_DIR = {"skill": "skills", "command": "commands", "agent": "agents"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PromoteError(Exception):
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or [message]


@dataclass
class Step:
    op: str  # canonicalize | link | replace-copy | replace-link | keep
    profile: str
    path: str
    detail: str


@dataclass
class PromotePlan:
    kind: str
    name: str
    canonical: str
    steps: list[Step] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    needs_choice: bool = False
    choices: list[str] = field(default_factory=list)  # valid values for `prefer`
    diff: str = ""
    source_profile: str | None = None  # whose content becomes canonical when the shared copy is (re)written

    @property
    def ok(self) -> bool:
        return not self.refusals


@dataclass
class PromoteResult:
    ok: bool
    dry_run: bool
    message: str
    plan: PromotePlan


def shared_root(cfg: Config, resources: list[Resource]) -> Path:
    """Configured shared dir, else the root existing symlinks already point into, else ~/.themis/shared."""
    if cfg.shared_dir:
        return Path(cfg.shared_dir).expanduser()
    roots: Counter[Path] = Counter()
    for r in resources:
        if r.is_symlink and not r.broken and r.kind in KIND_DIR and r.origin == "user" and r.realpath:
            real = Path(r.realpath)
            roots[real.parent.parent] += 1
    return roots.most_common(1)[0][0] if roots else themis_home() / "shared"


def _entry_name(r: Resource) -> str:
    return r.extra.get("entry") or Path(r.path or "").name


def _entry_path(profile: Profile, kind: str, entry: str) -> Path:
    return profile.path / KIND_DIR[kind] / entry


def _hash(path: Path, kind: str) -> str | None:
    return dir_hash(path) if kind == "skill" else file_hash(path)


def _inside(path: Path, root: Path) -> bool:
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


def plan_promote(
    kind: str,
    name: str,
    targets: list[Profile],
    resources: list[Resource],
    cfg: Config,
    prefer: str | None = None,
) -> PromotePlan:
    if kind not in KIND_DIR:
        raise PromoteError(f"Cannot promote kind {kind!r}.")
    mine = [r for r in resources if r.kind == kind and r.name == name and r.origin == "user" and not r.broken]
    root = shared_root(cfg, resources)
    entries = {_entry_name(r) for r in mine}
    entry = next(iter(entries), name + (".md" if kind != "skill" else ""))
    canonical = root / KIND_DIR[kind] / entry
    plan = PromotePlan(kind, name, str(canonical))
    if len(entries) > 1:
        plan.refusals.append(f"{name!r} is stored under different names {sorted(entries)}; rename first.")
        return plan
    if not _SAFE_NAME.match(entry.removesuffix(".md")) or not mine:
        plan.refusals.append(f"No promotable user-level {kind} named {name!r}.")
        return plan
    if not targets:
        plan.refusals.append("No target profiles selected.")
        return plan

    holders = {r.profile: r for r in mine}
    for p in targets:
        r = holders.get(p.name)
        if r and Path(r.path or "").parent.parent != p.path / KIND_DIR[kind] and kind == "skill":
            plan.refusals.append(f"{p.name}: {name!r} is not a top-level entry of {KIND_DIR[kind]}/.")
        if r and kind != "skill" and Path(r.path or "").parent != p.path / KIND_DIR[kind]:
            plan.refusals.append(f"{p.name}: {name!r} is in a namespace subdirectory; not promotable.")
        if not _inside(p.path / KIND_DIR[kind], p.path) and (p.path / KIND_DIR[kind]).exists():
            plan.refusals.append(f"{p.name}: {KIND_DIR[kind]}/ resolves outside the profile directory.")
    if plan.refusals:
        return plan

    # decide the canonical content
    canon_exists = canonical.exists()
    canon_hash = _hash(canonical, kind) if canon_exists else None
    copies = {r.profile: r for r in mine if not (r.is_symlink and r.realpath and Path(r.realpath) == canonical)}
    source: Resource | None = None
    distinct = {r.content_hash for r in copies.values()} | ({canon_hash} if canon_hash else set())
    if prefer == "shared":
        if not canon_exists:
            plan.refusals.append("There is no shared version yet; prefer a profile instead.")
            return plan
    elif prefer:
        source = holders.get(prefer)
        if source is None:
            plan.refusals.append(f"Profile {prefer!r} has no {kind} named {name!r}.")
            return plan
    elif len(distinct) > 1:
        plan.needs_choice = True
        plan.choices = ["shared"] * canon_exists + sorted(copies)
        a = next(iter(copies.values()))
        others = [r for r in copies.values() if r.content_hash != a.content_hash]
        b = others[0] if others else None
        if b is not None:
            plan.diff = diff_resources(a, b)
        plan.refusals.append("The copies differ. Choose which side wins: " + ", ".join(plan.choices) + ".")
        return plan
    elif not canon_exists:
        source = next(iter(copies.values()))

    if source is not None:
        plan.source_profile = source.profile
        if canon_exists and canon_hash != source.content_hash:
            plan.steps.append(
                Step(
                    "canonicalize",
                    "shared",
                    str(canonical),
                    f"overwrite shared with {source.profile}'s version (old one backed up)",
                )
            )
        elif not canon_exists:
            plan.steps.append(
                Step(
                    "canonicalize", "shared", str(canonical), f"copy {source.profile}'s version to the shared directory"
                )
            )
        canon_hash = source.content_hash

    for p in targets:
        e = _entry_path(p, kind, entry)
        if e.is_symlink() and Path(os.path.realpath(e)) == Path(os.path.realpath(canonical)) and canonical.exists():
            plan.steps.append(Step("keep", p.name, str(e), "already linked to the shared copy"))
        elif e.is_symlink():
            plan.steps.append(Step("replace-link", p.name, str(e), f"re-point symlink (was -> {os.readlink(e)})"))
        elif e.exists():
            same = _hash(e, kind) == canon_hash
            plan.steps.append(
                Step(
                    "replace-copy",
                    p.name,
                    str(e),
                    "replace identical copy with a symlink"
                    if same
                    else "replace DIFFERING copy with a symlink (backed up first)",
                )
            )
        else:
            plan.steps.append(Step("link", p.name, str(e), "create symlink"))
    return plan


def _backup_root() -> Path:
    return themis_home() / "backups" / dt.datetime.now().strftime("promote-%Y%m%d-%H%M%S")


def _copy(src: Path, dst: Path, kind: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    real = Path(os.path.realpath(src))
    if kind == "skill":
        shutil.copytree(real, dst, symlinks=False, ignore=shutil.ignore_patterns("node_modules", ".git"))
    else:
        shutil.copy2(real, dst)


def promote(
    kind: str,
    name: str,
    targets: list[Profile],
    resources: list[Resource],
    cfg: Config,
    *,
    prefer: str | None = None,
    confirm: str | None = None,
    dry_run: bool = True,
) -> PromoteResult:
    plan = plan_promote(kind, name, targets, resources, cfg, prefer)
    if not plan.ok:
        if dry_run:
            return PromoteResult(False, True, plan.refusals[0], plan)
        raise PromoteError(plan.refusals[0], plan.refusals)
    if dry_run:
        return PromoteResult(True, True, f"Dry run: {len(plan.steps)} step(s), nothing written.", plan)
    if confirm != CONFIRM:
        raise PromoteError(f"Confirmation token mismatch (expected {CONFIRM!r}).")

    canonical = Path(plan.canonical)
    backups = _backup_root()
    holders = {r.profile: r for r in resources if r.kind == kind and r.name == name and r.origin == "user"}
    by_profile = {p.name: p for p in targets}
    done: list[dict[str, str]] = []
    for step in plan.steps:
        if step.op == "keep":
            continue
        if step.op == "canonicalize":
            if canonical.exists():
                dest = backups / "shared" / canonical.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(canonical), str(dest))
            src = holders[plan.source_profile or ""]
            _copy(Path(src.extra.get("dir") or src.path or ""), canonical, kind)
            done.append({"op": step.op, "path": str(canonical)})
            continue
        entry = Path(step.path)
        if entry.is_symlink():
            entry.unlink()
        elif entry.exists():
            dest = backups / step.profile / KIND_DIR[kind] / entry.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(dest))
        entry.parent.mkdir(parents=True, exist_ok=True)
        if not _inside(entry.parent, by_profile[step.profile].path):
            raise PromoteError(f"Refusing to write outside {step.profile}'s profile directory.")
        os.symlink(canonical, entry)
        done.append({"op": step.op, "path": str(entry)})
    audit.record(
        "resource.promote",
        kind=kind,
        name=name,
        canonical=str(canonical),
        steps=done,
        backups=str(backups) if backups.exists() else None,
    )
    return PromoteResult(True, False, f"Promoted {name!r}: {len(done)} change(s). Backups: {backups}", plan)
