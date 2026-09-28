"""Os dois eventos da Sprint 14: enum fechado, props estritas e emissores (BACK 14.2).

`arquivo_processado` instrumenta a ingestão (prova que o arquivo entrou — ou por que
não entrou); `fechamento_produzido` é **a métrica da sprint** (clientes sem ERP que
produzem um fechamento). O risco é o mesmo da série: mandar "a coluna que divergiu" ou
"o texto da linha inválida" junto para facilitar o diagnóstico poria o arquivo do
cliente dentro do sink. Estes testes tentam as chaves proibidas pelo nome, tentam
texto livre nos campos de formato fechado, e exigem a recusa — e provam que a linha da
RECUSA passa pela barreira de durabilidade (commit) enquanto a do aceito não.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    ArquivoProcessadoProps,
    FechamentoProduzidoProps,
    UsageEventName,
    UsageEventRequest,
)
from app.modules.usage_events.service import UsageEventService

pytestmark = pytest.mark.unit

_ADAPTER: TypeAdapter[Any] = TypeAdapter(UsageEventRequest)
_CLIENT_ID = UUID("3f7b1e2a-0000-4000-8000-0000000000c1")
_MAPPING_ID = UUID("3f7b1e2a-0000-4000-8000-0000000000a1")

_S14_EVENTS = [UsageEventName.ARQUIVO_PROCESSADO, UsageEventName.FECHAMENTO_PRODUZIDO]

#: O vocabulário do PRD + os códigos de recusa tipada da BACK 14.3, em minúsculas.
_MOTIVOS = {
    "nenhum",
    "sem_mapeamento",
    "formato_nao_suportado",
    "arquivo_invalido",
    "sinal_nao_declarado",
    "cabecalho_divergente",
    "linhas_invalidas",
    "total_divergente",
    "arquivo_ja_processado",
}


class _Repo:
    """Repositório falso: captura o que chegaria ao `INSERT` e se houve commit."""

    def __init__(self, *, fail_commit: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.commits = 0
        self._fail_commit = fail_commit

    async def insert_ignore_duplicate(
        self, *, event: str, session_id: UUID | None, props: dict[str, Any]
    ) -> bool:
        self.rows.append({"event": event, "session_id": session_id, "props": props})
        return True

    async def commit(self) -> None:
        if self._fail_commit:
            raise RuntimeError("commit falhou")
        self.commits += 1


def _arquivo(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "mapeamento_id": _MAPPING_ID,
        "linhas": 240,
        "colunas_reconhecidas": 6,
        "rejeitado": False,
        "motivo": "nenhum",
    }
    return base | over


def _fechamento(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "tipo_origem": "arquivo",
        "competencia": "2026-08",
    }
    return base | over


class TestEnumEForaDaDedup:
    def test_nomes_literais_do_prd(self) -> None:
        assert UsageEventName.ARQUIVO_PROCESSADO.value == "arquivo_processado"
        assert UsageEventName.FECHAMENTO_PRODUZIDO.value == "fechamento_produzido"

    @pytest.mark.parametrize("event", _S14_EVENTS, ids=lambda e: e.value)
    def test_fora_da_allow_list_de_dedup(self, event: UsageEventName) -> None:
        """Cada envio/materialização é uma linha — dedup apagaria N-1 (sem migration)."""
        assert event.value not in DEDUPED_EVENT_NAMES

    @pytest.mark.parametrize("event", _S14_EVENTS, ids=lambda e: e.value)
    def test_nao_sao_aceitos_do_browser(self, event: UsageEventName) -> None:
        """Aceitar do cliente deixaria forjar o numerador da métrica."""
        assert event not in CLIENT_EMITTED_EVENTS
        body = {"event": event.value, "session_id": str(uuid4()), "props": _fechamento()}
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(body)


class TestArquivoProcessado:
    def test_props_tem_exatamente_as_seis_chaves(self) -> None:
        assert set(ArquivoProcessadoProps.model_fields) == {
            "client_id",
            "mapeamento_id",
            "linhas",
            "colunas_reconhecidas",
            "rejeitado",
            "motivo",
        }

    def test_motivo_e_o_vocabulario_fechado(self) -> None:
        """A MESMA lista dos códigos de recusa tipada (uma fonte), mais `nenhum`."""
        for motivo in _MOTIVOS:
            kwargs = _arquivo(rejeitado=motivo != "nenhum", motivo=motivo)
            assert ArquivoProcessadoProps(**kwargs).motivo == motivo

    @pytest.mark.parametrize(
        "motivo",
        ["Cabeçalho divergente", "coluna Histórico mudou", "CABECALHO_DIVERGENTE", "", "outro"],
    )
    def test_motivo_fora_do_vocabulario_e_recusado(self, motivo: str) -> None:
        with pytest.raises(ValidationError):
            ArquivoProcessadoProps(**_arquivo(rejeitado=True, motivo=motivo))

    @pytest.mark.parametrize(
        "extra",
        [
            {"coluna": "Histórico"},
            {"colunas": ["Data", "Valor"]},
            {"linha": 17},
            {"conteudo": "PAGTO ACME"},
            {"descricao": "PAGTO ACME"},
            {"nome_arquivo": "agosto.xlsx"},
            {"mensagem": "valor não numérico na linha 17"},
        ],
        ids=lambda e: next(iter(e)),
    )
    def test_chave_fora_da_whitelist_e_recusada(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ArquivoProcessadoProps(**_arquivo(**extra))

    @pytest.mark.parametrize(
        "over",
        [
            {"rejeitado": True, "motivo": "nenhum"},
            {"rejeitado": False, "motivo": "linhas_invalidas"},
        ],
        ids=["rejeitado_sem_motivo", "aceito_com_motivo"],
    )
    def test_rejeitado_e_motivo_sao_coerentes(self, over: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ArquivoProcessadoProps(**_arquivo(**over))

    @pytest.mark.parametrize("campo", ["linhas", "colunas_reconhecidas"])
    def test_contagem_negativa_e_recusada(self, campo: str) -> None:
        with pytest.raises(ValidationError):
            ArquivoProcessadoProps(**_arquivo(**{campo: -1}))

    def test_mapeamento_id_pode_ser_nulo(self) -> None:
        """Recusa ANTES de haver mapeamento (`sem_mapeamento`): sem id para citar."""
        props = ArquivoProcessadoProps(
            **_arquivo(mapeamento_id=None, rejeitado=True, motivo="sem_mapeamento", linhas=0)
        )
        assert props.mapeamento_id is None

    async def test_emissor_do_aceito_grava_sem_commit(self) -> None:
        repo = _Repo()
        ok = await UsageEventService(repo).emit_arquivo_processado(  # type: ignore[arg-type]
            client_id=_CLIENT_ID,
            mapeamento_id=_MAPPING_ID,
            linhas=240,
            colunas_reconhecidas=6,
            rejeitado=False,
            motivo="nenhum",
        )
        assert ok is True
        (row,) = repo.rows
        assert row["event"] == "arquivo_processado"
        assert row["session_id"] is None
        assert row["props"] == {
            "client_id": str(_CLIENT_ID),
            "mapeamento_id": str(_MAPPING_ID),
            "linhas": 240,
            "colunas_reconhecidas": 6,
            "rejeitado": False,
            "motivo": "nenhum",
        }
        # O aceito roda DEPOIS do commit da base: o request commita no fim.
        assert repo.commits == 0

    async def test_emissor_da_recusa_commita_antes_de_devolver(self) -> None:
        """A linha `rejeitado=true` precisa sobreviver ao `raise` que vem em seguida."""
        repo = _Repo()
        ok = await UsageEventService(repo).emit_arquivo_processado(  # type: ignore[arg-type]
            client_id=_CLIENT_ID,
            mapeamento_id=_MAPPING_ID,
            linhas=0,
            colunas_reconhecidas=5,
            rejeitado=True,
            motivo="cabecalho_divergente",
        )
        assert ok is True
        assert repo.commits == 1
        assert repo.rows[0]["props"]["motivo"] == "cabecalho_divergente"

    async def test_commit_que_falha_e_fail_soft(self, caplog: pytest.LogCaptureFixture) -> None:
        repo = _Repo(fail_commit=True)
        with caplog.at_level(logging.WARNING):
            ok = await UsageEventService(repo).emit_arquivo_processado(  # type: ignore[arg-type]
                client_id=_CLIENT_ID,
                mapeamento_id=None,
                linhas=0,
                colunas_reconhecidas=0,
                rejeitado=True,
                motivo="formato_nao_suportado",
            )
        assert ok is False

    async def test_props_invalidas_nao_derrubam_o_fluxo(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Motivo fora do vocabulário (erro de quem chamou) → False + warning sem valores."""
        repo = _Repo()
        with caplog.at_level(logging.WARNING):
            ok = await UsageEventService(repo).emit_arquivo_processado(  # type: ignore[arg-type]
                client_id=_CLIENT_ID,
                mapeamento_id=_MAPPING_ID,
                linhas=3,
                colunas_reconhecidas=6,
                rejeitado=True,
                motivo="coluna Histórico virou Descrição",  # type: ignore[arg-type]
            )
        assert ok is False
        assert repo.rows == []
        assert repo.commits == 0
        # O log não carrega nem o motivo, nem nome de coluna, nem conteúdo.
        assert "Histórico" not in caplog.text
        assert "Descrição" not in caplog.text


class TestFechamentoProduzido:
    def test_props_tem_exatamente_as_tres_chaves(self) -> None:
        assert set(FechamentoProduzidoProps.model_fields) == {
            "client_id",
            "tipo_origem",
            "competencia",
        }

    @pytest.mark.parametrize("tipo_origem", ["arquivo", "omie", "outro_erp"])
    def test_tipo_origem_slug_aceito(self, tipo_origem: str) -> None:
        assert FechamentoProduzidoProps(**_fechamento(tipo_origem=tipo_origem)).tipo_origem == (
            tipo_origem
        )

    @pytest.mark.parametrize(
        "tipo_origem",
        ["Planilha Agosto", "Arquivo", "arquivo csv", "1.01.01", "agosto.xlsx", ""],
    )
    def test_tipo_origem_com_espaco_acento_ou_ponto_e_recusado(self, tipo_origem: str) -> None:
        with pytest.raises(ValidationError):
            FechamentoProduzidoProps(**_fechamento(tipo_origem=tipo_origem))

    @pytest.mark.parametrize("competencia", ["2026-13", "1.01.01", "08/2026", "0000-08", ""])
    def test_competencia_fora_do_formato_e_recusada(self, competencia: str) -> None:
        with pytest.raises(ValidationError):
            FechamentoProduzidoProps(**_fechamento(competencia=competencia))

    @pytest.mark.parametrize(
        "extra",
        [
            {"destino": "demonstrativo_contabil"},
            {"categorias": ["arq-1"]},
            {"nome_cliente": "Padaria"},
            {"organization_id": str(uuid4())},
        ],
        ids=lambda e: next(iter(e)),
    )
    def test_chave_fora_da_whitelist_e_recusada(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            FechamentoProduzidoProps(**_fechamento(**extra))

    async def test_emissor_grava_competencia_como_yyyy_mm(self) -> None:
        repo = _Repo()
        ok = await UsageEventService(repo).emit_fechamento_produzido(  # type: ignore[arg-type]
            client_id=_CLIENT_ID, tipo_origem="arquivo", competencia=date(2026, 8, 1)
        )
        assert ok is True
        (row,) = repo.rows
        assert row["event"] == "fechamento_produzido"
        assert row["session_id"] is None
        assert row["props"] == {
            "client_id": str(_CLIENT_ID),
            "tipo_origem": "arquivo",
            "competencia": "2026-08",
        }
        assert repo.commits == 0

    async def test_props_invalidas_nao_derrubam_a_materializacao_commitada(self) -> None:
        repo = _Repo()
        ok = await UsageEventService(repo).emit_fechamento_produzido(  # type: ignore[arg-type]
            client_id=_CLIENT_ID, tipo_origem="Planilha Agosto", competencia=date(2026, 8, 1)
        )
        assert ok is False
        assert repo.rows == []
