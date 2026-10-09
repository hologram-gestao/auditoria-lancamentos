"""Testes unitários do omie_fetch (BACK 8.2/8.3) — snapshot de categoria.

Foco (task 86e33bmkb): o `category_code` precisa atravessar do response Omie
até o `OmieMovement`, porque o job o persiste na divergência e ele é a ÚNICA
fonte de Categoria para títulos Atrasado/Previsto (que ficam fora do
`ListarExtrato` e portanto fora do enriquecimento em runtime).

Sem Postgres, sem respx: stub de `OmieClient` com dados mínimos.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.integrations.omie.schemas import (
    LancamentoExtrato,
    OmieTituloStatus,
    TituloAPagarReceber,
)
from app.modules.reconciliations.processing.omie_fetch import (
    fetch_pending,
    fetch_realized,
)


class _StubOmieClient:
    """Só os 3 métodos que o fetch usa; devolve listas fixas."""

    def __init__(
        self,
        *,
        extrato: list[LancamentoExtrato] | None = None,
        pagar: list[TituloAPagarReceber] | None = None,
        receber: list[TituloAPagarReceber] | None = None,
    ) -> None:
        self._extrato = extrato or []
        self._pagar = pagar or []
        self._receber = receber or []

    async def listar_extrato(self, **_: Any) -> list[LancamentoExtrato]:
        return self._extrato

    async def listar_contas_pagar(
        self, *, status: OmieTituloStatus, **_: Any
    ) -> list[TituloAPagarReceber]:
        # Devolve tudo só no ATRASADO pra não duplicar entre os 2 status.
        return self._pagar if status is OmieTituloStatus.ATRASADO else []

    async def listar_contas_receber(
        self, *, status: OmieTituloStatus, **_: Any
    ) -> list[TituloAPagarReceber]:
        return self._receber if status is OmieTituloStatus.ATRASADO else []


@pytest.mark.asyncio
async def test_fetch_realized_carries_category_code() -> None:
    extrato = [
        LancamentoExtrato.model_validate(
            {
                "nCodLancamento": 501,
                "cNatureza": "D",
                "dDataLancamento": "10/04/2026",
                "nValorDocumento": Decimal("55.00"),
                "cSituacao": "Conciliado",
                "cCodCategoria": "2.04.78",
                "cDesCategoria": "Ferramentas - DFA",
                "nCodCliente": 3057934498,
            }
        )
    ]
    client = _StubOmieClient(extrato=extrato)

    movements = await fetch_realized(
        client,  # type: ignore[arg-type]
        omie_conta_id=42,
        window_start=date(2026, 3, 29),
        window_end=date(2026, 5, 3),
    )

    assert len(movements) == 1
    assert movements[0].category_code == "2.04.78"
    assert movements[0].supplier_code == 3057934498


@pytest.mark.asyncio
async def test_fetch_pending_carries_category_code_and_signs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Título a pagar sai NEGATIVO, a receber POSITIVO — ambos com o código
    de categoria do response (`codigo_categoria`)."""

    # O plano de chamadas real dorme 1.5s entre cada uma das 4 — desnecessário
    # contra stub.
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(
        "app.modules.reconciliations.processing.omie_fetch.asyncio.sleep",
        _no_sleep,
    )

    pagar = [
        TituloAPagarReceber.model_validate(
            {
                "codigo_lancamento_omie": 601,
                "data_vencimento": "06/07/2026",
                "valor_documento": Decimal("300.00"),
                "codigo_categoria": "2.01.96",
                "codigo_cliente_fornecedor": 100001,
            }
        )
    ]
    receber = [
        TituloAPagarReceber.model_validate(
            {
                "codigo_lancamento_omie": 602,
                "data_vencimento": "08/07/2026",
                "valor_documento": Decimal("120.00"),
                "codigo_categoria": "1.01.02",
                "codigo_cliente_fornecedor": 200002,
            }
        )
    ]
    client = _StubOmieClient(pagar=pagar, receber=receber)

    movements = await fetch_pending(
        client,  # type: ignore[arg-type]
        omie_conta_id=42,
        reference_month=date(2026, 7, 1),
    )

    by_id = {m.omie_id: m for m in movements}
    assert by_id[601].amount == Decimal("-300.00")
    assert by_id[601].category_code == "2.01.96"
    assert by_id[601].supplier_code == 100001
    assert by_id[602].amount == Decimal("120.00")
    assert by_id[602].category_code == "1.01.02"
    assert by_id[602].supplier_code == 200002


class _RecordingOmieClient(_StubOmieClient):
    """Registra os filtros de cada chamada de títulos (86e3n70p0)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.title_calls: list[dict[str, Any]] = []

    async def listar_contas_pagar(
        self, *, status: OmieTituloStatus, **kwargs: Any
    ) -> list[TituloAPagarReceber]:
        self.title_calls.append({"kind": "pagar", "status": status, **kwargs})
        return await super().listar_contas_pagar(status=status)

    async def listar_contas_receber(
        self, *, status: OmieTituloStatus, **kwargs: Any
    ) -> list[TituloAPagarReceber]:
        self.title_calls.append({"kind": "receber", "status": status, **kwargs})
        return await super().listar_contas_receber(status=status)


def _titulo(omie_id: int, vencimento: str, valor: str) -> TituloAPagarReceber:
    return TituloAPagarReceber.model_validate(
        {
            "codigo_lancamento_omie": omie_id,
            "data_vencimento": vencimento,
            "valor_documento": Decimal(valor),
            "codigo_categoria": "2.04.78",
            "codigo_cliente_fornecedor": 100001,
        }
    )


class TestFetchPendingNoLoteDaFatura:
    """Modo "vencimento da fatura" do cartão: títulos ABERTOS da conta, sem filtro
    de data (o filtro do Omie é de inclusão/alteração), recortados aqui pelo
    vencimento dentro da janela do lote."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _sleep(_: float) -> None:
            return None

        monkeypatch.setattr(
            "app.modules.reconciliations.processing.omie_fetch.asyncio.sleep", _sleep
        )

    @pytest.mark.asyncio
    async def test_busca_sem_data_e_recorta_pelo_vencimento(self) -> None:
        # Parcelas do Anydesk: a de 10/10 está no lote, as seguintes não.
        pagar = [
            _titulo(701, "10/10/2026", "199.84"),
            _titulo(702, "10/11/2026", "199.84"),
            _titulo(703, "10/12/2026", "199.84"),
        ]
        client = _RecordingOmieClient(pagar=pagar)

        movements = await fetch_pending(
            client,  # type: ignore[arg-type]
            omie_conta_id=42,
            reference_month=date(2026, 9, 1),
            due_window=(date(2026, 10, 7), date(2026, 10, 13)),
        )

        assert [m.omie_id for m in movements] == [701]
        assert movements[0].amount == Decimal("-199.84")
        assert movements[0].transaction_date == date(2026, 10, 10)
        assert len(client.title_calls) == 4
        for call in client.title_calls:
            assert call["data_de"] is None
            assert call["data_ate"] is None
            assert call["conta_corrente_id"] == 42

    @pytest.mark.asyncio
    async def test_sem_lote_o_filtro_continua_sendo_o_mes(self) -> None:
        client = _RecordingOmieClient(pagar=[_titulo(801, "10/10/2026", "10.00")])

        movements = await fetch_pending(
            client,  # type: ignore[arg-type]
            omie_conta_id=42,
            reference_month=date(2026, 9, 1),
        )

        # O processo de sempre: mês nos dois filtros e nenhum recorte por vencimento.
        assert [m.omie_id for m in movements] == [801]
        for call in client.title_calls:
            assert call["data_de"] == date(2026, 9, 1)
            assert call["data_ate"] == date(2026, 9, 30)
