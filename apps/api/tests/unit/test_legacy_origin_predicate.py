"""O predicado do cliente LEGADO, em Python e em SQL (S9, BACK 09.5 — retrabalho R1).

`resolve_origin_connections` decide em Python, por cliente; `origin_status` é
derivado por CONTAGEM, na mesma query da listagem, para não virar N+1. São duas
leituras do MESMO fato — "este cliente ainda opera pelas colunas antigas" — e
elas precisam concordar. Aqui se trava a concordância: os nomes das colunas saem
de UMA tupla, e a cláusula SQL é conferida pelo texto renderizado.

Sem isto, a divergência aparece só em produção, no formato mais caro: a tela
bloqueando "Nova conciliação" para um cliente que o backend serve normalmente.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.db.models import Client
from app.modules.client_connections.legacy_fallback import (
    _LEGACY_CREDENTIAL_COLUMNS,
    _has_legacy_credentials,
    clients_pending_conversion,
    legacy_origin_available,
)

#: Os 4 nomes, escritos à mão de propósito: se alguém mexer na tupla do módulo,
#: este teste é que tem de discordar.
NOMES_ESPERADOS = (
    "omie_app_key_encrypted",
    "omie_app_secret_encrypted",
    "omie_app_key_iv",
    "omie_app_secret_iv",
)


def _render(expression: object) -> str:
    compiled = expression.compile(  # type: ignore[attr-defined]
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    return " ".join(str(compiled).split())


class TestFonteUnicaDasColunas:
    def test_a_tupla_tem_exatamente_as_quatro_colunas(self) -> None:
        assert tuple(c.key for c in _LEGACY_CREDENTIAL_COLUMNS) == NOMES_ESPERADOS

    def test_o_predicado_python_le_a_tupla(self) -> None:
        """Preenchido de verdade = os 4 campos. Faltar UM já é ausente."""
        cheio = dict.fromkeys(NOMES_ESPERADOS, "x")
        assert _has_legacy_credentials(Client(name="Legado", **cheio)) is True
        for faltante in NOMES_ESPERADOS:
            parcial = dict(cheio)
            parcial[faltante] = None
            assert _has_legacy_credentials(Client(name="Parcial", **parcial)) is False

    def test_string_vazia_do_encerrado_conta_como_ausente(self) -> None:
        """Cliente ENCERRADO grava `''` (crypto-shredding §4.12): nada a decifrar."""
        vazio = dict.fromkeys(NOMES_ESPERADOS, "")
        assert _has_legacy_credentials(Client(name="Encerrado", **vazio)) is False


class TestClausulaSQL:
    def test_a_clausula_espelha_o_ramo_que_sintetiza(self) -> None:
        sql = _render(legacy_origin_available())
        # As MESMAS três condições do fallback, nesta ordem.
        assert "clients.closed_at IS NULL" in sql
        for nome in NOMES_ESPERADOS:
            assert f"clients.{nome} IS NOT NULL" in sql
            assert f"clients.{nome} != ''" in sql
        # Precedência: quem tem QUALQUER conexão gravada usa conexão.
        assert "NOT (EXISTS" in sql
        assert "client_connections.client_id = clients.id" in sql
        # E sem filtro de tipo de provedor — conexão de qualquer tipo já tira o
        # cliente do fallback, exatamente como `resolve_origin_connections`.
        assert "provider_type" not in sql

    def test_pendencia_de_conversao_olha_so_o_ciphertext(self) -> None:
        """Divergência DELIBERADA (não esquecimento): linha com ciphertext e IV
        nulo é corrupção e tem de seguir contando como pendente, para o
        `--verify` sair FAIL em vez de promover o fallback por cima dela."""
        sql = _render(clients_pending_conversion())
        assert "clients.omie_app_key_encrypted IS NOT NULL" in sql
        assert "clients.omie_app_key_iv" not in sql
        # Aqui, sim, o tipo importa: o que se converte é a credencial do Omie.
        assert "provider_type" in sql


class TestDerivacaoConcordaComOFallback:
    def test_os_tres_casos_que_a_contagem_precisa_acertar(self) -> None:
        """Tabela-verdade do que o `CASE` faz, escrita como o fallback a lê."""
        base = dict.fromkeys(NOMES_ESPERADOS, "x")
        legado = Client(id=uuid4(), name="Legado", **base)
        janela = Client(id=uuid4(), name="Nasceu na janela")
        encerrado = Client(id=uuid4(), name="Encerrado", **dict.fromkeys(NOMES_ESPERADOS, ""))

        assert _has_legacy_credentials(legado) is True
        assert _has_legacy_credentials(janela) is False
        assert _has_legacy_credentials(encerrado) is False
