"""Smoke test of the resource write actions on a temp copy of the real profiles' configuration.

Reads the real ~/.claude* configuration (read-only), copies a redacted subset into a temp directory
(settings, skills, commands, agents, plugin registries, MCP server definitions with secrets masked), then runs
the plugin and MCP toggles, the settings editor and promote there. Afterwards it proves the real files did
not change. Transcripts and credentials are never copied. Usage: uv run python scripts/smoke_real_copy.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import orjson

from themis.config import Config
from themis.profiles.discover import discover_profiles
from themis.redact import redact_obj
from themis.resources import settings as st
from themis.resources.scan import scan_inventory
from themis.resources.symlinks import promote, shared_root


def fingerprint(paths: list[Path]) -> dict[str, str]:
    out = {}
    for p in paths:
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            out[str(p)] = ",".join(sorted(os.listdir(p)))
    return out


def copy_profile(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True)
    (dst / "projects").mkdir()
    settings = src / "settings.json"
    if settings.is_file():
        obj = orjson.loads(settings.read_bytes())
        (dst / "settings.json").write_bytes(orjson.dumps(redact_obj(obj), option=orjson.OPT_INDENT_2))
    cj = src / ".claude.json"
    if cj.is_file():  # only MCP server definitions, secrets masked; never the account or caches
        servers = orjson.loads(cj.read_bytes()).get("mcpServers", {})
        (dst / ".claude.json").write_bytes(orjson.dumps({"mcpServers": redact_obj(servers)}))
    for sub in ("skills", "commands", "agents"):
        if (src / sub).is_dir():
            # dereference symlinks so the copy holds real, identical files (nothing points back at the real tree)
            shutil.copytree(
                src / sub,
                dst / sub,
                symlinks=False,
                ignore=shutil.ignore_patterns(".trash", "synced"),
                ignore_dangling_symlinks=True,
            )
    (dst / "plugins").mkdir()
    for name in ("installed_plugins.json", "known_marketplaces.json"):
        if (src / "plugins" / name).is_file():
            shutil.copy2(src / "plugins" / name, dst / "plugins" / name)


def main() -> int:
    real = discover_profiles()
    watched = [p.path / "settings.json" for p in real] + [p.path / "skills" for p in real]
    watched += [Path.home() / ".claude-shared" / "skills", Path.home() / ".themis" / "audit.log"]
    before = fingerprint(watched)

    tmp = Path(tempfile.mkdtemp(prefix="themis-smoke-"))
    try:
        profiles = {}
        for p in real:
            copy_profile(p.path, tmp / "home" / p.path.name)
            profiles[p.name] = tmp / "home" / p.path.name
        os.environ["THEMIS_PROFILES"] = ":".join(str(v) for v in profiles.values())
        os.environ["THEMIS_HOME"] = str(tmp / "themis")
        cfg = Config(shared_dir=str(tmp / "shared"))
        copies = discover_profiles()
        by_name = {p.name: p for p in copies}
        inv = scan_inventory(copies)
        print(f"copied {len(copies)} profiles, {len(inv.resources)} resources, errors: {inv.errors}")
        steps = 0

        for p in copies:  # plugin toggle round trip on the first enabled plugin
            plug = next((r for r in inv.resources if r.profile == p.name and r.kind == "plugin" and r.enabled), None)
            if plug:
                assert st.set_plugin_enabled(p, plug.name, False, confirm=st.CONFIRM, dry_run=False).ok
                assert st.set_plugin_enabled(p, plug.name, True, confirm=st.CONFIRM, dry_run=False).ok
                print(f"plugin toggle ok: {p.name} {plug.name}")
                steps += 1
                break

        for p in copies:  # MCP toggle round trip on a user-scope server
            srv = next(
                (r for r in inv.resources if r.profile == p.name and r.kind == "mcp" and r.origin == "user"), None
            )
            if srv:
                assert st.set_mcp_enabled(p, srv.name, False, confirm=st.CONFIRM, dry_run=False).ok
                assert st.set_mcp_enabled(p, srv.name, True, confirm=st.CONFIRM, dry_run=False).ok
                print(f"mcp toggle ok: {p.name} {srv.name}")
                steps += 1
                break

        p = next(p for p in copies if (p.path / "settings.json").is_file())
        edited = st.settings_text_for_edit(p)
        assert st.edit_settings(p, edited, confirm=st.CONFIRM, dry_run=False).ok  # no-op edit must be harmless
        text = st.settings_text_for_edit(p).rstrip()[:-1].rstrip() + ',\n  "themisSmoke": true\n}\n'
        res = st.edit_settings(p, text, confirm=st.CONFIRM, dry_run=False)
        assert res.ok and orjson.loads((p.path / "settings.json").read_bytes())["themisSmoke"] is True
        print(f"settings edit ok: {p.name}")
        steps += 1

        inv = scan_inventory(copies)
        targets = sorted(
            {r.name for r in inv.resources if r.kind == "skill" and r.origin == "user" and r.identical_copy}
        )
        if targets:  # identical copies: dereferencing real symlinks produced these
            name = targets[0]
            holders = [
                by_name[r.profile] for r in inv.resources if r.name == name and r.kind == "skill" and r.origin == "user"
            ]
            res = promote("skill", name, holders, inv.resources, cfg, confirm="confirm", dry_run=False)
            root = shared_root(cfg, inv.resources)
            assert res.ok and (root / "skills" / name / "SKILL.md").exists(), res.message
            print(f"promote ok: {name} -> {root} for {[h.name for h in holders]}")
            steps += 1
        else:
            print("promote skipped: no identical user-level copies in this setup")
        print(f"{steps} write path(s) exercised in the temp copy")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = fingerprint(watched)
    changed = [k for k in before if before[k] != after.get(k)]
    if changed:
        print("REAL FILES CHANGED:", changed)
        return 1
    print("real profile files unchanged (settings.json, skills listings, shared skills, audit log)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
