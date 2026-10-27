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


class Config(BaseModel):
    aliases: dict[str, str] = Field(default_factory=dict)
    context_windows: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WINDOWS))
    prices: dict[str, Price] = Field(default_factory=lambda: dict(DEFAULT_PRICES))


def load_config() -> Config:
    """Load config.toml; a missing or broken file falls back to defaults."""
    path = config_path()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    cfg = Config()
    cfg.aliases = {str(k): str(v) for k, v in raw.get("aliases", {}).items()}
    cfg.context_windows |= {str(k): int(v) for k, v in raw.get("context_windows", {}).items()}
    for fam, p in raw.get("prices", {}).items():
        try:
            cfg.prices[str(fam)] = Price(**p)
        except (TypeError, ValueError):
            continue
    return cfg
