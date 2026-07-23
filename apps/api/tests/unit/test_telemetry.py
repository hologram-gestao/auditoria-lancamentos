"""Testes dos emissores de instrumentação (Sprint 3, BACK 03.2).

Critérios:
    - `emit_acesso_negado` emite EXATAMENTE { user_id, client_id_alvo, rota }.
    - `emit_chave_rotacionada` emite EXATAMENTE { clientes_afetados, duracao_s }.
    - Nenhum campo extra além dos declarados.
    - Sem PII no output (só IDs/contadores) — nem nome, razão social, descrição.
    - O redactor do structlog cobre chaves sensíveis (defesa em profundidade).
"""

from __future__ import annotations

import structlog

from app.core.logging import _redact_sensitive
from app.core.telemetry import (
    EVENT_ACESSO_NEGADO,
    EVENT_CHAVE_ROTACIONADA,
    emit_acesso_negado,
    emit_chave_rotacionada,
)

# Chaves internas que o structlog.testing.LogCapture adiciona a cada entrada.
_STRUCTLOG_INTERNAL_KEYS = {"event", "log_level"}


class TestEmitAcessoNegado:
    def test_emits_exactly_declared_fields(self) -> None:
        with structlog.testing.capture_logs() as logs:
            emit_acesso_negado(
                user_id="user-1",
                client_id_alvo="client-77",
                rota="/api/v1/clients/client-77",
            )

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == EVENT_ACESSO_NEGADO
        assert entry["log_level"] == "warning"
        assert entry["user_id"] == "user-1"
        assert entry["client_id_alvo"] == "client-77"
        assert entry["rota"] == "/api/v1/clients/client-77"
        # Nenhum campo além dos declarados.
        assert set(entry) - _STRUCTLOG_INTERNAL_KEYS == {"user_id", "client_id_alvo", "rota"}

    def test_no_pii_in_output(self) -> None:
        with structlog.testing.capture_logs() as logs:
            emit_acesso_negado(user_id="u", client_id_alvo="c", rota="/r")

        serialized = str(logs[0]).lower()
        for forbidden in ("nome", "razao", "razão", "descr", "email", "cnpj"):
            assert forbidden not in serialized, f"PII '{forbidden}' vazou no evento"


class TestEmitChaveRotacionada:
    def test_emits_exactly_declared_fields(self) -> None:
        with structlog.testing.capture_logs() as logs:
            emit_chave_rotacionada(clientes_afetados=12, duracao_s=3.5)

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == EVENT_CHAVE_ROTACIONADA
        assert entry["log_level"] == "info"
        assert entry["clientes_afetados"] == 12
        assert entry["duracao_s"] == 3.5
        # Nenhum campo além dos declarados.
        assert set(entry) - _STRUCTLOG_INTERNAL_KEYS == {"clientes_afetados", "duracao_s"}

    def test_no_secret_fields(self) -> None:
        with structlog.testing.capture_logs() as logs:
            emit_chave_rotacionada(clientes_afetados=1, duracao_s=0.1)

        serialized = str(logs[0]).lower()
        for forbidden in ("dek", "key_id", "ciphertext", "secret"):
            assert forbidden not in serialized, f"segredo '{forbidden}' vazou no evento"


class TestRedactorCoversTelemetry:
    """Defesa em profundidade: se algum dia um campo sensível escorregar para um
    evento, o redactor global (já testado em test_logging) o mascara."""

    def test_sensitive_key_would_be_masked(self) -> None:
        out = _redact_sensitive(None, "info", {"event": EVENT_CHAVE_ROTACIONADA, "secret": "x"})
        assert out["secret"] == "[REDACTED]"

    def test_declared_id_fields_pass_through(self) -> None:
        out = _redact_sensitive(
            None,
            "warning",
            {"event": EVENT_ACESSO_NEGADO, "user_id": "u", "client_id_alvo": "c", "rota": "/r"},
        )
        assert out["user_id"] == "u"
        assert out["client_id_alvo"] == "c"
        assert out["rota"] == "/r"
