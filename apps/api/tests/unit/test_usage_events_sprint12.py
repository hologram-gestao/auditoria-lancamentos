"""Os dois eventos da Sprint 12: enum fechado, props estritas e emissor (BACK 12.2).

`movimentos_sincronizados` instrumenta o R0; `depara_aplicado` é **a métrica da
sprint** (cobertura do de-para). O risco dos dois é o mesmo da série: a tentação de
mandar "a categoria sem decisão" ou "o alvo escolhido" junto para facilitar o
diagnóstico poria o desenho contábil do cliente dentro do sink de métrica, que é o
lugar do sistema com menos proteção. Estes testes tentam as chaves proibidas pelo
nome e exigem a recusa.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    DeparaAplicadoProps,
    MovimentosSincronizadosProps,
    UsageEventName,
    UsageEventRequest,
)
from app.modules.usage_events.service import UsageEventService, decimal_to_cents

_ADAPTER: TypeAdapter[Any] = TypeAdapter(UsageEventRequest)
_CLIENT_ID = UUID("3f7b1e2a-0000-4000-8000-0000000000c1")

_S12_EVENTS = [UsageEventName.MOVIMENTOS_SINCRONIZADOS, UsageEventName.DEPARA_APLICADO]


class _Repo:
    """Repositório falso: captura o que chegaria ao `INSERT`."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def insert_ignore_duplicate(
        self, *, event: str, session_id: UUID | None, props: dict[str, Any]
    ) -> bool:
        self.rows.append({"event": event, "session_id": session_id, "props": props})
        return True


def _movimentos(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "competencia": "2026-06",
        "movimentos": 80,
        "sem_categoria": 0,
        "contas": 1,
    }
    return base | over


def _depara(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "destino": "demonstrativo_contabil",
        "valor_com_decisao_centavos": 9_820_00,
        "valor_nao_mapear_centavos": 1_200_00,
        "valor_sem_decisao_centavos": 180_00,
        "categorias_sem_decisao": 3,
    }
    return base | over


class TestEnumEForaDaDedup:
    def test_nomes_literais_do_prd(self) -> None:
        assert UsageEventName.MOVIMENTOS_SINCRONIZADOS.value == "movimentos_sincronizados"
        assert UsageEventName.DEPARA_APLICADO.value == "depara_aplicado"

    @pytest.mark.parametrize("event", _S12_EVENTS, ids=lambda e: e.value)
    def test_fora_da_allow_list_de_dedup(self, event: UsageEventName) -> None:
        """Cada sincronização/materialização é uma linha — dedup apagaria N-1."""
        assert event.value not in DEDUPED_EVENT_NAMES

    @pytest.mark.parametrize("event", _S12_EVENTS, ids=lambda e: e.value)
    def test_nao_sao_aceitos_do_browser(self, event: UsageEventName) -> None:
        """Aceitar do cliente deixaria forjar numerador E denominador da cobertura."""
        assert event not in CLIENT_EMITTED_EVENTS
        body = {"event": event.value, "session_id": str(uuid4()), "props": _depara()}
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(body)


class TestMovimentosSincronizados:
    def test_props_tem_exatamente_as_cinco_chaves(self) -> None:
        assert set(MovimentosSincronizadosProps.model_fields) == {
            "client_id",
            "competencia",
            "movimentos",
            "sem_categoria",
            "contas",
        }

    @pytest.mark.parametrize(
        "extra",
        [
            {"categorias": ["2.04.94"]},
            {"ids_movimentos": ["123"]},
            {"valor_total": 100},
            {"descricao": "PAGTO ACME"},
        ],
        ids=lambda e: next(iter(e)),
    )
    def test_chave_fora_da_whitelist_e_recusada(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            MovimentosSincronizadosProps(**_movimentos(**extra))

    @pytest.mark.parametrize("competencia", ["2026-13", "06/2026", "2026-6", "junho", ""])
    def test_competencia_fora_do_formato_e_recusada(self, competencia: str) -> None:
        with pytest.raises(ValidationError):
            MovimentosSincronizadosProps(**_movimentos(competencia=competencia))

    @pytest.mark.parametrize("campo", ["movimentos", "sem_categoria", "contas"])
    def test_contagem_negativa_e_recusada(self, campo: str) -> None:
        with pytest.raises(ValidationError):
            MovimentosSincronizadosProps(**_movimentos(**{campo: -1}))

    async def test_emissor_grava_competencia_como_yyyy_mm_e_sem_session(self) -> None:
        repo = _Repo()
        ok = await UsageEventService(repo).emit_movimentos_sincronizados(  # type: ignore[arg-type]
            client_id=_CLIENT_ID,
            competencia=date(2026, 6, 1),
            movimentos=80,
            sem_categoria=2,
            contas=3,
        )
        assert ok is True
        (row,) = repo.rows
        assert row["event"] == "movimentos_sincronizados"
        assert row["session_id"] is None
        assert row["props"] == {
            "client_id": str(_CLIENT_ID),
            "competencia": "2026-06",
            "movimentos": 80,
            "sem_categoria": 2,
            "contas": 3,
        }

    async def test_props_invalidas_nao_derrubam_a_sincronizacao_ja_gravada(self) -> None:
        """Retrabalho 12.2: o ano 999 sai do `strftime` como `'999-06'` e as props o
        recusam. O emissor roda DEPOIS do commit da base — devolve False e loga,
        nunca levanta (antes: 500 com a base já gravada)."""
        repo = _Repo()
        ok = await UsageEventService(repo).emit_movimentos_sincronizados(  # type: ignore[arg-type]
            client_id=_CLIENT_ID,
            competencia=date(999, 6, 1),
            movimentos=1,
            sem_categoria=0,
            contas=1,
        )
        assert ok is False
        assert repo.rows == []


class TestDeparaAplicado:
    def test_props_tem_exatamente_as_seis_chaves_do_outcome(self) -> None:
        assert set(DeparaAplicadoProps.model_fields) == {
            "client_id",
            "destino",
            "valor_com_decisao_centavos",
            "valor_nao_mapear_centavos",
            "valor_sem_decisao_centavos",
            "categorias_sem_decisao",
        }

    @pytest.mark.parametrize(
        "extra",
        [
            {"nome_categoria": "Despesas com IOF"},
            {"categoria": "2.04.94"},
            {"nome_alvo": "Receita Bruta de Vendas"},
            {"alvo": "1.01"},
            {"descricao": "PAGTO ACME"},
            {"categorias_sem_decisao_codigos": ["2.04.94"]},
        ],
        ids=lambda e: next(iter(e)),
    )
    def test_chave_proibida_e_recusada(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            DeparaAplicadoProps(**_depara(**extra))

    @pytest.mark.parametrize(
        "destino",
        [
            "Receita Bruta de Vendas",
            "Demonstrativo Contábil",
            "demonstrativo contabil",
            "DEMONSTRATIVO_CONTABIL",
            "1.01.01",
            "",
        ],
    )
    def test_destino_so_aceita_slug_de_tipo(self, destino: str) -> None:
        """Nome de alvo/categoria (espaço, acento, maiúscula, ponto) não passa."""
        with pytest.raises(ValidationError):
            DeparaAplicadoProps(**_depara(destino=destino))

    @pytest.mark.parametrize(
        "destino",
        [
            "demonstrativo_gerencial",
            "demonstrativo_contabil",
            "conta_contabil",
            "natureza_fiscal",
            "fluxo_de_caixa",
        ],
    )
    def test_os_cinco_tipos_seedados_sao_aceitos(self, destino: str) -> None:
        assert DeparaAplicadoProps(**_depara(destino=destino)).destino == destino

    @pytest.mark.parametrize(
        "campo",
        [
            "valor_com_decisao_centavos",
            "valor_nao_mapear_centavos",
            "valor_sem_decisao_centavos",
            "categorias_sem_decisao",
        ],
    )
    def test_negativo_e_recusado(self, campo: str) -> None:
        with pytest.raises(ValidationError):
            DeparaAplicadoProps(**_depara(**{campo: -1}))

    async def test_emissor_converte_decimal_em_centavos_inteiros(self) -> None:
        repo = _Repo()
        ok = await UsageEventService(repo).emit_depara_aplicado(  # type: ignore[arg-type]
            client_id=_CLIENT_ID,
            destino="demonstrativo_contabil",
            valor_com_decisao=Decimal("98200.00"),
            valor_nao_mapear=Decimal("12000.10"),
            valor_sem_decisao=Decimal("1800.05"),
            categorias_sem_decisao=3,
        )
        assert ok is True
        (row,) = repo.rows
        assert row["event"] == "depara_aplicado"
        assert row["session_id"] is None
        props = row["props"]
        assert props["valor_com_decisao_centavos"] == 9_820_000
        assert props["valor_nao_mapear_centavos"] == 1_200_010
        assert props["valor_sem_decisao_centavos"] == 180_005
        for chave in (
            "valor_com_decisao_centavos",
            "valor_nao_mapear_centavos",
            "valor_sem_decisao_centavos",
        ):
            assert type(props[chave]) is int, "centavos são int — nunca Decimal nem float"

    @pytest.mark.parametrize(
        "over",
        [{"destino": "Receita Bruta de Vendas"}, {"valor_com_decisao": Decimal("1.001")}],
        ids=["destino_invalido", "mais_de_2_casas"],
    )
    async def test_props_invalidas_nao_derrubam_a_materializacao_commitada(
        self, over: dict[str, Any]
    ) -> None:
        """Props recusadas (padrão do destino, `decimal_to_cents`) → False, sem levantar."""
        repo = _Repo()
        kwargs: dict[str, Any] = {
            "client_id": _CLIENT_ID,
            "destino": "demonstrativo_contabil",
            "valor_com_decisao": Decimal("1.00"),
            "valor_nao_mapear": Decimal("0"),
            "valor_sem_decisao": Decimal("0"),
            "categorias_sem_decisao": 0,
        } | over
        ok = await UsageEventService(repo).emit_depara_aplicado(**kwargs)  # type: ignore[arg-type]
        assert ok is False
        assert repo.rows == []


class TestDecimalParaCentavos:
    @pytest.mark.parametrize(
        ("valor", "centavos"),
        [
            (Decimal("0.00"), 0),
            (Decimal("0"), 0),
            (Decimal("12400.00"), 1_240_000),
            (Decimal("0.01"), 1),
            (Decimal("99999999999.99"), 9_999_999_999_999),
        ],
    )
    def test_conversao_exata(self, valor: Decimal, centavos: int) -> None:
        assert decimal_to_cents(valor) == centavos

    def test_mais_de_duas_casas_e_erro_nao_arredondamento(self) -> None:
        """Arredondar em silêncio mudaria a métrica sem ninguém ver."""
        with pytest.raises(ValueError, match="2 casas"):
            decimal_to_cents(Decimal("1.005"))
