"""Derived numbers that depend on user config (window sizes, price table)."""

from __future__ import annotations

from themis.config import EXTENDED_WINDOW, Config, Price
from themis.sessions.model import SessionSummary, Tokens


def context_window(model: str | None, observed: int, cfg: Config) -> int:
    """Window size from the config table (longest substring match).

    If the observed context exceeds it, the session evidently ran with the extended window.
    """
    window = cfg.context_windows.get("default", 200_000)
    if model:
        for key in sorted(cfg.context_windows, key=len, reverse=True):
            if key != "default" and key in model:
                window = cfg.context_windows[key]
                break
    if observed > window:
        window = max(window, EXTENDED_WINDOW)
    return window


def context_percent(s: SessionSummary, cfg: Config, *, peak: bool = False) -> float:
    tokens = s.context_peak if peak else s.context_tokens
    window = context_window(s.last_model, s.context_peak, cfg)
    return round(100 * tokens / window, 1) if window else 0.0


def _family_price(model: str, cfg: Config) -> Price | None:
    for fam, price in cfg.prices.items():
        if fam in model:
            return price
    return None


def tokens_cost(t: Tokens, price: Price) -> float:
    cc_1h = min(t.cache_creation_1h, t.cache_creation)
    cc_5m = t.cache_creation - cc_1h
    return (
        t.input * price.input
        + t.output * price.output
        + t.cache_read * price.cache_read
        + cc_5m * price.cache_write_5m
        + cc_1h * price.cache_write_1h
    ) / 1_000_000


def model_cost(model: str, tok: Tokens, cfg: Config) -> float | None:
    """API-equivalent USD for one model's tokens, or None when the model has no price."""
    price = _family_price(model, cfg)
    return None if price is None else tokens_cost(tok, price)


def estimate_cost(s: SessionSummary, cfg: Config) -> tuple[float, bool]:
    """API-equivalent USD estimate and whether any model had no price (incomplete)."""
    total, incomplete = 0.0, False
    for model, tok in s.model_tokens.items():
        price = _family_price(model, cfg)
        if price is None:
            incomplete = incomplete or tok.total > 0
            continue
        total += tokens_cost(tok, price)
    return total, incomplete
