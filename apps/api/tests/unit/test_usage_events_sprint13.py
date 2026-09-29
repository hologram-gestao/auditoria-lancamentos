"""Instrumentação do Outcome da Sprint 13 (BACK 13.1).

A métrica é a mediana de `arquivo_contabil_gerado.created_at -
depara_aplicado.created_at`, casados por `materializacao_id`. As duas pontas nascem
aqui: a chave nova em `depara_aplicado` (o emissor da materialização passa o id da
linha criada — provado em `test_client_mapping_apply.py`) e o evento novo.

O risco do evento novo é o mesmo da série, com um agravante: o arquivo contábil
CARREGA o histórico do cliente final, e a tentação de mandar "o nome do layout" ou
"o histórico que foi" junto para facilitar o diagnóstico poria o dado do cliente no
sink de métrica. Estes testes tentam as chaves proibidas pelo nome e exigem a recusa.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from structlog.testing import capture_logs

from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    ArquivoContabilGeradoProps,
    UsageEventName,
    UsageEventRequest,
)
from app.modules.usage_events.service import UsageEventService

_ADAPTER: TypeAdapter[Any] = TypeAdapter(UsageEventRequest)
_CLIENT_ID = UUID("3f7b1e2a-0000-4000-8000-0000000000c1")
_MATERIALIZATION_ID = UUID("3f7b1e2a-0000-4000-8000-0000000000a1")
_LAYOUT_ID = UUID("3f7b1e2a-0000-4000-8000-0000000000b1")

_HISTORICO = "RECEBIMENTO REF. ALUGUEL IMOVEL, INQUILINO D LTDA - LOJA 04 - 06/2026"


class _Repo:
    """Repositório falso: captura o que chegaria ao `INSERT`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    async def insert_ignore_duplicate(
        self, *, event: str, session_id: UUID | None, props: dict[str, Any]
    ) -> bool:
        if self.fail:
            msg = f"INSERT INTO usage_events ... {props}"
            raise RuntimeError(msg)
        self.rows.append({"event": event, "session_id": session_id, "props": props})
        return True


def _props(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "destino": "conta_contabil",
        "competencia": "2026-08",
        "materializacao_id": _MATERIALIZATION_ID,
        "layout_id": _LAYOUT_ID,
        "layout_versao": 1,
        "linhas": 32,
        "valor_total_centavos": 5_357_099,
    }
    return base | over


def _kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "destino": "conta_contabil",
        "competencia": date(2026, 8, 1),
        "materializacao_id": _MATERIALIZATION_ID,
        "layout_id": _LAYOUT_ID,
        "layout_versao": 2,
        "linhas": 32,
        "valor_total": Decimal("53570.99"),
    }
    return base | over


class TestEnumEForaDaDedup:
    def test_nome_literal_do_prd(self) -> None:
        assert UsageEventName.ARQUIVO_CONTABIL_GERADO.value == "arquivo_contabil_gerado"

    def test_fora_da_allow_list_de_dedup(self) -> None:
        """Cada geração é uma linha: gerar de novo a mesma materialização é outro fato,
        e na allow-list a 2ª em diante sumiria (evento novo nasce SEM dedup)."""
        assert UsageEventName.ARQUIVO_CONTABIL_GERADO.value not in DEDUPED_EVENT_NAMES

    def test_nao_e_aceito_do_browser(self) -> None:
        """Aceitar do cliente deixaria forjar a ponta final da métrica."""
        assert UsageEventName.ARQUIVO_CONTABIL_GERADO not in CLIENT_EMITTED_EVENTS
        body = {
            "event": UsageEventName.ARQUIVO_CONTABIL_GERADO.value,
            "session_id": str(uuid4()),
            "props": {k: str(v) for k, v in _props().items()},
        }
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(body)


class TestProps:
    def test_exatamente_as_oito_chaves_do_prd(self) -> None:
        assert set(ArquivoContabilGeradoProps.model_fields) == {
            "client_id",
            "destino",
            "competencia",
            "materializacao_id",
            "layout_id",
            "layout_versao",
            "linhas",
            "valor_total_centavos",
        }

    def test_caminho_feliz(self) -> None:
        props = ArquivoContabilGeradoProps(**_props())
        assert props.materializacao_id == _MATERIALIZATION_ID
        assert props.valor_total_centavos == 5_357_099

    @pytest.mark.parametrize(
        "extra",
        [
            {"nome_layout": "Domínio: lançamentos contábeis (CSV)"},
            {"sistema_alvo": "Domínio"},
            {"historico": _HISTORICO},
            {"nome_cliente": "Cliente Exemplo LTDA"},
            {"categoria": "2.04.94"},
            {"categorias": ["2.04.94"]},
            {"nome_arquivo": "lancamentos_2026-08_v1.csv"},
            {"sha256": "0" * 64},
        ],
        ids=lambda e: next(iter(e)),
    )
    def test_chave_extra_e_recusada(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ArquivoContabilGeradoProps(**_props(**extra))

    @pytest.mark.parametrize(
        "destino",
        ["Conta contábil", "conta contabil", "CONTA_CONTABIL", _HISTORICO, "1.01", ""],
    )
    def test_destino_so_aceita_slug_de_tipo(self, destino: str) -> None:
        with pytest.raises(ValidationError):
            ArquivoContabilGeradoProps(**_props(destino=destino))

    @pytest.mark.parametrize("competencia", ["2026-13", "08/2026", "0000-08", _HISTORICO, ""])
    def test_competencia_fora_do_formato_e_recusada(self, competencia: str) -> None:
        with pytest.raises(ValidationError):
            ArquivoContabilGeradoProps(**_props(competencia=competencia))

    @pytest.mark.parametrize(
        "campo", ["materializacao_id", "layout_id", "client_id"], ids=lambda c: c
    )
    def test_id_so_aceita_uuid(self, campo: str) -> None:
        """Um id não é lugar para texto: nome de layout no lugar do id é recusado."""
        with pytest.raises(ValidationError):
            ArquivoContabilGeradoProps(**_props(**{campo: "Domínio"}))

    def test_versao_do_layout_comeca_em_1(self) -> None:
        with pytest.raises(ValidationError):
            ArquivoContabilGeradoProps(**_props(layout_versao=0))

    @pytest.mark.parametrize("campo", ["linhas", "valor_total_centavos"])
    def test_negativo_e_recusado(self, campo: str) -> None:
        with pytest.raises(ValidationError):
            ArquivoContabilGeradoProps(**_props(**{campo: -1}))

    def test_arquivo_vazio_e_valido(self) -> None:
        """Zero linhas e zero centavos são números honestos (`ge=0`), não ausência."""
        props = ArquivoContabilGeradoProps(**_props(linhas=0, valor_total_centavos=0))
        assert props.linhas == 0


class TestEmissor:
    async def test_grava_as_oito_chaves_em_centavos_e_sem_session(self) -> None:
        repo = _Repo()
        ok = await UsageEventService(repo).emit_arquivo_contabil_gerado(**_kwargs())  # type: ignore[arg-type]
        assert ok is True
        (row,) = repo.rows
        assert row["event"] == "arquivo_contabil_gerado"
        assert row["session_id"] is None
        assert row["props"] == {
            "client_id": str(_CLIENT_ID),
            "destino": "conta_contabil",
            "competencia": "2026-08",
            "materializacao_id": str(_MATERIALIZATION_ID),
            "layout_id": str(_LAYOUT_ID),
            "layout_versao": 2,
            "linhas": 32,
            "valor_total_centavos": 5_357_099,
        }
        assert type(row["props"]["valor_total_centavos"]) is int, "centavos são int"

    def test_assinatura_nao_tem_onde_caber_texto_livre(self) -> None:
        """Nenhum parâmetro de nome/histórico: o único `str` é o slug do destino."""
        params = inspect.signature(UsageEventService.emit_arquivo_contabil_gerado).parameters
        assert set(params) - {"self"} == {
            "client_id",
            "destino",
            "competencia",
            "materializacao_id",
            "layout_id",
            "layout_versao",
            "linhas",
            "valor_total",
        }

    @pytest.mark.parametrize(
        "over",
        [
            {"destino": _HISTORICO},
            {"valor_total": Decimal("1.001")},
            {"layout_versao": 0},
            {"linhas": -1},
            {"competencia": date(999, 8, 1)},
        ],
        ids=["destino_texto", "mais_de_2_casas", "versao_zero", "linhas_negativas", "ano_999"],
    )
    async def test_props_invalidas_viram_warning_nunca_excecao(self, over: dict[str, Any]) -> None:
        """Fail-soft: a geração já foi COMMITADA quando o emissor roda — prop recusada
        devolve False e loga só o nome do evento, sem valor nenhum."""
        repo = _Repo()
        with capture_logs() as logs:
            ok = await UsageEventService(repo).emit_arquivo_contabil_gerado(**_kwargs(**over))  # type: ignore[arg-type]
        assert ok is False
        assert repo.rows == []
        (entry,) = [e for e in logs if e["event"] == "usage_event_props_invalid"]
        assert entry["usage_event"] == "arquivo_contabil_gerado"
        dump = repr(logs)
        assert _HISTORICO not in dump
        assert str(_MATERIALIZATION_ID) not in dump

    async def test_falha_do_insert_nao_levanta_nem_loga_props(self) -> None:
        """O banco recusou (o texto da exceção carrega as props): False, warning sem props."""
        repo = _Repo(fail=True)
        with capture_logs() as logs:
            ok = await UsageEventService(repo).emit_arquivo_contabil_gerado(**_kwargs())  # type: ignore[arg-type]
        assert ok is False
        (entry,) = [e for e in logs if e["event"] == "usage_event_emit_failed"]
        assert entry["usage_event"] == "arquivo_contabil_gerado"
        assert str(_LAYOUT_ID) not in repr(logs)


class TestDeparaAplicadoTemAChaveDeCasamento:
    async def test_as_duas_pontas_carregam_o_mesmo_materializacao_id(self) -> None:
        """As duas linhas da MESMA materialização compartilham `materializacao_id` — é o
        casamento da fórmula. (A prova contra Postgres, com a materialização e a geração
        reais, é da BACK 13.4.)"""
        repo = _Repo()
        service = UsageEventService(repo)  # type: ignore[arg-type]
        await service.emit_depara_aplicado(
            client_id=_CLIENT_ID,
            destino="conta_contabil",
            competencia=date(2026, 8, 1),
            materializacao_id=_MATERIALIZATION_ID,
            valor_com_decisao=Decimal("53570.99"),
            valor_nao_mapear=Decimal("0.00"),
            valor_sem_decisao=Decimal("0.00"),
            categorias_sem_decisao=0,
        )
        await service.emit_arquivo_contabil_gerado(**_kwargs())
        depara, arquivo = repo.rows
        assert depara["event"] == "depara_aplicado"
        assert arquivo["event"] == "arquivo_contabil_gerado"
        assert (
            depara["props"]["materializacao_id"]
            == arquivo["props"]["materializacao_id"]
            == str(_MATERIALIZATION_ID)
        )
