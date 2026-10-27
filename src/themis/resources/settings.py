"""Settings reader (merged view with precedence and origin) and the settings write functions.

Reads redact through `redact_obj`. Writes (plugin toggle, MCP toggle, settings editor) all follow the
same shape: build a plan with a diff (dry run), then apply it with the confirmation token, which
backs up the file to ~/.themis/backups/<profile>/ and appends to the audit log (rule 1).
"""

from __future__ import annotations

import datetime as dt
import difflib
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from themis import audit
from themis.paths import themis_home
from themis.profiles.model import Profile
from themis.redact import MASK, redact_obj
from themis.resources.jsonio import read_json
from themis.resources.model import Resource

CONFIRM = "confirm"


class SettingsError(Exception):
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or [message]


# ---------------------------------------------------------------------------- reading


@dataclass
class SettingValue:
    key: str
    value: Any  # redacted
    origin: str  # winning level
    layers: list[tuple[str, Any]] = field(default_factory=list)  # (level, redacted value), low to high

    @property
    def overridden(self) -> bool:
        return len(self.layers) > 1


def settings_layers(profile: Profile, project: Path | None = None) -> list[tuple[str, Path]]:
    """Settings files from lowest to highest precedence. remote-settings.json is managed policy, so it wins."""
    layers = [("user", profile.path / "settings.json")]
    if project is not None:
        layers += [
            ("project", project / ".claude" / "settings.json"),
            ("local", project / ".claude" / "settings.local.json"),
        ]
    layers.append(("remote", profile.path / "remote-settings.json"))
    return layers


def merged_settings(profile: Profile, project: Path | None = None) -> tuple[list[SettingValue], list[str]]:
    """Top-level keys merged over user < project < local < remote(managed), each with every layer's value."""
    layers = settings_layers(profile, project)
    errors: list[str] = []
    merged: dict[str, SettingValue] = {}
    for level, path in layers:
        data, err = read_json(path)
        if err:
            errors.append(f"{path}: {err}")
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            safe = redact_obj({key: value})[key]
            sv = merged.setdefault(str(key), SettingValue(str(key), safe, level))
            sv.layers.append((level, safe))
            sv.value, sv.origin = safe, level
    return sorted(merged.values(), key=lambda s: s.key), errors


def scan_permissions(settings: dict[str, Any], profile: str, path: str) -> list[Resource]:
    perms = settings.get("permissions")
    out: list[Resource] = []
    if not isinstance(perms, dict):
        return out
    for bucket, rules in perms.items():
        if isinstance(rules, list):
            out.extend(
                Resource(kind="permission", name=str(r), profile=profile, path=path, extra={"bucket": str(bucket)})
                for r in rules
            )
        else:
            out.append(
                Resource(
                    kind="permission", name=f"{bucket}={rules}", profile=profile, path=path, extra={"bucket": "setting"}
                )
            )
    return out


# ---------------------------------------------------------------------------- writing


@dataclass
class WritePlan:
    action: str
    profile: str
    path: str
    diff: str
    changed: bool
    new_bytes: bytes = field(repr=False, default=b"")
    note: str = ""


@dataclass
class WriteResult:
    ok: bool
    dry_run: bool
    message: str
    plan: WritePlan | None = None
    backup: str | None = None


def _backup_dir(profile: str) -> Path:
    return themis_home() / "backups" / profile


def _diff(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"{label} (current)", f"{label} (new)")
    )


def _dump(obj: Any, trailing_newline: bool) -> bytes:
    out = orjson.dumps(obj, option=orjson.OPT_INDENT_2)
    return out + b"\n" if trailing_newline else out


def _load_obj(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SettingsError(f"Cannot read {path.name}: {exc.__class__.__name__}") from exc
    try:
        obj = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise SettingsError(f"{path.name} is not valid JSON; refusing to edit it.") from exc
    if not isinstance(obj, dict):
        raise SettingsError(f"{path.name} is not a JSON object.")
    return obj, raw


def _check_inside(profile: Profile, path: Path) -> None:
    if os.path.realpath(path.parent) != os.path.realpath(profile.path) and not str(os.path.realpath(path)).startswith(
        os.path.realpath(profile.path) + os.sep
    ):
        raise SettingsError("Refusing to write outside the profile directory.")


def _apply(profile: Profile, plan: WritePlan, path: Path, confirm: str | None, dry_run: bool) -> WriteResult:
    if dry_run:
        return WriteResult(True, True, "Dry run: nothing written." if plan.changed else "No change.", plan)
    if confirm != CONFIRM:
        raise SettingsError(f"Confirmation token mismatch (expected {CONFIRM!r}).")
    if not plan.changed:
        return WriteResult(True, False, "No change.", plan)
    _check_inside(profile, path)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = _backup_dir(profile.name)
    bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / f"{path.name}.{stamp}"
    shutil.copy2(path, backup)
    tmp = path.with_name(f".{path.name}.themis-tmp{os.getpid()}-{threading.get_ident()}")
    tmp.write_bytes(plan.new_bytes)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
    audit.record(plan.action, profile=profile.name, path=str(path), backup=str(backup))
    return WriteResult(True, False, f"Written. Backup: {backup}", plan, str(backup))


def _json_text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _plan_obj(action: str, profile: Profile, path: Path, mutate: Any, display_only: str | None = None) -> WritePlan:
    obj, raw = _load_obj(path)
    before = orjson.dumps(obj, option=orjson.OPT_INDENT_2).decode()
    mutate(obj)
    new = _dump(obj, raw.endswith(b"\n"))
    after = orjson.dumps(orjson.loads(new), option=orjson.OPT_INDENT_2).decode()
    if display_only:  # only show the keys that matter (e.g. never print the whole .claude.json)
        before, after = (_pick(orjson.loads(before), display_only), _pick(orjson.loads(after), display_only))
    changed = raw != new and orjson.loads(raw) != orjson.loads(new)
    return WritePlan(action, profile.name, str(path), _diff(before, after, path.name) if changed else "", changed, new)


def _pick(obj: dict[str, Any], key: str) -> str:
    return orjson.dumps({key: obj.get(key)}, option=orjson.OPT_INDENT_2).decode()


def set_plugin_enabled(
    profile: Profile, plugin_id: str, enabled: bool, *, confirm: str | None = None, dry_run: bool = True
) -> WriteResult:
    """Toggle `enabledPlugins["<plugin>@<marketplace>"]` in the profile's settings.json (what /plugin does)."""
    path = profile.path / "settings.json"

    def mutate(obj: dict[str, Any]) -> None:
        ep = obj.get("enabledPlugins")
        if not isinstance(ep, dict):
            ep = obj["enabledPlugins"] = {}
        ep[plugin_id] = enabled

    plan = _plan_obj("plugin.toggle", profile, path, mutate, "enabledPlugins")
    plan.note = f"{plugin_id} -> {'enabled' if enabled else 'disabled'}"
    return _apply(profile, plan, path, confirm, dry_run)


def set_mcp_enabled(
    profile: Profile, server: str, enabled: bool, *, confirm: str | None = None, dry_run: bool = True
) -> WriteResult:
    """Toggle a user-scope MCP server through `disabledMcpServers` in the profile's .claude.json.

    This is the list Claude Code's own /mcp toggle maintains (verified in the 2.1.311 binary). Servers
    that come from plugins or project .mcp.json files have different mechanisms and are not handled.
    """
    path = profile.path / ".claude.json"

    def mutate(obj: dict[str, Any]) -> None:
        servers = obj.get("mcpServers")
        if not isinstance(servers, dict) or server not in servers:
            raise SettingsError(f"{server!r} is not a user-scope MCP server in this profile.")
        cur = obj.get("disabledMcpServers")
        names = [str(x) for x in cur] if isinstance(cur, list) else []
        names = [n for n in names if n != server]
        if not enabled:
            names.append(server)
        if names or "disabledMcpServers" in obj:
            obj["disabledMcpServers"] = names

    plan = _plan_obj("mcp.toggle", profile, path, mutate, "disabledMcpServers")
    plan.note = f"{server} -> {'enabled' if enabled else 'disabled'}"
    return _apply(profile, plan, path, confirm, dry_run)


# ---- the free-form settings editor


def settings_text_for_edit(profile: Profile) -> str:
    """settings.json as text with secret values masked. Masked values are restored on save."""
    obj, _raw = _load_obj(profile.path / "settings.json")
    return orjson.dumps(redact_obj(obj), option=orjson.OPT_INDENT_2).decode() + "\n"


def _restore(new: Any, old: Any) -> Any:
    """Put original values back wherever the edited text still holds the mask at the same path."""
    if new == MASK:
        if old is None or old == MASK:
            raise SettingsError("A masked value was added where no secret existed; type the real value or remove it.")
        return old
    if isinstance(new, dict):
        old_d = old if isinstance(old, dict) else {}
        return {k: _restore(v, old_d.get(k)) for k, v in new.items()}
    if isinstance(new, list):
        old_l = old if isinstance(old, list) else []
        return [_restore(v, old_l[i] if i < len(old_l) else None) for i, v in enumerate(new)]
    return new


def validate_settings_text(text: str) -> str | None:
    """None when the text is a valid settings object, else a short reason."""
    try:
        obj = orjson.loads(text)
    except orjson.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    if not isinstance(obj, dict):
        return "Settings must be a JSON object."
    for key, kind in (("permissions", dict), ("hooks", dict), ("enabledPlugins", dict), ("env", dict)):
        if key in obj and not isinstance(obj[key], kind):
            return f"{key!r} must be an object."
    perms = obj.get("permissions")
    if isinstance(perms, dict):
        for bucket in ("allow", "deny", "ask"):
            if bucket in perms and not (
                isinstance(perms[bucket], list) and all(isinstance(x, str) for x in perms[bucket])
            ):
                return f"permissions.{bucket} must be a list of strings."
    return None


def edit_settings(profile: Profile, new_text: str, *, confirm: str | None = None, dry_run: bool = True) -> WriteResult:
    path = profile.path / "settings.json"
    problem = validate_settings_text(new_text)
    if problem:
        raise SettingsError(problem)
    old_obj, raw = _load_obj(path)
    restored = _restore(orjson.loads(new_text), old_obj)
    new = _dump(restored, True)
    shown_old = orjson.dumps(redact_obj(old_obj), option=orjson.OPT_INDENT_2).decode() + "\n"
    shown_new = orjson.dumps(redact_obj(restored), option=orjson.OPT_INDENT_2).decode() + "\n"
    changed = orjson.loads(raw) != restored
    plan = WritePlan(
        "settings.edit",
        profile.name,
        str(path),
        _diff(shown_old, shown_new, path.name) if changed else "",
        changed,
        new,
    )
    return _apply(profile, plan, path, confirm, dry_run)
