from __future__ import annotations

import pytest

from themis.redact import MASK, redact_obj, redact_text


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-ABCDEFGH12345678",
        "sk-proj1234567890abcdefghij",
        "ghp_" + "a" * 36,
        "github_pat_" + "B" * 30,
        "xoxb-1234567890-abcdefghij",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
        "Bearer abcdef1234567890",
    ],
)
def test_token_shapes_are_masked(secret: str) -> None:
    out = redact_text(f"before {secret} after")
    assert secret not in out and MASK in out


def test_key_value_and_env_assignments() -> None:
    out = redact_text('API_KEY=hunter2hunter2 and "password": "swordfish1" and GITHUB_TOKEN: abcd1234')
    assert "hunter2" not in out and "swordfish" not in out and "abcd1234" not in out


def test_url_credentials() -> None:
    assert "pa55word" not in redact_text("postgres://admin:pa55word@db.local/x")


def test_private_key_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact_text(pem)


def test_plain_text_untouched() -> None:
    text = "Fix the token counting in parser.py (see sessions/metrics.py)"
    assert redact_text(text) == text


def test_mcp_definition_env_and_headers_masked() -> None:
    cfg = {
        "mcpServers": {
            "gh": {
                "command": "npx",
                "args": ["-y", "server"],
                "env": {"GITHUB_TOKEN": "ghp_" + "a" * 36, "DEBUG": "1"},
                "headers": {"Authorization": "Bearer abcdef1234567890"},
            }
        },
        "apiKey": "plainsecretvalue",
    }
    out = redact_obj(cfg)
    gh = out["mcpServers"]["gh"]
    assert gh["env"] == {"GITHUB_TOKEN": MASK, "DEBUG": MASK}
    assert gh["headers"] == {"Authorization": MASK}
    assert gh["command"] == "npx" and gh["args"] == ["-y", "server"]
    assert out["apiKey"] == MASK
