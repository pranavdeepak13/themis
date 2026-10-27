"""Validate a manifest against the vendored ARD entry schema and the conformance rules the schema cannot express.

`ard-entry.schema.json` is the unmodified file from ards-project/ard-spec (spec/schemas), vendored so
validation needs no network. Schema violations are errors; the rules the ARD conformance tester reports as
warnings (representativeQueries absent or outside 2-5) are warnings.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from themis.registry.urn import parse_urn, valid_publisher


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@cache
def _schema() -> dict[str, Any]:
    return json.loads(resources.files("themis.registry").joinpath("ard-entry.schema.json").read_text())


def _validator() -> Draft202012Validator:
    schema = _schema()
    return Draft202012Validator({"$ref": "#/$defs/ArdManifest", "$defs": schema["$defs"]})


def validate_manifest(manifest: Any) -> ValidationReport:
    report = ValidationReport()
    for err in sorted(_validator().iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(document)"
        report.errors.append(f"{where}: {err.message[:200]}")
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        return report
    ids = Counter(e.get("identifier") for e in entries if isinstance(e, dict))
    for ident, n in ids.items():
        if n > 1:
            report.errors.append(f"{ident}: identifier appears {n} times (must be globally unique)")
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        label = e.get("identifier") or f"entries/{i}"
        parsed = parse_urn(str(e.get("identifier", "")))
        if parsed and not valid_publisher(parsed["publisher"]):
            report.errors.append(f"{label}: publisher {parsed['publisher']!r} is not a fully qualified domain")
        url = e.get("url")
        if isinstance(url, str) and not urlparse(url).scheme:  # the schema's "uri" format is not enforced by default
            report.errors.append(f"{label}: url {url!r} is not an absolute URI")
        q = e.get("representativeQueries")
        if q is None:
            report.warnings.append(f"{label}: representativeQueries is absent (SHOULD have 2-5)")
        elif isinstance(q, list) and not 2 <= len(q) <= 5:
            report.warnings.append(f"{label}: representativeQueries has {len(q)} (SHOULD have 2-5)")
    return report
