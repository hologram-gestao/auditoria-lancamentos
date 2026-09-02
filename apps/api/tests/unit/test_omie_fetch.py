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
            }
        )
    ]
    client = _StubOmieClient(extrato=extrato)

    movements = await fetch_realized(
        client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        tolerance_days=3,
    )

    assert len(movements) == 1
    assert movements[0].category_code == "2.04.78"


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
    assert by_id[602].amount == Decimal("120.00")
    assert by_id[602].category_code == "1.01.02"
