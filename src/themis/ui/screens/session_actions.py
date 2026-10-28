"""Confirmation flows for session actions. Every write goes through themis.sessions (rule 1, rule 8)."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.containers import Vertical
from textual.widgets import Button

from themis import clipboard
from themis.config import Config
from themis.format import human_bytes
from themis.profiles.model import Profile
from themis.sessions import actions, meta
from themis.sessions.actions import CONFIRM, ActionError, ActionResult, FilePlan
from themis.sessions.export import export_session
from themis.sessions.model import SessionSummary
from themis.sessions.query import Row
from themis.ui.widgets.modals import ArchivesModal, ChoiceModal, ConfirmModal, ResumeModal, TextPromptModal

FILES_LISTED = 40
Pair = tuple[Profile, SessionSummary]


def _files_table(plan: FilePlan, limit: int = FILES_LISTED) -> Table:
    t = Table("file", "size", box=None, pad_edge=False)
    for f in sorted(plan.files, key=lambda f: -f.size)[:limit]:
        t.add_row(f.rel, Text(human_bytes(f.size), justify="right"))
    if len(plan.files) > limit:
        t.add_row(Text(f"+{len(plan.files) - limit} more files", style="dim"), "")
    return t


def _reasons(reasons: list[str], style: str = "bold") -> Group:
    return Group(*[Text(f"✗ {r}", style=style) for r in reasons[:12]])


class SessionActions(Vertical):
    """Mixin-style base for SessionsPane: action flows, each ending in a core call."""

    cfg: Config
    profiles: dict[str, Profile]
    all_rows: list[Row]
    at_risk_rows: list[Row]
    marked: set[str]

    # Provided by SessionsPane
    def current_row(self) -> Row | None: ...
    def reload(self, rebuild: bool = False) -> None: ...
    def apply_meta(self) -> None: ...
    def action_toggle_mark(self) -> None: ...
    def action_mark_all(self) -> None: ...
    def action_clear_marks(self) -> None: ...

    # ------------------------------------------------------------------ helpers

    def targets(self) -> list[Row]:
        """Marked sessions if any are marked, otherwise the one under the cursor."""
        if self.marked:
            return [r for r in self.all_rows if r.key in self.marked]
        row = self.current_row()
        return [row] if row else []

    def _pair(self, row: Row) -> Pair | None:
        profile = self.profiles.get(row.summary.profile)
        return (profile, row.summary) if profile else None

    def _pairs(self, rows: list[Row]) -> list[Pair]:
        return [p for r in rows if (p := self._pair(r))]

    def _note(self, message: str, severity: str = "information") -> None:
        self.app.notify(message, severity=severity, timeout=6)  # type: ignore[arg-type]

    def _none_selected(self) -> None:
        self._note("Select a session first.", "warning")

    # ------------------------------------------------------------------ workers

    @work(thread=True, group="action")
    def _run_bulk(
        self,
        label: str,
        pairs: list[Pair],
        fn: Callable[[Profile, SessionSummary], ActionResult],
        done: Callable[[list[ActionResult]], None] | None = None,
    ) -> None:
        call = self.app.call_from_thread
        results = actions.run_bulk(
            pairs, fn, progress=lambda d, t: call(self._note, f"{label}: {d}/{t}") if t > 1 else None
        )
        call(self._after_bulk, label, [r for _, r in results], done)

    def _after_bulk(
        self, label: str, results: list[ActionResult], done: Callable[[list[ActionResult]], None] | None
    ) -> None:
        good = [r for r in results if r.ok]
        bad = [r for r in results if not r.ok]
        size = human_bytes(sum(r.bytes for r in good))
        if good:
            self._note(f"{label}: {len(good)} session(s) done, {size}.")
        if bad:
            self._note(
                f"{label}: {len(bad)} failed. {bad[0].message} {bad[0].reasons[0] if bad[0].reasons else ''}", "error"
            )
        self.marked.clear()
        self.reload()
        if done:
            done(results)

    @work(thread=True, group="action")
    def _run_one(self, label: str, fn: Callable[[], ActionResult]) -> None:
        call = self.app.call_from_thread
        try:
            res = fn()
        except ActionError as e:
            call(self._note, f"{label} refused: {'; '.join(e.reasons[:3])}", "error")
            return
        except OSError as e:
            call(self._note, f"{label} failed: {e}", "error")
            return
        call(self._note, f"{label}: {res.message}" + (f" {res.path}" if res.path else ""))
        call(self.reload)

    # ------------------------------------------------------------------ resume

    def do_resume(self) -> None:
        row = self.current_row()
        pair = self._pair(row) if row else None
        if not row or not pair:
            return self._none_selected()
        command = actions.resume_command(*pair)

        def handle(choice: str | None) -> None:
            if choice == "copy":
                ok = clipboard.copy_text(command)
                self.app.copy_to_clipboard(command)
                self._note("Resume command copied." if ok else "Resume command sent to the terminal clipboard.")
            elif choice == "launch":
                try:
                    actions.launch_resume(command, self.cfg)
                    self._note("Opened in your configured terminal.")
                except ActionError as e:
                    self._note(str(e), "error")

        self.app.push_screen(
            ResumeModal(
                command, can_launch=bool(self.cfg.resume_launcher), cwd_missing=not actions.cwd_exists(row.summary)
            ),
            handle,
        )

    # ------------------------------------------------------------------ delete

    def do_delete(self, rows: list[Row] | None = None, title: str = "Delete permanently") -> None:
        rows = rows or self.targets()
        if not rows:
            return self._none_selected()
        pairs = self._pairs(rows)
        plans = [(p, actions.plan_files(*p)) for p in pairs]
        ok = [(p, plan) for p, plan in plans if plan.ok]
        blocked = [(p, plan) for p, plan in plans if not plan.ok]
        parts: list[RenderableType] = []
        if len(plans) == 1:
            (_, summary), plan = plans[0]
            parts.append(
                Text(
                    f"{summary.profile}/{summary.session_id}\n{len(plan.files)} files, {human_bytes(plan.total_bytes)}"
                )
            )
            parts.append(_files_table(plan))
        else:
            parts.append(
                Text(
                    f"{len(ok)} session(s), {sum(len(pl.files) for _, pl in ok)} files, "
                    f"{human_bytes(sum(pl.total_bytes for _, pl in ok))}"
                )
            )
            for (_, summary), plan in ok[:25]:
                parts.append(
                    Text(
                        f"  {summary.profile}/{summary.session_id[:8]}  "
                        f"{len(plan.files)} files  {human_bytes(plan.total_bytes)}"
                    )
                )
        for (_, summary), plan in blocked:
            parts.append(Text(f"\nRefused {summary.profile}/{summary.session_id[:8]}:", style="bold reverse"))
            parts.append(_reasons(plan.refusals))
        parts.append(Text("\nThis cannot be undone. Archive first if you may want it back.", style="bold"))
        single = len(ok) == 1 and len(plans) == 1
        token = ok[0][0][1].session_id[:8] if single else f"delete {len(ok)}"
        modal = ConfirmModal(
            title,
            Group(*parts),
            token=token,
            danger=True,
            confirm_label=f"Delete {len(ok)} session(s)",
            blocked=not ok,
        )

        def handle(result: tuple[bool, bool] | None) -> None:
            if result and result[0] and ok:
                self._run_bulk(
                    "Delete",
                    [p for p, _ in ok],
                    lambda prof, s: actions.delete_session(prof, s, confirm=actions.delete_token(s)),
                )

        self.app.push_screen(modal, handle)

    # ------------------------------------------------------------------ archive and restore

    def do_archive(self, rows: list[Row] | None = None) -> None:
        rows = rows or self.targets()
        if not rows:
            return self._none_selected()
        dry = [(p, actions.archive_session(*p, dry_run=True)) for p in self._pairs(rows)]
        ok = [(p, r) for p, r in dry if r.ok]
        bad = [(p, r) for p, r in dry if not r.ok]
        parts: list[RenderableType] = [
            Text(
                f"{len(ok)} session(s), {sum(r.files for _, r in ok)} files, "
                f"{human_bytes(sum(r.bytes for _, r in ok))} before compression."
            ),
            Text("Written to ~/.themis/archive/<profile>/ with a manifest. The originals are not deleted."),
        ]
        for (_, s), r in bad:
            parts += [Text(f"\n{s.profile}/{s.session_id[:8]}:", style="bold reverse"), _reasons(r.reasons)]
        single_row = rows[0] if len(ok) == 1 and len(rows) == 1 else None

        def handle(result: tuple[bool, bool] | None) -> None:
            if not (result and result[0] and ok):
                return

            def done(res: list[ActionResult]) -> None:
                if single_row and res and res[0].ok:
                    self.do_delete([single_row], "Archived. Delete the original files too?")

            self._run_bulk(
                "Archive", [p for p, _ in ok], lambda prof, s: actions.archive_session(prof, s, confirm=CONFIRM), done
            )

        self.app.push_screen(
            ConfirmModal("Archive", Group(*parts), confirm_label=f"Archive {len(ok)} session(s)", blocked=not ok),
            handle,
        )

    def do_archive_at_risk(self) -> None:
        self.do_archive(list(self.at_risk_rows))

    def do_restore(self) -> None:
        entries = actions.list_archives()

        def picked(entry: actions.ArchiveEntry | None) -> None:
            profile = self.profiles.get(entry.profile) if entry else None
            if not entry or not profile:
                return
            dry = actions.restore_session(profile, entry.session_id, dry_run=True)
            parts: list[RenderableType] = [
                Text(f"{dry.files} files, {human_bytes(dry.bytes)} back into {profile.name}.")
            ]
            if dry.reasons:
                parts.append(_reasons(dry.reasons))

            def confirmed(result: tuple[bool, bool] | None) -> None:
                if result and result[0]:
                    self._run_one(
                        "Restore", lambda: actions.restore_session(profile, entry.session_id, confirm=CONFIRM)
                    )

            self.app.push_screen(
                ConfirmModal("Restore archive", Group(*parts), confirm_label="Restore", blocked=not dry.ok), confirmed
            )

        self.app.push_screen(ArchivesModal(entries), picked)

    # ------------------------------------------------------------------ rename, tag, export

    def do_rename(self) -> None:
        row = self.current_row()
        if not row:
            return self._none_selected()

        def handle(value: str | None) -> None:
            if value is not None:
                meta.set_title(row.summary.profile, row.summary.session_id, value)
                self.apply_meta()
                self._note("Renamed in Themis (the transcript is untouched).")

        self.app.push_screen(
            TextPromptModal(
                "Rename session", "Display name (empty restores the original topic):", row.topic if row.renamed else ""
            ),
            handle,
        )

    def do_tag(self, rows: list[Row] | None = None) -> None:
        rows = rows or self.targets()
        if not rows:
            return self._none_selected()
        bulk = len(rows) > 1
        label = (
            "Comma-separated. Adds to the marked sessions; prefix a tag with - to remove it."
            if bulk
            else "Comma-separated tags:"
        )

        def handle(value: str | None) -> None:
            if value is None:
                return
            words = [w.strip() for w in value.split(",")]
            if bulk:
                items = [(r.summary.profile, r.summary.session_id) for r in rows]
                meta.bulk_tag(
                    items,
                    add=[w for w in words if w and not w.startswith("-")],
                    remove=[w[1:] for w in words if w.startswith("-")],
                )
            else:
                meta.set_tags(rows[0].summary.profile, rows[0].summary.session_id, words)
            self.apply_meta()

        self.app.push_screen(
            TextPromptModal("Tag sessions" if bulk else "Tag session", label, "" if bulk else ", ".join(rows[0].tags)),
            handle,
        )

    def do_export(self) -> None:
        row = self.current_row()
        pair = self._pair(row) if row else None
        if not pair:
            return self._none_selected()
        dry = export_session(*pair, dry_run=True)

        def handle(result: tuple[bool, bool] | None) -> None:
            if result and result[0]:
                include = result[1]
                self._run_one("Export", lambda: export_session(*pair, include_tool_output=include, confirm=CONFIRM))

        self.app.push_screen(
            ConfirmModal(
                "Export to markdown",
                Text(f"Redacted markdown of the main conversation.\nWritten to {dry.path}"),
                confirm_label="Export",
                checkbox="Include tool output (truncated, still redacted)",
            ),
            handle,
        )

    # ------------------------------------------------------------------ copy and move

    def do_copy(self) -> None:
        self._transfer(move=False)

    def do_move(self) -> None:
        self._transfer(move=True)

    def _transfer(self, *, move: bool) -> None:
        row = self.current_row()
        src = self.profiles.get(row.summary.profile) if row else None
        if not row or not src:
            return self._none_selected()
        verb = "Move" if move else "Copy"
        choices = [(n, f"{p.display}  {p.path}") for n, p in self.profiles.items() if n != src.name]

        def chosen(name: str | None) -> None:
            dst = self.profiles.get(name or "")
            if not dst:
                return
            fn = actions.move_session if move else actions.copy_session
            dry = fn(src, dst, row.summary, dry_run=True)
            parts: list[RenderableType] = [
                Text(
                    f"{src.name} → {dst.name}: {dry.files} files, {human_bytes(dry.bytes)}.\n"
                    "Same project folder in the target, so resume works there."
                ),
            ]
            if move:
                parts.append(Text("The source files are deleted after the copy.", style="bold"))
            if dry.reasons:
                parts.append(_reasons(dry.reasons))
            token = actions.delete_token(row.summary) if move else None

            def confirmed(result: tuple[bool, bool] | None) -> None:
                if result and result[0]:
                    self._run_one(verb, lambda: fn(src, dst, row.summary, confirm=token or CONFIRM))

            self.app.push_screen(
                ConfirmModal(
                    f"{verb} to {dst.name}",
                    Group(*parts),
                    token=token,
                    danger=move,
                    confirm_label=verb,
                    blocked=not dry.ok,
                ),
                confirmed,
            )

        self.app.push_screen(ChoiceModal(f"{verb} to which profile?", choices, "There is no other profile."), chosen)

    # ------------------------------------------------------------------ buttons

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        handlers: dict[str, Callable[[], None]] = {
            "btn-resume": self.do_resume,
            "btn-mark": self.action_toggle_mark,
            "btn-rename": self.do_rename,
            "btn-tag": self.do_tag,
            "btn-export": self.do_export,
            "btn-archive": lambda: self.do_archive([r] if (r := self.current_row()) else []),
            "btn-copy": self.do_copy,
            "btn-move": self.do_move,
            "btn-delete": lambda: self.do_delete([r] if (r := self.current_row()) else []),
            "btn-mark-all": self.action_mark_all,
            "btn-clear": self.action_clear_marks,
            "btn-bulk-tag": self.do_tag,
            "btn-bulk-archive": self.do_archive,
            "btn-bulk-delete": self.do_delete,
            "btn-archive-risk": self.do_archive_at_risk,
        }
        handler = handlers.get(event.button.id or "")
        if handler:
            event.stop()
            handler()
