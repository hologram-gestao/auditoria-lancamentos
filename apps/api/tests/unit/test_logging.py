"""Testes do logging estruturado e do redactor de segredos.

Critérios:
    - Toda key sensível tem valor substituído por [REDACTED].
    - Match é case-insensitive (`PASSWORD`, `Password`, `password` → todos pegos).
    - Match por SEGMENTO (`omie_app_secret`, `x-api-key`, `set-cookie` → pegos;
      `input_tokens` → NÃO pego: é contagem, não credencial).
    - Keys neutras (id, status, count) NÃO são afetadas.
    - O processor é idempotente.
"""

from __future__ import annotations

import pytest

from app.core.logging import _redact_sensitive, sanitize_validation_errors


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
            "SEARCH_BLIND_INDEX_KEY",
            "search_blind_index_key",
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

    @pytest.mark.parametrize(
        "key",
        [
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "max_tokens",
            "max_output_tokens",
            "total_tokens",
            "tokens_used",
        ],
    )
    def test_token_counts_are_not_redacted(self, key: str) -> None:
        """Contagem de token é MÉTRICA, não credencial.

        O match por substring mascarava todas estas (todas contêm "token"), o que
        deixava o guardrail de custo da qualificação — `cached_input_tokens` em
        `qualification_semantic_batch_done` — inauditável em produção.
        """
        out = _redact_sensitive(None, "info", {key: 1785})
        assert out[key] == 1785, f"Key '{key}' é contagem e não deveria ser mascarada"

    @pytest.mark.parametrize(
        "key",
        ["token", "access_token", "refresh_token", "id_token", "accessToken", "authToken"],
    )
    def test_credential_tokens_are_still_redacted(self, key: str) -> None:
        """A contrapartida do teste acima: credencial continua mascarada.

        Inclui camelCase porque `accessToken` sem normalização viraria um
        segmento único e escaparia do match.
        """
        out = _redact_sensitive(None, "info", {key: "eyJhbGciOi..."})
        assert out[key] == "[REDACTED]", f"Key '{key}' é credencial e DEVE ser mascarada"

    def test_metric_and_credential_side_by_side(self) -> None:
        """O caso que motivou a correção: os dois no MESMO evento de log."""
        out = _redact_sensitive(
            None,
            "info",
            {
                "event": "qualification_semantic_batch_done",
                "input_tokens": 612,
                "output_tokens": 180,
                "cached_input_tokens": 1785,
                "glossary_block_chars": 1688,
                "access_token": "eyJhbGciOi...",
            },
        )
        assert out["input_tokens"] == 612
        assert out["output_tokens"] == 180
        assert out["cached_input_tokens"] == 1785
        assert out["glossary_block_chars"] == 1688
        assert out["access_token"] == "[REDACTED]"

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


class TestSanitizeValidationErrors:
    """Frente 1 de 86e2rtxcm: o log de validação não pode carregar o payload."""

    def test_input_and_ctx_are_dropped(self) -> None:
        errors = [
            {
                "type": "string_too_long",
                "loc": ("body", "user_note"),
                "msg": "String should have at most 2000 characters",
                "input": "SEGREDO-DO-CLIENTE-" + "x" * 2000,
                "ctx": {"max_length": 2000, "echo": "SEGREDO-DO-CLIENTE"},
                "url": "https://errors.pydantic.dev/2/v/string_too_long",
            }
        ]
        out = sanitize_validation_errors(errors)
        assert out == [
            {
                "type": "string_too_long",
                "loc": ("body", "user_note"),
                "msg": "String should have at most 2000 characters",
            }
        ]
        assert "SEGREDO-DO-CLIENTE" not in str(out)

    def test_allow_list_survives_missing_keys(self) -> None:
        # Erro sem `msg` (não deveria existir, mas o sanitizador não pode
        # explodir DENTRO do exception handler — isso mataria a resposta 400).
        out = sanitize_validation_errors([{"type": "missing", "loc": ("body",)}])
        assert out == [{"type": "missing", "loc": ("body",)}]

    def test_empty_errors(self) -> None:
        assert sanitize_validation_errors([]) == []

    def test_does_not_mutate_the_original(self) -> None:
        errors = [{"type": "t", "loc": ("body",), "msg": "m", "input": "SEGREDO"}]
        sanitize_validation_errors(errors)
        assert errors[0]["input"] == "SEGREDO"  # o chamador continua dono do dado


class TestRedactorRecursion:
    """Frente 2 de 86e2rtxcm: chave sensível aninhada não escapa mais."""

    def test_nested_dict_is_redacted(self) -> None:
        out = _redact_sensitive(None, "info", {"payload": {"password": "secret123"}})
        assert out["payload"]["password"] == "[REDACTED]"

    def test_list_of_dicts_is_redacted(self) -> None:
        event = {"errors": [{"loc": ("body",), "app_secret": "s3cr3t"}]}
        out = _redact_sensitive(None, "info", event)
        assert out["errors"][0]["app_secret"] == "[REDACTED]"
        assert out["errors"][0]["loc"] == ("body",)

    def test_tuple_stays_tuple(self) -> None:
        # `loc` do Pydantic é tuple; processors downstream não podem receber list.
        out = _redact_sensitive(None, "info", {"errors": [{"loc": ("body", "field")}]})
        assert isinstance(out["errors"][0]["loc"], tuple)

    def test_caller_structure_is_not_mutated(self) -> None:
        nested = {"password": "secret123"}
        _redact_sensitive(None, "info", {"payload": nested})
        assert nested["password"] == "secret123"  # cópia, nunca mutação in-place

    def test_depth_cap_fails_closed(self) -> None:
        deep: dict[str, object] = {"password": "leaf-secret"}
        for _ in range(12):
            deep = {"nested": deep}
        out = _redact_sensitive(None, "info", {"data": deep})
        assert "leaf-secret" not in str(out)  # além do teto vira [REDACTED] inteiro

    def test_circular_reference_does_not_hang(self) -> None:
        a: dict[str, object] = {}
        a["self"] = a
        out = _redact_sensitive(None, "info", {"data": a})
        assert "[REDACTED]" in str(out["data"])

    def test_nested_idempotent(self) -> None:
        event = {"payload": {"password": "x"}}
        once = _redact_sensitive(None, "info", event)
        twice = _redact_sensitive(None, "info", dict(once))
        assert twice["payload"]["password"] == "[REDACTED]"
