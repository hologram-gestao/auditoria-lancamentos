"""Testes do `compute_balances` (processing/balances.py).

Validação:
    1. Caso vazio → tudo None.
    2. Sem coluna balance no arquivo → balance_start/end_omie None, mas
       balance_end_file também None.
    3. Caminho feliz: saldos calculados, divergência zero quando Omie
       cobre todos os movimentos do arquivo.
    4. Movimento Omie fora do período estrito é IGNORADO (tolerance_days
       expande o fetch, mas só os de dentro contam pro saldo).
    5. Movimentos `is_realized=False` (Atrasado/Previsto) NÃO contam.
    6. Ordenação por (transaction_date, created_at) — entries fora de ordem
       são reordenadas corretamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.modules.reconciliations.processing.balances import (
    SessionBalances,
    compute_balances,
)


@dataclass
class _FE:
    transaction_date: date
    amount: Decimal
    balance: Decimal | None
    created_at: datetime


@dataclass
class _OM:
    transaction_date: date
    amount: Decimal
    is_realized: bool


def _dt(month: int, day: int, *, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute)


def test_empty_file_entries_returns_all_none() -> None:
    result = compute_balances(
        [],
        [],
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    assert result == SessionBalances(None, None, None, None)


def test_balance_start_derived_from_first_entry() -> None:
    """saldo_anterior = first.balance - first.amount.

    Cenário da Sicredi do Pedro: linha 02/03 saída de -3,45 com saldo
    336,19. Saldo ANTES dela = 339,64 (que é o SALDO ANTERIOR do extrato).
    """
    entries = [
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-3.45"),
            balance=Decimal("336.19"),
            created_at=_dt(3, 2, hour=10),
        ),
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-40.51"),
            balance=Decimal("295.68"),
            created_at=_dt(3, 2, hour=11),
        ),
    ]
    result = compute_balances(
        entries,
        [],
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    assert result.balance_start == Decimal("339.64")
    assert result.balance_end_file == Decimal("295.68")


def test_balance_end_omie_matches_when_all_movements_in_period() -> None:
    """Saldo Omie = balance_start + soma dos realizados no período estrito."""
    entries = [
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-100.00"),
            balance=Decimal("900.00"),
            created_at=_dt(3, 2),
        ),
        _FE(
            transaction_date=date(2026, 3, 15),
            amount=Decimal("-200.00"),
            balance=Decimal("700.00"),
            created_at=_dt(3, 15),
        ),
    ]
    omie = [
        _OM(transaction_date=date(2026, 3, 2), amount=Decimal("-100.00"), is_realized=True),
        _OM(transaction_date=date(2026, 3, 15), amount=Decimal("-200.00"), is_realized=True),
    ]
    result = compute_balances(
        entries,
        omie,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    # balance_start = 900 - (-100) = 1000
    assert result.balance_start == Decimal("1000.00")
    assert result.balance_end_file == Decimal("700.00")
    # balance_end_omie = 1000 + (-100) + (-200) = 700
    assert result.balance_end_omie == Decimal("700.00")
    assert result.balance_difference == Decimal("0.00")


def test_omie_movement_outside_strict_period_ignored() -> None:
    """Tolerance_days expande o fetch, mas só conta o que tá dentro do mês."""
    entries = [
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-100.00"),
            balance=Decimal("900.00"),
            created_at=_dt(3, 2),
        ),
    ]
    omie = [
        _OM(transaction_date=date(2026, 3, 2), amount=Decimal("-100.00"), is_realized=True),
        # Fora do período estrito (tolerance trouxe pro fetch, mas não conta)
        _OM(transaction_date=date(2026, 2, 28), amount=Decimal("-50.00"), is_realized=True),
        _OM(transaction_date=date(2026, 4, 2), amount=Decimal("-75.00"), is_realized=True),
    ]
    result = compute_balances(
        entries,
        omie,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    # balance_start = 900 - (-100) = 1000
    # balance_end_omie = 1000 + (-100) = 900  (50 e 75 ignorados)
    assert result.balance_end_omie == Decimal("900.00")
    assert result.balance_difference == Decimal("0.00")  # 900 - 900


def test_pending_movements_excluded_from_balance() -> None:
    """Atrasado/Previsto (is_realized=False) não afetam saldo bancário."""
    entries = [
        _FE(
            transaction_date=date(2026, 3, 5),
            amount=Decimal("-50.00"),
            balance=Decimal("450.00"),
            created_at=_dt(3, 5),
        ),
    ]
    omie = [
        _OM(transaction_date=date(2026, 3, 5), amount=Decimal("-50.00"), is_realized=True),
        # Pendente (Atrasado/Previsto) — NÃO conta
        _OM(transaction_date=date(2026, 3, 10), amount=Decimal("-9999.00"), is_realized=False),
    ]
    result = compute_balances(
        entries,
        omie,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    # balance_start = 450 - (-50) = 500
    # balance_end_omie = 500 + (-50) = 450  (pending ignorado)
    assert result.balance_end_omie == Decimal("450.00")
    assert result.balance_difference == Decimal("0.00")


def test_divergence_when_omie_missing_movement() -> None:
    """Se o arquivo tem mais movs do que o Omie, balance_difference != 0."""
    entries = [
        _FE(
            transaction_date=date(2026, 3, 1),
            amount=Decimal("-30.00"),
            balance=Decimal("970.00"),
            created_at=_dt(3, 1),
        ),
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-20.00"),
            balance=Decimal("950.00"),
            created_at=_dt(3, 2),
        ),
    ]
    # Omie só tem 1 das 2 movs — divergência esperada
    omie = [
        _OM(transaction_date=date(2026, 3, 1), amount=Decimal("-30.00"), is_realized=True),
    ]
    result = compute_balances(
        entries,
        omie,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    # balance_start = 970 - (-30) = 1000
    # balance_end_file = 950
    # balance_end_omie = 1000 + (-30) = 970
    # diff = 950 - 970 = -20
    assert result.balance_end_file == Decimal("950.00")
    assert result.balance_end_omie == Decimal("970.00")
    assert result.balance_difference == Decimal("-20.00")


def test_unordered_entries_sorted_correctly() -> None:
    """Lista fora de ordem é reordenada por (transaction_date, created_at)."""
    entries = [
        # Mar/20 entra ANTES do Mar/02 na lista, mas deve ser detectado como último
        _FE(
            transaction_date=date(2026, 3, 20),
            amount=Decimal("-300.00"),
            balance=Decimal("400.00"),
            created_at=_dt(3, 20),
        ),
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-100.00"),
            balance=Decimal("900.00"),
            created_at=_dt(3, 2),
        ),
    ]
    result = compute_balances(
        entries,
        [],
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    # first = Mar/02 (após sort) → balance_start = 900 - (-100) = 1000
    # last = Mar/20 → balance_end_file = 400
    assert result.balance_start == Decimal("1000.00")
    assert result.balance_end_file == Decimal("400.00")


def test_first_entry_without_balance_returns_partial_nones() -> None:
    """IA falhou em extrair saldo da primeira linha → balance_start None.

    end_file ainda funciona se a última linha tem balance.
    """
    entries = [
        _FE(
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-50.00"),
            balance=None,  # IA não extraiu
            created_at=_dt(3, 2),
        ),
        _FE(
            transaction_date=date(2026, 3, 20),
            amount=Decimal("-30.00"),
            balance=Decimal("400.00"),
            created_at=_dt(3, 20),
        ),
    ]
    result = compute_balances(
        entries,
        [],
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    assert result.balance_start is None
    assert result.balance_end_file == Decimal("400.00")
    assert result.balance_end_omie is None  # depende do balance_start
    assert result.balance_difference is None
