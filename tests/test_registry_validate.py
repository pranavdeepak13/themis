"""The generated manifest must satisfy the vendored ARD entry schema; the validator must catch real violations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import orjson
from typer.testing import CliRunner

from fixtures.make_fixtures import FAKE_KEY
from themis.cli import app
from themis.registry.manifest import build_manifest
from themis.registry.queries import representative_queries
from themis.registry.validate import _schema, validate_manifest
from themis.resources.store import load_all

runner = CliRunner()


def _manifest() -> dict:
    return build_manifest(load_all().inventory.resources, meta={"resources": {}})


def test_generated_manifest_is_valid_with_no_warnings(tree: dict[str, Path]) -> None:
    m = _manifest()
    report = validate_manifest(m)
    assert report.errors == [] and report.warnings == [] and len(m["entries"]) > 10
    for e in m["entries"]:
        assert 2 <= len(e["representativeQueries"]) <= 5
        assert all(isinstance(v, str | int | float | bool | type(None)) for v in e["metadata"].values())
        assert ("url" in e) != ("data" in e)


def test_vendored_schema_is_the_ard_one() -> None:
    schema = _schema()
    assert schema["$id"].endswith("ards-project/ard-spec/main/spec/schemas/ard-entry.schema.json")
    assert (
        schema["$defs"]["EntryFields"]["properties"]["identifier"]["pattern"]
        == r"^urn:air:[a-zA-Z0-9.-]+(:[a-zA-Z0-9._-]+)+$"
    )


def test_validator_catches_each_kind_of_violation(tree: dict[str, Path]) -> None:
    good = _manifest()
    entry = good["entries"][0]

    def broken(**changes) -> list[str]:
        m = copy.deepcopy(good)
        m["entries"][0].update(changes)
        for k in [k for k, v in changes.items() if v is ...]:
            del m["entries"][0][k]
        return validate_manifest(m).errors

    assert broken(data={"x": 1})  # url and data together
    assert broken(identifier="not-a-urn") and broken(identifier="urn:air:bad domain:x")
    assert broken(displayName=...) and broken(type=...)
    assert broken(metadata={"themis": {"nested": "object"}})  # the bug the schema caught: metadata must be scalars
    assert broken(url="relative/path.md") if "url" in entry else True
    assert broken(identifier="urn:air:localhost:skill:user:x")  # single-label publisher
    dup = copy.deepcopy(good)
    dup["entries"].append(copy.deepcopy(dup["entries"][0]))
    assert any("globally unique" in e for e in validate_manifest(dup).errors)
    assert validate_manifest({"nope": []}).errors and validate_manifest([]).errors


def test_missing_or_out_of_range_queries_are_warnings_not_errors(tree: dict[str, Path]) -> None:
    m = _manifest()
    del m["entries"][0]["representativeQueries"]
    m["entries"][1]["representativeQueries"] = ["a", "b", "c", "d", "e", "f"]
    report = validate_manifest(m)
    assert report.ok and len(report.warnings) == 2


def test_representative_queries_heuristics() -> None:
    q = representative_queries(
        "tdd",
        "Test-driven development. Use when the user wants to build features or fix bugs test-first, "
        "or mentions red-green-refactor.",
        "skill",
    )
    assert 2 <= len(q) <= 5 and any("test-first" in x for x in q)
    assert len(set(x.lower() for x in q)) == len(q)
    fallback = representative_queries("coffee-chat", None, "skill")
    assert len(fallback) >= 2 and any("coffee chat" in x for x in fallback)
    secret = representative_queries(
        "x", f"Use when you need to call the API with token {FAKE_KEY} for reports", "skill"
    )
    assert all(FAKE_KEY not in x for x in secret)
    assert all(len(x) <= 110 for x in representative_queries("long", "Use when " + "word " * 80, "skill"))


def test_cli_validate_and_manifest_roundtrip(tree: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "ard.json"
    result = runner.invoke(app, ["registry", "manifest", "--out", str(out)])
    assert result.exit_code == 0 and out.exists()
    ok = runner.invoke(app, ["registry", "validate", str(out)])
    assert ok.exit_code == 0 and "valid" in ok.stdout and "0 error(s)" in ok.stdout
    data = json.loads(out.read_text())
    data["entries"][0]["metadata"] = {"themis": {"nested": 1}}
    bad = tmp_path / "bad.json"
    bad.write_bytes(orjson.dumps(data))
    res = runner.invoke(app, ["registry", "validate", str(bad)])
    assert res.exit_code == 1 and "INVALID" in res.stdout
    assert runner.invoke(app, ["registry", "validate", str(tmp_path / "missing.json")]).exit_code == 2
    assert runner.invoke(app, ["registry", "manifest", "--publisher", "localhost", "--stdout"]).exit_code == 2
