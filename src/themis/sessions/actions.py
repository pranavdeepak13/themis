"""Write actions on sessions. Every write under a profile directory in Themis lives here (rule 1).

Each action takes a confirmation token, supports `dry_run`, and appends to the audit log. Delete
re-walks the session's file set at execution time and refuses anything outside the profile
directory, any symlink, and any file touched in the last two minutes. Transcripts are only ever
removed whole (delete, move), never edited.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import orjson

from themis import audit
from themis.config import Config
from themis.paths import archive_dir, cache_dir
from themis.profiles.model import Profile
from themis.sessions.files import session_files
from themis.sessions.model import SessionSummary

RECENT_SECONDS = 120
CONFIRM = "confirm"
_SAFE_ID = re.compile(r"^[0-9A-Za-z_-]{8,64}$")


class ActionError(Exception):
    """An action was refused or failed. `reasons` lists every cause."""

    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or [message]


@dataclass(frozen=True)
class PlannedFile:
    rel: str  # relative to the profile directory
    size: int
    mtime_ns: int


@dataclass
class FilePlan:
    profile: str
    session_id: str
    files: list[PlannedFile] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def ok(self) -> bool:
        return not self.refusals


@dataclass
class ActionResult:
    action: str
    ok: bool
    dry_run: bool
    message: str
    files: int = 0
    bytes: int = 0
    path: str | None = None
    reasons: list[str] = field(default_factory=list)


def delete_token(summary: SessionSummary) -> str:
    """What the user must type to confirm a delete or move: the first 8 characters of the id."""
    return summary.session_id[:8]


def _check_token(given: str | None, expected: str) -> None:
    if given != expected:
        raise ActionError(f"Confirmation token mismatch (expected {expected!r}).")


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


# ---------------------------------------------------------------------------------------------
# File set planning
# ---------------------------------------------------------------------------------------------


def plan_files(profile: Profile, summary: SessionSummary, *, now: float | None = None) -> FilePlan:
    """Fresh file set of the session with every safety refusal that applies to removing it."""
    plan = FilePlan(profile=profile.name, session_id=summary.session_id)
    if not _SAFE_ID.match(summary.session_id) or "/" in summary.project_dir or summary.project_dir in ("", ".", ".."):
        plan.refusals.append("Session id or project directory name is not a plain name.")
        return plan
    now = time.time() if now is None else now
    profile_real = Path(os.path.realpath(profile.path))
    project = profile.projects_dir / summary.project_dir
    found = session_files(profile.path, project, summary.session_id)
    if not found:
        plan.refusals.append("No files found for this session (already deleted?).")
        return plan
    for rf, mtime_ns in found:
        abs_path = profile.path / rf.path
        if abs_path.is_symlink() or not _is_inside(Path(os.path.realpath(abs_path)), profile_real):
            plan.refusals.append(f"Outside the profile directory or a symlink: {rf.path}")
            continue
        age = now - mtime_ns / 1e9
        if age < RECENT_SECONDS:
            plan.refusals.append(f"Modified {max(age, 0):.0f}s ago (under {RECENT_SECONDS}s): {rf.path}")
        plan.files.append(PlannedFile(rf.path, rf.size, mtime_ns))
    return plan


def _owned_dirs(profile: Profile, summary: SessionSummary) -> list[Path]:
    sid = summary.session_id
    return [
        profile.projects_dir / summary.project_dir / sid,
        profile.path / "file-history" / sid,
        profile.path / "session-env" / sid,
    ]


# ---------------------------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------------------------


def _delete_planned(profile: Profile, summary: SessionSummary, plan: FilePlan) -> None:
    profile_real = Path(os.path.realpath(profile.path))
    allowed = {f.rel: f for f in plan.files}
    for pf in plan.files:
        path = profile.path / pf.rel
        try:
            st = path.lstat()
        except FileNotFoundError:
            continue
        if pf.rel not in allowed or path.is_symlink() or not _is_inside(Path(os.path.realpath(path)), profile_real):
            raise ActionError(f"Refusing to delete {pf.rel}: not a plain file inside the profile.")
        if st.st_mtime_ns != pf.mtime_ns or st.st_size != pf.size:
            raise ActionError(f"{pf.rel} changed since the plan was made; nothing further was deleted.")
        path.unlink()
    for owned in _owned_dirs(profile, summary):
        if not owned.is_dir() or owned.is_symlink():
            continue
        for dirpath, _dirs, _files in os.walk(owned, topdown=False):
            with contextlib.suppress(OSError):
                os.rmdir(dirpath)
    (cache_dir() / profile.name / f"{summary.session_id}.json").unlink(missing_ok=True)


def delete_session(
    profile: Profile,
    summary: SessionSummary,
    *,
    confirm: str | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> ActionResult:
    """Permanently delete every file of one session."""
    plan = plan_files(profile, summary, now=now)
    if dry_run:
        return ActionResult(
            "delete",
            plan.ok,
            True,
            "Dry run: nothing deleted.",
            len(plan.files),
            plan.total_bytes,
            reasons=plan.refusals,
        )
    if not plan.ok:
        raise ActionError("Delete refused.", plan.refusals)
    _check_token(confirm, delete_token(summary))
    _delete_planned(profile, summary, plan)
    audit.record(
        "session.delete",
        profile=profile.name,
        session_id=summary.session_id,
        files=[f.rel for f in plan.files],
        bytes=plan.total_bytes,
    )
    return ActionResult("delete", True, False, "Deleted.", len(plan.files), plan.total_bytes)


# ---------------------------------------------------------------------------------------------
# Archive and restore
# ---------------------------------------------------------------------------------------------


@dataclass
class ArchiveEntry:
    profile: str
    session_id: str
    created: str
    topic: str | None
    cwd: str | None
    files: int
    bytes: int
    archive_path: str


def _archive_paths(profile: str, session_id: str) -> tuple[Path, Path]:
    base = archive_dir() / profile
    return base / f"{session_id}.tar.gz", base / f"{session_id}.manifest.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_session(
    profile: Profile, summary: SessionSummary, *, confirm: str | None = None, dry_run: bool = False
) -> ActionResult:
    """Compress the session file set into ~/.themis/archive/<profile>/. The originals stay."""
    plan = plan_files(profile, summary, now=0)  # recency does not matter for a copy
    refusals = [r for r in plan.refusals if not r.startswith("Modified")]
    tar_path, manifest_path = _archive_paths(profile.name, summary.session_id)
    if tar_path.exists():
        refusals.append(f"Already archived: {tar_path}")
    if dry_run:
        return ActionResult(
            "archive", not refusals, True, "Dry run: nothing written.", len(plan.files), plan.total_bytes,
            path=str(tar_path), reasons=refusals,
        )  # fmt: skip
    if refusals:
        raise ActionError("Archive refused.", refusals)
    _check_token(confirm, CONFIRM)
    manifest = {
        "version": 1,
        "created": dt.datetime.now(dt.UTC).isoformat(),
        "profile": profile.name,
        "profile_path": str(profile.path),
        "session_id": summary.session_id,
        "project_dir": summary.project_dir,
        "cwd": summary.cwd,
        "topic": summary.topic,
        "files": [
            {"path": f.rel, "size": f.size, "sha256": _sha256(profile.path / f.rel)} for f in plan.files
        ],
    }  # fmt: skip
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tar_path.with_suffix(f".tmp{os.getpid()}")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            for f in plan.files:
                tar.add(profile.path / f.rel, arcname=f.rel, recursive=False)
        with tarfile.open(tmp, "r:gz") as tar:
            names = {m.name: m.size for m in tar.getmembers()}
        for f in plan.files:
            if names.get(f.rel) != f.size:
                raise ActionError(f"Archive verification failed for {f.rel}.")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, tar_path)
    manifest_path.write_bytes(orjson.dumps(manifest, option=orjson.OPT_INDENT_2))
    audit.record(
        "session.archive",
        profile=profile.name,
        session_id=summary.session_id,
        archive=str(tar_path),
        files=len(plan.files),
        bytes=plan.total_bytes,
    )
    return ActionResult("archive", True, False, "Archived.", len(plan.files), plan.total_bytes, path=str(tar_path))


def list_archives(profile: str | None = None) -> list[ArchiveEntry]:
    out: list[ArchiveEntry] = []
    root = archive_dir()
    dirs = [root / profile] if profile else (sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else [])
    for d in dirs:
        for mf in sorted(d.glob("*.manifest.json")):
            try:
                m = orjson.loads(mf.read_bytes())
                tar_path = d / f"{m['session_id']}.tar.gz"
                if not tar_path.is_file():
                    continue
                out.append(
                    ArchiveEntry(
                        profile=m["profile"],
                        session_id=m["session_id"],
                        created=m["created"],
                        topic=m.get("topic"),
                        cwd=m.get("cwd"),
                        files=len(m["files"]),
                        bytes=sum(int(f["size"]) for f in m["files"]),
                        archive_path=str(tar_path),
                    )
                )
            except (OSError, orjson.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return out


def _safe_member(name: str, allowed: set[str]) -> bool:
    p = Path(name)
    return name in allowed and not p.is_absolute() and ".." not in p.parts


def restore_session(
    profile: Profile, session_id: str, *, confirm: str | None = None, dry_run: bool = False
) -> ActionResult:
    """Extract an archive back into its profile. Refuses to overwrite any existing file."""
    tar_path, manifest_path = _archive_paths(profile.name, session_id)
    try:
        manifest = orjson.loads(manifest_path.read_bytes())
        expected = {f["path"]: f for f in manifest["files"]}
    except (OSError, orjson.JSONDecodeError, KeyError, TypeError) as e:
        raise ActionError(f"No readable archive manifest for {session_id}: {e}") from e
    if not tar_path.is_file():
        raise ActionError(f"Archive file missing: {tar_path}")
    profile_real = Path(os.path.realpath(profile.path))
    refusals: list[str] = []
    for rel in expected:
        dest = profile.path / rel
        if not _safe_member(rel, set(expected)) or not _is_inside(Path(os.path.realpath(dest)), profile_real):
            refusals.append(f"Unsafe path in archive: {rel}")
        elif dest.exists() or dest.is_symlink():
            refusals.append(f"Already exists: {rel}")
    total = sum(int(f["size"]) for f in expected.values())
    if dry_run:
        return ActionResult(
            "restore", not refusals, True, "Dry run: nothing restored.", len(expected), total, reasons=refusals
        )
    if refusals:
        raise ActionError("Restore refused.", refusals)
    _check_token(confirm, CONFIRM)
    created: list[Path] = []
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar:
                if not member.isreg() or not _safe_member(member.name, set(expected)):
                    continue
                dest = profile.path / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                with src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
                created.append(dest)
                if dest.stat().st_size != int(expected[member.name]["size"]):
                    raise ActionError(f"Size mismatch restoring {member.name}.")
                if _sha256(dest) != expected[member.name]["sha256"]:
                    raise ActionError(f"Checksum mismatch restoring {member.name}.")
        if len(created) != len(expected):
            raise ActionError("Archive is missing files listed in its manifest.")
    except BaseException:
        for p in created:
            p.unlink(missing_ok=True)
        raise
    # Fresh mtimes: Claude Code's retention sweep goes by file age, and an old mtime would purge the
    # restored session again at the next start.
    now = time.time()
    for p in created:
        os.utime(p, (now, now))
    (cache_dir() / profile.name / f"{session_id}.json").unlink(missing_ok=True)
    audit.record(
        "session.restore",
        profile=profile.name,
        session_id=session_id,
        files=[str(p.relative_to(profile.path)) for p in created],
    )
    return ActionResult("restore", True, False, "Restored.", len(created), total)


# ---------------------------------------------------------------------------------------------
# Copy and move between profiles
# ---------------------------------------------------------------------------------------------


def _copy_plan(src: Profile, dst: Profile, summary: SessionSummary, plan: FilePlan) -> list[str]:
    refusals = [r for r in plan.refusals if not r.startswith("Modified")]
    if src.path == dst.path:
        refusals.append("Source and target profile are the same.")
    dst_real = Path(os.path.realpath(dst.path))
    for f in plan.files:
        dest = dst.path / f.rel
        if dest.exists() or dest.is_symlink():
            refusals.append(f"Already exists in {dst.name}: {f.rel}")
        elif not _is_inside(Path(os.path.realpath(dest)), dst_real):
            refusals.append(f"Target path escapes the profile: {f.rel}")
    return refusals


def _copy_files(src: Profile, dst: Profile, plan: FilePlan) -> list[Path]:
    created: list[Path] = []
    try:
        for f in plan.files:
            dest = dst.path / f.rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
            shutil.copyfile(src.path / f.rel, tmp)
            if tmp.stat().st_size != f.size:
                tmp.unlink(missing_ok=True)
                raise ActionError(f"Size changed while copying {f.rel}; copy aborted.")
            os.replace(tmp, dest)
            created.append(dest)
    except BaseException:
        for p in created:
            p.unlink(missing_ok=True)
        raise
    return created


def copy_session(
    src: Profile, dst: Profile, summary: SessionSummary, *, confirm: str | None = None, dry_run: bool = False
) -> ActionResult:
    """Copy the file set into another profile under the same encoded-cwd directory, so resume works there."""
    plan = plan_files(src, summary, now=0)
    refusals = _copy_plan(src, dst, summary, plan)
    if dry_run:
        return ActionResult(
            "copy", not refusals, True, "Dry run: nothing copied.", len(plan.files), plan.total_bytes, reasons=refusals
        )
    if refusals:
        raise ActionError("Copy refused.", refusals)
    _check_token(confirm, CONFIRM)
    created = _copy_files(src, dst, plan)
    now = time.time()  # see restore_session: keep the copy out of the retention sweep
    for p in created:
        os.utime(p, (now, now))
    audit.record(
        "session.copy",
        source=src.name,
        target=dst.name,
        session_id=summary.session_id,
        files=len(created),
        bytes=plan.total_bytes,
    )
    return ActionResult("copy", True, False, f"Copied to {dst.name}.", len(created), plan.total_bytes)


def move_session(
    src: Profile,
    dst: Profile,
    summary: SessionSummary,
    *,
    confirm: str | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> ActionResult:
    """Copy to the other profile, then delete the source. Needs the delete token."""
    plan = plan_files(src, summary, now=now)
    refusals = plan.refusals + [r for r in _copy_plan(src, dst, summary, plan) if r not in plan.refusals]
    if dry_run:
        return ActionResult(
            "move", not refusals, True, "Dry run: nothing moved.", len(plan.files), plan.total_bytes, reasons=refusals
        )
    if refusals:
        raise ActionError("Move refused.", refusals)
    _check_token(confirm, delete_token(summary))
    created = _copy_files(src, dst, plan)
    stamp = time.time()
    for p in created:
        os.utime(p, (stamp, stamp))
    try:
        _delete_planned(src, summary, plan)
    except ActionError as e:
        for p in created:  # the source is intact, so undo the copy and leave things as they were
            p.unlink(missing_ok=True)
        raise ActionError(f"Move aborted, copy rolled back: {e}") from e
    audit.record(
        "session.move",
        source=src.name,
        target=dst.name,
        session_id=summary.session_id,
        files=[f.rel for f in plan.files],
        bytes=plan.total_bytes,
    )
    return ActionResult("move", True, False, f"Moved to {dst.name}.", len(plan.files), plan.total_bytes)


# ---------------------------------------------------------------------------------------------
# Resume and bulk
# ---------------------------------------------------------------------------------------------


def cwd_exists(summary: SessionSummary) -> bool:
    return bool(summary.cwd) and Path(summary.cwd or "").is_dir()


def resume_command(profile: Profile, summary: SessionSummary) -> str:
    cwd = shlex.quote(summary.cwd) if summary.cwd else "."
    return f"cd {cwd} && CLAUDE_CONFIG_DIR={shlex.quote(str(profile.path))} claude --resume {summary.session_id}"


def launch_resume(command: str, cfg: Config) -> ActionResult:
    """Run the user's configured launcher template. Without one, the command is copy-only."""
    if not cfg.resume_launcher:
        raise ActionError("No resume_launcher configured in ~/.themis/config.toml; copy the command instead.")
    full = cfg.resume_launcher.replace("{command}", shlex.quote(command))
    subprocess.Popen(full, shell=True, start_new_session=True)  # noqa: S602 - user-configured template
    audit.record("session.resume_launch", launcher=cfg.resume_launcher)
    return ActionResult("resume", True, False, "Launcher started.")


def run_bulk(
    items: Iterable[tuple[Profile, SessionSummary]],
    action: Callable[[Profile, SessionSummary], ActionResult],
    progress: Callable[[int, int], None] | None = None,
) -> list[tuple[SessionSummary, ActionResult]]:
    """Apply one action to many sessions. A refusal on one session never stops the others."""
    todo = list(items)
    results: list[tuple[SessionSummary, ActionResult]] = []
    for i, (profile, summary) in enumerate(todo, 1):
        try:
            res = action(profile, summary)
        except ActionError as e:
            res = ActionResult("bulk", False, False, str(e), reasons=e.reasons)
        results.append((summary, res))
        if progress:
            progress(i, len(todo))
    return results
