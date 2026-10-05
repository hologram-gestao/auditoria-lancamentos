"""Testes do logging estruturado e do redactor de segredos.

Critérios:
    - Toda key sensível tem valor substituído por [REDACTED].
    - Match é case-insensitive (`PASSWORD`, `Password`, `password` → todos pegos).
    - Match por SEGMENTO (`omie_app_secret`, `x-api-key`, `set-cookie` → pegos;
      `input_tokens` → NÃO pego: é contagem, não credencial).
    - Keys neutras (id, status, count) NÃO são afetadas.
    - O processor é idempotente.
    - Trecho com FORMA de segredo é mascarado sob qualquer chave (86e3anx7y), e
      o 64 hex sob chave de hash (`file_hash`, `sha256`) continua legível.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
import structlog

from app.core.logging import (
    MAX_VALUE_SCAN_CHARS,
    _redact_sensitive,
    build_processors,
    sanitize_validation_errors,
)


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
            "url",
            "webhook",
            "webhook_url",
            "LEADS_SLACK_WEBHOOK_URL",
            "database_url",
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


# Segredos FALSOS montados por concatenação: o texto literal no repositório não
# casa com o padrão dos scanners de segredo (GitHub push protection), e o teto
# de comprimento deles não importa aqui, só a forma que o redator reconhece.
_JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjE3MDB9" + ".c2lnbmF0dXJlLWZha2VfLXg"
_ANTHROPIC_KEY = "sk-" + "ant-api03-" + "fakeKeyForTests_0123456789-abc"
_SLACK_WEBHOOK = "https://hooks." + "slack.com/services/TFAKE/BFAKE/" + "fake-token-for-tests"
_DISCORD_WEBHOOK = "https://discord.com/api/" + "webhooks/123456/" + "fake-token-for-tests"
_HEX64 = "0f" * 32  # forma de OMIE_ENCRYPTION_KEY / JWT_SECRET / SHA-256
_BASIC = "Basic " + "dXN1YXJpbzpzZW5oYS1zZWNyZXRh"  # base64("usuario:senha-secreta")


def _redact(event: dict[str, Any]) -> dict[str, Any]:
    return _redact_sensitive(None, "info", dict(event))


class TestRedactorByValue:
    """86e3anx7y: segredo sob chave INOCENTE também é mascarado, pela forma."""

    @pytest.mark.parametrize(
        ("secret", "name"),
        [
            (_JWT, "jwt"),
            (_ANTHROPIC_KEY, "anthropic"),
            (_SLACK_WEBHOOK, "slack"),
            (_DISCORD_WEBHOOK, "discord"),
            ("Bearer " + "abc.DEF-123_xyz~tok", "bearer"),
            ("bearer " + "abc.DEF-123_xyz~tok", "bearer-minusculo"),
            (_BASIC, "basic"),
            (_HEX64, "hex64"),
        ],
    )
    def test_each_format_is_masked_under_a_neutral_key(self, secret: str, name: str) -> None:
        out = _redact({"detail": f"antes {secret} depois"})
        assert out["detail"] == "antes [REDACTED] depois", name

    def test_only_the_matching_stretch_is_masked(self) -> None:
        out = _redact({"error": f"falha ao postar em {_SLACK_WEBHOOK}"})
        assert out["error"] == "falha ao postar em [REDACTED]"

    def test_webhook_without_scheme_is_masked(self) -> None:
        bare = _SLACK_WEBHOOK.removeprefix("https://")
        assert _redact({"error": f"host {bare}"})["error"] == "host [REDACTED]"

    def test_event_string_itself_is_scanned(self) -> None:
        out = _redact({"event": f"token recebido {_JWT}"})
        assert out["event"] == "token recebido [REDACTED]"

    def test_nested_list_and_dict_are_scanned(self) -> None:
        out = _redact({"payload": {"items": [f"x {_ANTHROPIC_KEY}", ("t", _JWT)]}})
        assert out["payload"]["items"][0] == "x [REDACTED]"
        assert out["payload"]["items"][1] == ("t", "[REDACTED]")

    def test_several_secrets_in_one_string(self) -> None:
        out = _redact({"detail": f"{_JWT} e {_DISCORD_WEBHOOK}"})
        assert out["detail"] == "[REDACTED] e [REDACTED]"

    def test_hex_inside_a_non_credential_basic_is_still_masked(self) -> None:
        # "Basic <palavra>" não é credencial, mas a palavra pode ser uma chave hex.
        out = _redact({"detail": f"Basic {_HEX64}"})
        assert out["detail"] == "Basic [REDACTED]"

    # --- Falsos positivos que TÊM de sobreviver ---------------------------------

    @pytest.mark.parametrize("key", ["file_hash", "sha256", "input_hash", "hexdigest", "fileHash"])
    def test_hex64_under_a_hash_key_survives(self, key: str) -> None:
        """O `file_hash` é a chave da dedup; mascará-lo apagaria o suporte."""
        assert _redact({key: _HEX64})[key] == _HEX64

    def test_hex64_list_under_a_hash_key_survives(self) -> None:
        out = _redact({"file_hashes": [_HEX64, _HEX64.upper()]})
        assert out["file_hashes"] == [_HEX64, _HEX64.upper()]

    def test_hex64_nested_under_a_hash_key_survives(self) -> None:
        out = _redact({"files": [{"file_hash": _HEX64, "detail": _HEX64}]})
        assert out["files"][0]["file_hash"] == _HEX64
        assert out["files"][0]["detail"] == "[REDACTED]"  # mesma forma, chave neutra

    def test_hash_key_exempts_only_the_hex64(self) -> None:
        # A exceção é do formato de 64 hex, não da chave inteira.
        assert _redact({"file_hash": _JWT})["file_hash"] == "[REDACTED]"

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("client_id", "3f2b8c1e-9a4d-4e7b-8c2f-1a2b3c4d5e6f"),
            ("request_id", "3f2b8c1e9a4d4e7b8c2f1a2b3c4d5e6f"),  # UUID sem hífen: 32 hex
            ("route", "/api/v1/clients"),
            ("path", "/api/v1/clients/3f2b8c1e-9a4d-4e7b-8c2f-1a2b3c4d5e6f/titles"),
            ("detail", "https://api.omie.com.br/api/v1/financas/extrato/"),
            ("detail", "eyJhbGciOi..."),  # JWT truncado: base64 curto, não credencial
            ("detail", "eyJ0eXAiOiJKV1QifQ"),  # um segmento só
            ("detail", "Basic setup concluído"),
            ("detail", "bearer curto"),
            ("hash_prefix", "0f0f0f0f"),
            ("detail", "f" * 63),
            ("detail", "f" * 65),
            ("event", "reconciliation_check_duplicate"),
        ],
    )
    def test_false_positives_survive(self, key: str, value: str) -> None:
        assert _redact({key: value})[key] == value

    @pytest.mark.parametrize(
        "key", ["input_tokens", "cached_input_tokens", "duration_ms", "linhas"]
    )
    def test_numbers_are_untouched(self, key: str) -> None:
        assert _redact({key: 1785})[key] == 1785

    def test_idempotent(self) -> None:
        event = {"detail": f"falha {_SLACK_WEBHOOK} com {_JWT}", "file_hash": _HEX64}
        once = _redact(event)
        twice = _redact(once)
        assert once == twice
        assert twice["detail"] == "falha [REDACTED] com [REDACTED]"

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("x" * 100 * 1024, id="corrida-de-letras"),
            pytest.param("eyJ" * (100 * 1024 // 3), id="corrida-base64url-eyJ"),
            pytest.param("f" * 100 * 1024, id="corrida-hex"),
            pytest.param("a." * (50 * 1024), id="letras-e-pontos"),
            pytest.param(
                ("eyJ" + "a" * 200 + " " + "f" * 63 + " Basic setup bearer x " * 3)
                * (100 * 1024 // 340),
                id="misto",
            ),
        ],
    )
    def test_long_string_without_secret_is_linear(self, text: str) -> None:
        """100 KB sem segredo, nas formas que os padrões consomem: sem backtracking.

        A corrida SEM espaço é o pior caso: um padrão com quantificador opcional
        no início (`(?:[a-z]+\\.)?discord`) varre a corrida inteira a partir de
        cada posição, e foi o que fez 256 KB de `x` levarem 74 s na primeira
        versão. O teto do tempo é largo para não ficar instável em runner lento;
        uma regressão quadrática passa dele por ordens de grandeza.
        """
        start = time.perf_counter()
        out = _redact({"detail": text})
        elapsed = time.perf_counter() - start
        assert out["detail"] == text
        assert elapsed < 0.5, f"varredura de {len(text)} caracteres levou {elapsed:.3f}s"

    def test_string_above_the_cap_has_the_tail_masked(self) -> None:
        """Acima do teto, o excedente vira [REDACTED] sem ser lido (fail-closed)."""
        head = "x" * MAX_VALUE_SCAN_CHARS
        out = _redact({"detail": head + f" {_SLACK_WEBHOOK}"})
        assert out["detail"] == head + "[REDACTED]"
        assert "slack" not in out["detail"]

    def test_string_at_the_cap_is_scanned_whole(self) -> None:
        text = "x" * (MAX_VALUE_SCAN_CHARS - 1 - len(_JWT)) + " " + _JWT
        assert len(text) == MAX_VALUE_SCAN_CHARS
        out = _redact({"detail": text})
        assert out["detail"].endswith(" [REDACTED]")


class TestRedactorPositionInTheChain:
    """O texto da exceção nasce em `format_exc_info`; o redator tem de vir depois."""

    @staticmethod
    def _render(event: dict[str, Any]) -> str:
        """Roda a cadeia de produção inteira, renderer JSON incluído."""
        rendered: Any = event
        for processor in build_processors(is_prod=True):
            rendered = processor(None, "error", rendered)
        assert isinstance(rendered, str)
        return rendered

    def test_redactor_runs_after_format_exc_info(self) -> None:
        processors = build_processors(is_prod=True)
        assert processors.index(_redact_sensitive) > processors.index(
            structlog.processors.format_exc_info
        )

    def test_exception_text_with_webhook_url_is_masked(self) -> None:
        try:
            raise ConnectionError(f"All connection attempts failed: {_SLACK_WEBHOOK}")
        except ConnectionError as exc:
            output = self._render({"event": "alert_webhook_failed", "exc_info": exc})
        payload = json.loads(output)
        assert "hooks.slack.com" not in output
        assert "[REDACTED]" in payload["exception"]
        assert "ConnectionError" in payload["exception"]  # o diagnóstico continua

    def test_key_redaction_still_works_after_format_exc_info(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            output = self._render(
                {"event": "x", "password": "senha-123", "file_hash": _HEX64, "exc_info": exc}
            )
        payload = json.loads(output)
        assert payload["password"] == "[REDACTED]"
        assert payload["file_hash"] == _HEX64
        assert "senha-123" not in output
