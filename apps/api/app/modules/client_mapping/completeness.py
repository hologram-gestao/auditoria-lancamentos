"""Completude de partida de uma materialização — função PURA (Sprint 16, BACK 16.4 — outcome).

    completude = Σ|valor| das linhas com PARTIDA COMPLETA ÷ Σ|valor| das linhas com ALVO

por materialização do destino `conta_contabil`. É a métrica da sprint, e ela NÃO é
evento: sai da própria materialização, por consulta sobre o SNAPSHOT dos itens — que é
imutável, então o número de uma materialização nunca muda (nem depois do encerramento,
que purga as decisões: a presença do histórico também está no snapshot, 16.3).

Um cálculo só, sem segundo critério:
  - "partida completa" é o predicado ÚNICO da 16.3 (`partida.is_partida_completa`);
  - o percentual é o `_pct` da S12 (ADR-077-BE): `Decimal`, quantizado a 0,01 HALF_EVEN,
    e denominador zero → `None` — nunca um "0%" com cara de resultado quando não há
    linha com alvo.

Serve à prévia (itens calculados) e à listagem/leitura das materializações (itens do
snapshot), sobre o MESMO protocolo de linha.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.db.models import MaterializedSituation
from app.modules.client_mapping.apply import _pct
from app.modules.client_mapping.partida import PartidaLine, is_partida_completa

_ZERO = Decimal("0.00")


class CompletenessLine(PartidaLine, Protocol):
    @property
    def amount(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class PartidaCompleteness:
    """O numerador, o denominador e o percentual — os três, para a conta ser conferível."""

    complete_amount: Decimal
    target_amount: Decimal
    pct: Decimal | None


def partida_completeness(lines: Iterable[CompletenessLine]) -> PartidaCompleteness:
    """Agrega as linhas: Σ|valor| com alvo (denominador) e, delas, as de partida completa."""
    complete = _ZERO
    target = _ZERO
    for line in lines:
        if (
            str(getattr(line.situation, "value", line.situation))
            != MaterializedSituation.ALVO.value
        ):
            continue
        target += abs(line.amount)
        if is_partida_completa(line):
            complete += abs(line.amount)
    return PartidaCompleteness(
        complete_amount=complete, target_amount=target, pct=_pct(complete, target)
    )
