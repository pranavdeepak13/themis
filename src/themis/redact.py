"""Secret redaction. Applied at the reader layer so the UI cannot display secrets."""

from __future__ import annotations

import re
from typing import Any

MASK = "***REDACTED***"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
]
# user:password@host in URLs
_URL_CREDS = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")
# NAME=value / "name": "value" where the name looks secret
_SECRET_NAME = r"[A-Za-z0-9_.\-]*(?:token|secret|password|passwd|api[_-]?key|apikey|auth|credential)[A-Za-z0-9_.\-]*"
_ASSIGN = re.compile(rf"(?i)(\b{_SECRET_NAME}\b[\"']?\s*[:=]\s*[\"']?)([^\s\"',;&}}\]]{{4,}})")

_SENSITIVE_KEY = re.compile(r"(?i)token|secret|password|passwd|api[_-]?key|apikey|auth|credential|cookie")
_ALWAYS_REDACT_KEYS = {"env", "headers", "headershelper", "oauth"}


def redact_secrets(text: str) -> str:
    """Known secret shapes only (keys, tokens, bearer strings, URL credentials). Safe for whole documents such
    as reports, where prose like "Tokens: 2.1B" must survive."""
    for pat in _PATTERNS:
        text = pat.sub(MASK, text)
    return _URL_CREDS.sub(MASK, text)


def redact_text(text: str) -> str:
    """redact_secrets plus the NAME=value heuristic for names that look secret. Stricter, for free text."""
    return _ASSIGN.sub(lambda m: m.group(1) + MASK, redact_secrets(text))


def redact_obj(obj: Any) -> Any:
    """Recursively redact a parsed config. Values under env/headers keys are always masked."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            k = str(key)
            if k.lower() in _ALWAYS_REDACT_KEYS and isinstance(value, dict):
                out[k] = {sub: MASK for sub in value}
            elif _SENSITIVE_KEY.search(k) and isinstance(value, str | int | float):
                out[k] = MASK
            else:
                out[k] = redact_obj(value)
        return out
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
