"""Optional user config from ~/.themis/config.toml, with built-in defaults."""

from __future__ import annotations

import tomllib

from pydantic import BaseModel, Field

from themis.paths import config_path


class Price(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


# API-equivalent list prices by model family. Estimates only: all profiles are subscription.
DEFAULT_PRICES: dict[str, Price] = {
    "opus": Price(input=5, output=25, cache_read=0.5, cache_write_5m=6.25, cache_write_1h=10),
    "sonnet": Price(input=3, output=15, cache_read=0.3, cache_write_5m=3.75, cache_write_1h=6),
    "haiku": Price(input=1, output=5, cache_read=0.1, cache_write_5m=1.25, cache_write_1h=2),
}

# Substring of the model id -> context window in tokens. Longest match wins.
DEFAULT_WINDOWS: dict[str, int] = {"default": 200_000, "[1m]": 1_000_000}
EXTENDED_WINDOW = 1_000_000


class AdvisorConfig(BaseModel):
    """Thresholds for the optimisation advisor. Override under [advisor] in config.toml."""

    unused_days: int = 60  # skills and MCP servers unused for this long are flagged
    claude_md_max_tokens: int = 2000  # estimated tokens above which a CLAUDE.md is "oversized"
    skill_desc_max_chars: int = 500  # skill descriptions longer than this are flagged
    description_overlap: float = 0.6  # word-set Jaccard above which two descriptions "overlap"
    near_duplicate: float = 0.85  # same, for near-duplicate skills with different names
    high_context_percent: float = 80.0
    low_cache_hit_rate: float = 0.5
    min_cache_tokens: int = 50_000  # ignore tiny sessions when judging the cache hit rate
    startup_overhead_warn_tokens: int = 8_000


class Config(BaseModel):
    aliases: dict[str, str] = Field(default_factory=dict)
    context_windows: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WINDOWS))
    prices: dict[str, Price] = Field(default_factory=lambda: dict(DEFAULT_PRICES))
    # Shell template run by "open resume in terminal"; {command} is the resume command. None = copy only.
    resume_launcher: str | None = None
    # Sessions this many days (or fewer) from being purged are flagged.
    retention_warn_days: int = 7
    # Canonical directory for shared skills, commands and agents. None = detect from existing symlinks.
    shared_dir: str | None = None
    # Transcripts at or above this size are flagged by the doctor.
    large_transcript_mb: int = 100
    advisor: AdvisorConfig = Field(default_factory=AdvisorConfig)


def load_config() -> Config:
    """Load config.toml; a missing or broken file falls back to defaults."""
    path = config_path()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    cfg = Config()
    cfg.aliases = {str(k): str(v) for k, v in raw.get("aliases", {}).items()}
    launcher = raw.get("resume_launcher")
    cfg.resume_launcher = launcher if isinstance(launcher, str) and launcher.strip() else None
    warn = raw.get("retention_warn_days")
    if isinstance(warn, int) and warn >= 0:
        cfg.retention_warn_days = warn
    shared = raw.get("shared_dir")
    cfg.shared_dir = shared if isinstance(shared, str) and shared.strip() else None
    big = raw.get("large_transcript_mb")
    if isinstance(big, int) and big > 0:
        cfg.large_transcript_mb = big
    adv = raw.get("advisor")
    if isinstance(adv, dict):
        for key, value in adv.items():
            current = getattr(cfg.advisor, key, None)
            if isinstance(current, bool) or current is None:
                continue
            if isinstance(value, int | float) and not isinstance(value, bool):
                setattr(cfg.advisor, key, type(current)(value))
    cfg.context_windows |= {str(k): int(v) for k, v in raw.get("context_windows", {}).items()}
    for fam, p in raw.get("prices", {}).items():
        try:
            cfg.prices[str(fam)] = Price(**p)
        except (TypeError, ValueError):
            continue
    return cfg
