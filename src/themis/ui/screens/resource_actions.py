"""Action flows for the resource screens. Every write is a core call with a dry run, a diff and a confirm."""

from __future__ import annotations

from rich.console import Group as RichGroup
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.containers import Vertical
from textual.widgets import Button

from themis.config import load_config
from themis.profiles.model import Profile
from themis.resources import mcp_health
from themis.resources import settings as st
from themis.resources.model import Resource
from themis.resources.store import ResourceData
from themis.resources.symlinks import CONFIRM, PromoteError, promote
from themis.resources.view import Group
from themis.sessions import meta
from themis.ui.widgets.modals import ChoiceModal, ConfirmModal, TextPromptModal
from themis.ui.widgets.resource_modals import EditSettingsModal, InfoModal, ProfilePickModal, diff_text

NO_NATIVE_MCP = (
    "Claude Code has no native toggle for this server. User-scope servers are toggled through "
    "disabledMcpServers; servers provided by a plugin follow the plugin's enabled state (disable the plugin), "
    "and project .mcp.json servers are approved per project inside Claude Code."
)


class ResourceActions(Vertical):
    data: ResourceData | None
    profile: str | None

    def current_group(self) -> Group | None:
        return None

    def refresh_view(self) -> None: ...

    # ------------------------------------------------------------------ helpers

    def _profile_obj(self, name: str) -> Profile | None:
        return next((p for p in (self.data.profiles if self.data else []) if p.name == name), None)

    def _reload(self) -> None:
        reload = getattr(self.app, "reload_resources", None)
        if reload:
            reload()

    def _pick_member(self, members: list[Resource], title: str, then) -> None:  # noqa: ANN001
        """Run `then(member)` for the member in the filtered profile, or ask which profile."""
        if self.profile:
            members = [m for m in members if m.profile == self.profile]
        if not members:
            self.notify("Nothing to act on in the selected profile.", severity="warning")
        elif len(members) == 1:
            then(members[0])
        else:

            def picked(name: str | None) -> None:
                if name:
                    then(next(m for m in members if m.profile == name))

            self.app.push_screen(ChoiceModal(title, [(m.profile, m.profile) for m in members]), picked)

    # ------------------------------------------------------------------ tags

    @on(Button.Pressed, "#btn-rtag")
    def do_tag(self) -> None:
        g = self.current_group()
        if g is None:
            return

        def done(value: str | None) -> None:
            if value is None:
                return
            meta.set_resource_tags(g.kind, g.name, [t for t in value.replace(",", " ").split() if t])
            if self.data is not None:
                self.data.meta = meta.load_meta()
                self.refresh_view()
            self.notify("Tags saved (Themis only).")

        self.app.push_screen(TextPromptModal("Tag resource", "Tags, space separated", " ".join(g.tags)), done)

    # ------------------------------------------------------------------ promote

    @on(Button.Pressed, "#btn-promote")
    def do_promote(self) -> None:
        g = self.current_group()
        if g is None or self.data is None:
            return
        if g.origin != "user":
            self.notify(
                "Only user-level resources can be promoted (not plugin, project or synced ones).", severity="warning"
            )
            return
        names = [p.name for p in self.data.profiles]
        note = f"Link {g.name!r} into the profiles you tick. Existing identical copies become symlinks."
        self.app.push_screen(
            ProfilePickModal(f"Promote {g.name}", names, set(g.profiles), note),
            lambda c: self._promote_plan(g, c, None),
        )

    def _promote_plan(self, g: Group, chosen: list[str] | None, prefer: str | None) -> None:
        if not chosen or self.data is None:
            return
        targets = [p for p in self.data.profiles if p.name in chosen]
        try:
            res = promote(g.kind, g.name, targets, self.data.inventory.resources, load_config(), prefer=prefer)
        except PromoteError as exc:
            self.notify(str(exc), severity="error")
            return
        plan = res.plan
        if plan.needs_choice:

            def choose(_ok: tuple[bool, bool]) -> None:
                if not _ok[0]:
                    return
                self.app.push_screen(
                    ChoiceModal(
                        "Which side wins?",
                        [
                            (c, "keep the shared version" if c == "shared" else f"use {c}'s version")
                            for c in plan.choices
                        ],
                    ),
                    lambda side: self._promote_plan(g, chosen, side) if side else None,
                )

            self.app.push_screen(
                ConfirmModal("These copies have drifted", diff_text(plan.diff), confirm_label="Choose a side…"), choose
            )
            return
        if not res.ok:
            self.notify(res.message, severity="error")
            return
        steps = Table("step", "profile", "what", box=None)
        for s in plan.steps:
            steps.add_row(s.op, s.profile, s.detail)
        body = RichGroup(
            Text(f"Canonical copy: {plan.canonical}", style="bold"),
            steps,
            Text("\nReplaced copies are moved to ~/.themis/backups, never deleted.", style="dim"),
        )

        def go(result: tuple[bool, bool]) -> None:
            if not result[0]:
                return
            try:
                done = promote(
                    g.kind,
                    g.name,
                    targets,
                    self.data.inventory.resources,
                    load_config(),
                    prefer=prefer,
                    confirm=CONFIRM,
                    dry_run=False,
                )
            except PromoteError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(done.message, timeout=8)
            self._reload()

        self.app.push_screen(ConfirmModal(f"Promote {g.name}", body, confirm_label="Create symlinks"), go)

    # ------------------------------------------------------------------ plugin and MCP toggles

    @on(Button.Pressed, "#btn-toggle")
    def do_toggle(self) -> None:
        g = self.current_group()
        if g is None:
            return
        if g.kind == "marketplace":
            self.notify(
                "A marketplace has no on/off switch. Toggle its plugins instead.", severity="warning", timeout=6
            )
            return
        if g.kind == "mcp":
            members = [m for m in g.members if m.origin == "user"]
            if not members:
                self.notify(NO_NATIVE_MCP, severity="warning", timeout=10)
                return
        else:
            members = g.members
        self._pick_member(members, f"Which profile? ({g.name})", lambda m: self._toggle(m))

    def _toggle(self, m: Resource) -> None:
        profile = self._profile_obj(m.profile)
        if profile is None:
            return
        new = not bool(m.enabled)
        fn = st.set_mcp_enabled if m.kind == "mcp" else st.set_plugin_enabled
        try:
            res = fn(profile, m.name, new)
        except st.SettingsError as exc:
            self.notify(str(exc), severity="error")
            return
        assert res.plan is not None
        body = RichGroup(
            Text(f"{m.profile}: {res.plan.note}", style="bold"),
            Text(res.plan.path, style="dim"),
            diff_text(res.plan.diff),
        )

        def go(result: tuple[bool, bool]) -> None:
            if not result[0]:
                return
            try:
                done = fn(profile, m.name, new, confirm=st.CONFIRM, dry_run=False)
            except st.SettingsError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(done.message, timeout=8)
            self._reload()

        self.app.push_screen(
            ConfirmModal(f"{'Enable' if new else 'Disable'} {m.name}", body, confirm_label="Write"), go
        )

    # ------------------------------------------------------------------ MCP health (button only)

    @on(Button.Pressed, "#btn-health")
    def do_health(self) -> None:
        if self.data is None:
            return
        g = self.current_group()
        names = {m.profile for m in g.members} if g else set()
        if self.profile:
            names &= {self.profile}
        targets = [p for p in self.data.profiles if p.name in (names or {p.name for p in self.data.profiles})]
        self.notify(f"Running `claude mcp list` for {len(targets)} profile(s)…", timeout=4)
        self._health(targets)

    @work(thread=True, exclusive=True, group="health")
    def _health(self, targets: list[Profile]) -> None:
        results = [(p.name, *mcp_health.check_profile(p)) for p in targets]
        self.app.call_from_thread(self._show_health, results)

    def _show_health(self, results: list[tuple[str, list[mcp_health.ServerHealth], str | None]]) -> None:
        t = Table("profile", "server", "status", box=None)
        colour = {"connected": "bold", "needs-auth": "italic", "failed": "bold reverse", "unknown": "dim"}
        for profile, servers, error in results:
            if error:
                t.add_row(profile, Text(error, style="bold reverse"), "")
            for s in servers:
                t.add_row(profile, s.name, Text(s.status, style=colour.get(s.status, "")))
        self.app.push_screen(InfoModal("MCP health (claude mcp list)", t))

    # ------------------------------------------------------------------ settings editor

    @on(Button.Pressed, "#btn-edit")
    def do_edit(self) -> None:
        if self.data is None:
            return
        names = [p.name for p in self.data.profiles if not self.profile or p.name == self.profile]
        if len(names) == 1:
            self._edit(names[0])
        else:
            self.app.push_screen(
                ChoiceModal("Edit which profile's settings.json?", [(n, n) for n in names]),
                lambda n: self._edit(n) if n else None,
            )

    def _edit(self, name: str) -> None:
        profile = self._profile_obj(name)
        if profile is None:
            return
        try:
            text = st.settings_text_for_edit(profile)
        except st.SettingsError as exc:
            self.notify(str(exc), severity="error")
            return

        def edited(new: str | None) -> None:
            if new is None or new == text:
                return
            try:
                res = st.edit_settings(profile, new)
            except st.SettingsError as exc:
                self.notify(str(exc), severity="error")
                return
            assert res.plan is not None
            if not res.plan.changed:
                self.notify("No change.")
                return

            def go(result: tuple[bool, bool]) -> None:
                if not result[0]:
                    return
                try:
                    done = st.edit_settings(profile, new, confirm=st.CONFIRM, dry_run=False)
                except st.SettingsError as exc:
                    self.notify(str(exc), severity="error")
                    return
                self.notify(done.message, timeout=8)
                self._reload()

            self.app.push_screen(
                ConfirmModal(f"Write settings.json ({name})", diff_text(res.plan.diff), confirm_label="Write"), go
            )

        self.app.push_screen(EditSettingsModal(name, text), edited)
