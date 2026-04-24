"""Testes do logging estruturado e do redactor de segredos.

Critérios:
    - Toda key sensível tem valor substituído por [REDACTED].
    - Match é case-insensitive (`PASSWORD`, `Password`, `password` → todos pegos).
    - Match por substring (`omie_app_secret`, `x-api-key`, `set-cookie` → pegos).
    - Keys neutras (id, status, count) NÃO são afetadas.
    - O processor é idempotente.
"""

from __future__ import annotations

import pytest

from app.core.logging import _redact_sensitive


class TestRedactor:
    def test_password_key_is_redacted(self) -> None:
        out = _redact_sensitive(None, "info", {"password": "secret123"})
        assert out["password"] == "[REDACTED]"

    def test_uppercase_key_is_redacted(self) -> None:
        out = _redact_sensitive(None, "info", {"PASSWORD": "secret"})
        assert out["PASSWORD"] == "[REDACTED]"

    def test_mixed_case_key_is_redacted(self) -> None:
        out = _redact_sensitive(None, "info", {"Authorization": "Bearer xyz"})
        assert out["Authorization"] == "[REDACTED]"

    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "passwd",
            "pwd",
            "user_password",
            "token",
            "access_token",
            "refresh_token",
            "jwt",
            "api_key",
            "apikey",
            "x-api-key",
            "app_key",
            "app_secret",
            "omie_app_key_encrypted",
            "omie_app_secret_encrypted",
            "secret",
            "client_secret",
            "authorization",
            "cookie",
            "set-cookie",
            "encryption_key",
            "OMIE_ENCRYPTION_KEY",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        out = _redact_sensitive(None, "info", {key: "valor-secreto"})
        assert out[key] == "[REDACTED]", f"Key '{key}' deveria ser mascarada"

    @pytest.mark.parametrize(
        "key",
        ["user_id", "client_id", "status", "count", "method", "path", "duration_ms"],
    )
    def test_neutral_keys_are_preserved(self, key: str) -> None:
        out = _redact_sensitive(None, "info", {key: "valor-ok"})
        assert out[key] == "valor-ok", f"Key '{key}' deveria passar intacta"

    def test_multiple_keys_partial_redaction(self) -> None:
        event = {
            "user_id": "abc-123",
            "password": "secret",
            "duration_ms": 42,
            "authorization": "Bearer xyz",
        }
        out = _redact_sensitive(None, "info", event)
        assert out["user_id"] == "abc-123"
        assert out["duration_ms"] == 42
        assert out["password"] == "[REDACTED]"
        assert out["authorization"] == "[REDACTED]"

    def test_idempotent(self) -> None:
        """Aplicar 2x não deve alterar resultado."""
        event = {"password": "x", "user_id": "u"}
        out1 = _redact_sensitive(None, "info", event)
        out2 = _redact_sensitive(None, "info", out1)
        assert out1 == out2

    def test_empty_event(self) -> None:
        assert _redact_sensitive(None, "info", {}) == {}
