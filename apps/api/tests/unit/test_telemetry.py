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
    EVENT_ACESSO_CROSS_TENANT_NEGADO,
    EVENT_ACESSO_NEGADO,
    EVENT_CHAVE_ROTACIONADA,
    emit_acesso_cross_tenant_negado,
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


class TestEmitAcessoCrossTenantNegado:
    """Sprint 5 / R6 — o evento declarado no PRD, com EXATAMENTE 4 propriedades."""

    def test_emits_exactly_declared_fields(self) -> None:
        with structlog.testing.capture_logs() as logs:
            emit_acesso_cross_tenant_negado(
                user_scope="client",
                tenant_ator="tenant-A",
                tenant_alvo="tenant-B",
                rota="/api/v1/reconciliations/abc",
            )

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == EVENT_ACESSO_CROSS_TENANT_NEGADO
        assert entry["log_level"] == "warning"
        assert entry["user_scope"] == "client"
        assert entry["tenant_ator"] == "tenant-A"
        assert entry["tenant_alvo"] == "tenant-B"
        assert entry["rota"] == "/api/v1/reconciliations/abc"
        assert set(entry) - _STRUCTLOG_INTERNAL_KEYS == {
            "user_scope",
            "tenant_ator",
            "tenant_alvo",
            "rota",
        }

    def test_ator_system_nao_tem_tenant(self) -> None:
        """Usuário da equipe Hologram não pertence a tenant — `None`, não string vazia."""
        with structlog.testing.capture_logs() as logs:
            emit_acesso_cross_tenant_negado(
                user_scope="system",
                tenant_ator=None,
                tenant_alvo="tenant-B",
                rota="/r",
            )

        assert logs[0]["tenant_ator"] is None

    def test_no_pii_in_output(self) -> None:
        with structlog.testing.capture_logs() as logs:
            emit_acesso_cross_tenant_negado(
                user_scope="client", tenant_ator="a", tenant_alvo="b", rota="/r"
            )

        serialized = str(logs[0]).lower()
        for forbidden in ("nome", "razao", "razão", "descr", "email", "cnpj"):
            assert forbidden not in serialized, f"PII '{forbidden}' vazou no evento"


class TestEventoSobreviveAoRedactor:
    """O ponto cego que deixou `tenant_do_token` [REDACTED] em 100% das emissões.

    `structlog.testing.capture_logs()` SUBSTITUI a cadeia de processors inteira,
    então o redactor nunca roda nos testes acima — eles afirmavam que o campo
    saía com valor e passavam mesmo enquanto, em produção, ele saía mascarado.

    Os testes desta classe fecham esse buraco: exercitam a cadeia REAL. Se
    alguém renomear o campo de volta para algo terminado em `_token`, ou
    afrouxar a regra do redactor, aqui quebra.
    """

    def test_redactor_nao_mascara_o_tenant_do_ator(self) -> None:
        """`tenant_ator` é ID, não credencial — precisa chegar ao log com valor."""
        evento = {
            "event": EVENT_ACESSO_CROSS_TENANT_NEGADO,
            "user_scope": "client",
            "tenant_ator": "tenant-A",
            "tenant_alvo": "tenant-B",
            "rota": "/api/v1/clients/tenant-B/glossary",
        }
        out = _redact_sensitive(None, "warning", dict(evento))
        assert out["tenant_ator"] == "tenant-A", (
            "O tenant do ator saiu mascarado — é a dimensão que a S5/R6 acrescentou "
            "e sem ela a negação cross-tenant não diz DE ONDE veio a tentativa."
        )
        assert out["tenant_alvo"] == "tenant-B"
        assert out["user_scope"] == "client"
        assert out["rota"] == evento["rota"]

    def test_as_quatro_propriedades_passam_pelo_redactor(self) -> None:
        """Nenhuma das 4 do contrato do PRD pode ser engolida pelo redactor."""
        evento = {
            "user_scope": "client",
            "tenant_ator": "A",
            "tenant_alvo": "B",
            "rota": "/r",
        }
        out = _redact_sensitive(None, "warning", dict(evento))
        assert out == evento

    def test_credencial_no_mesmo_evento_continua_mascarada(self) -> None:
        """A contrapartida: liberar o ID do ator não afrouxa a regra de segredo."""
        out = _redact_sensitive(
            None,
            "warning",
            {"tenant_ator": "A", "access_token": "eyJhbGciOi...", "authorization": "Bearer x"},
        )
        assert out["tenant_ator"] == "A"
        assert out["access_token"] == "[REDACTED]"
        assert out["authorization"] == "[REDACTED]"
