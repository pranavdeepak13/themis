from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.make_fixtures import build_profile_tree


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Synthetic profiles under a temp dir; Themis state goes to a temp THEMIS_HOME."""
    profiles = build_profile_tree(tmp_path / "home")
    monkeypatch.setenv("THEMIS_PROFILES", ":".join(str(p) for p in profiles.values()))
    monkeypatch.setenv("THEMIS_HOME", str(tmp_path / "themis"))
    return profiles
