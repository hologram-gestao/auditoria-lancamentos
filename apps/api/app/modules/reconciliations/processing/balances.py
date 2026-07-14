"""Cálculo dos saldos agregados de uma reconciliation_session.

Por que isto vive separado de `job.py`:
    A lógica é pura (sem I/O), tem várias guardas contra dados ausentes
    e merece testes unitários sem subir DB nem mockar Omie.

Cálculos:
    - `balance_start`: saldo ANTES da primeira movimentação do extrato.
      Derivado de `first.balance - first.amount` (a coluna `balance` no
      file_entry é o saldo APÓS a movimentação; subtrair o amount devolve
      o saldo prévio). Se a primeira linha não tem `balance` (IA falhou
      em extrair a coluna saldo do extrato), devolve None — os derivados
      degradam em cascata.

    - `balance_end_file`: saldo APÓS a última movimentação. É o `balance`
      da última linha por ordem cronológica.

    - `balance_end_omie`: reconstrução do saldo final segundo o Omie.
      `balance_start + sum(movimentos realizados no Omie dentro do período
      ESTRITO do arquivo)`. Usamos só `is_realized=True`; pending
      (Atrasado/Previsto) são títulos futuros, NÃO afetam saldo bancário
      até serem liquidados.

    - `balance_difference`: `balance_end_file - balance_end_omie`. None
      se qualquer um dos dois falta. Na aba 1 do Excel, `|diff| ≤ 0,01`
      é "Conferido"; caso contrário "Divergente".

Ordenação:
    UUIDs v4 não são insertion-ordered. Usamos `(transaction_date,
    created_at)` pra desempate dentro do mesmo dia. `created_at` é
    `server_default=NOW()` em microssegundos — entries inseridos no mesmo
    flush ficam em ordem de insertion, que é a ordem em que a IA extraiu,
    que é a ordem do extrato.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol


class FileEntryForBalance(Protocol):
    """Subset do `ReconciliationFileEntry` consumido aqui.

    Tipado como Protocol pra que testes possam usar SimpleNamespace/dataclass
    e o módulo não dependa de SQLAlchemy nem do DB.
    """

    transaction_date: date
    amount: Decimal
    balance: Decimal | None
    # `created_at` é `datetime` no model, mas tipar como `Any` simplifica:
    # só precisamos que seja comparável dentro do `sorted`.
    created_at: Any


class OmieMovementForBalance(Protocol):
    transaction_date: date
    amount: Decimal
    is_realized: bool


@dataclass(frozen=True, slots=True)
class SessionBalances:
    """Resultado do cálculo. Quatro campos sempre presentes, alguns None."""

    balance_start: Decimal | None
    balance_end_file: Decimal | None
    balance_end_omie: Decimal | None
    balance_difference: Decimal | None


def compute_balances(
    file_entries: Sequence[FileEntryForBalance],
    omie_movements: Sequence[OmieMovementForBalance],
    *,
    period_start: date,
    period_end: date,
    opening_balance: Decimal | None = None,
    closing_balance: Decimal | None = None,
) -> SessionBalances:
    """Calcula os 4 saldos.

    Args:
        file_entries: TODAS as entries da sessão (já persistidas).
        omie_movements: todos os movimentos Omie consolidados (realized +
            pending, já dedup). Filtramos `is_realized` aqui.
        period_start/period_end: período ESTRITO do arquivo (NÃO expandido
            por tolerância). Movimentos Omie fora dessa janela não contam
            pro saldo — eles existem no payload porque o `fetch_realized`
            expande o período por `tolerance_days`, mas só os do mês real
            afetam o saldo final.

    Returns:
        `SessionBalances`. Campos individuais podem ser None quando a base
        de dados falta — a UI degrada com "Indisponível" no Status.
    """
    if not file_entries:
        return SessionBalances(None, None, None, None)

    ordered = sorted(file_entries, key=lambda e: (e.transaction_date, e.created_at))
    first = ordered[0]
    last = ordered[-1]

    # BACK 02.3 — o saldo do PARSE (opening/closing do statement) é a fonte da
    # verdade quando informado: sempre presente (Decimal) e não depende do
    # `balance` por linha, que falta em faturas de cartão. Só caímos na
    # derivação por linha quando o parse não passou os saldos (sessões legadas /
    # chamadas de teste antigas).
    balance_start: Decimal | None
    if opening_balance is not None:
        balance_start = opening_balance
    elif first.balance is not None:
        balance_start = first.balance - first.amount
    else:
        balance_start = None

    balance_end_file = closing_balance if closing_balance is not None else last.balance

    balance_end_omie: Decimal | None = None
    if balance_start is not None:
        omie_in_period = sum(
            (
                mov.amount
                for mov in omie_movements
                if mov.is_realized and period_start <= mov.transaction_date <= period_end
            ),
            start=Decimal("0"),
        )
        balance_end_omie = balance_start + omie_in_period

    balance_difference: Decimal | None = None
    if balance_end_file is not None and balance_end_omie is not None:
        balance_difference = balance_end_file - balance_end_omie

    return SessionBalances(
        balance_start=balance_start,
        balance_end_file=balance_end_file,
        balance_end_omie=balance_end_omie,
        balance_difference=balance_difference,
    )
