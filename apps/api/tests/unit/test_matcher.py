"""Testes unitários do algoritmo de cruzamento (S10 / BACK 8.4).

Função pura — sem DB, sem mock. Cobre:
    - Match básico de mesmo dia + valor.
    - Tolerância de data (limite e fora do limite).
    - Tolerância de valor 0.01 BRL (limite e fora).
    - Sinal aritmético: débito não casa crédito.
    - Desempate: menor |days_diff| → menor |amount_diff| → date asc.
    - Greedy por linha: 2 file_entries disputando o mesmo Omie — só o
      primeiro vence.
    - Lista vazia (matcher tolera os dois lados vazios).
    - `unmatched_omie_indices` preserva ordem original.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.reconciliations.processing.matcher import (
    AMOUNT_TOLERANCE,
    FileEntryForMatch,
    MatchResult,
    OmieMovement,
    match,
)


def _pairs(result: MatchResult) -> list[tuple[str, int]]:
    """(file_id, omie_id) de cada match — ignora o days_diff (BACK 02.4).

    Os testes de lógica de matching validam o PAR; o days_diff assinado é
    coberto por `TestMatcherDaysDiff`.
    """
    return [(file_id, omie_id) for file_id, omie_id, _dd in result.matches]


def _file(id_: str, d: date, amount: str) -> FileEntryForMatch:
    return FileEntryForMatch(id=id_, transaction_date=d, amount=Decimal(amount))


def _omie(omie_id: int, d: date, amount: str, status: str = "Conciliado") -> OmieMovement:
    return OmieMovement(
        omie_id=omie_id,
        transaction_date=d,
        amount=Decimal(amount),
        status=status,
        is_realized=True,
    )


@pytest.mark.unit
class TestMatcherBasic:
    def test_empty_inputs_returns_empty_result(self) -> None:
        result = match([], [], tolerance_days=3)
        assert _pairs(result) == []
        assert result.unmatched_omie_indices == []

    def test_perfect_match_same_day_same_amount(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie, tolerance_days=3)

        assert _pairs(result) == [("F1", 1)]
        assert result.unmatched_omie_indices == []

    def test_no_candidates_leaves_omie_unmatched(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "999.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie, tolerance_days=3)

        assert _pairs(result) == []
        assert result.unmatched_omie_indices == [0]


@pytest.mark.unit
class TestMatcherAmountTolerance:
    def test_amount_within_tolerance_matches(self) -> None:
        # |100.00 - 100.01| = 0.01 → exatamente no limite.
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.01")]
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == [("F1", 1)]

    def test_amount_just_outside_tolerance_does_not_match(self) -> None:
        # 0.02 > AMOUNT_TOLERANCE (0.01) → não casa.
        assert Decimal("0.01") == AMOUNT_TOLERANCE
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.02")]
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == []
        assert result.unmatched_omie_indices == [0]

    def test_opposite_sign_does_not_match(self) -> None:
        # Débito do arquivo (-100) vs crédito do Omie (+100) — diferença de 200.
        files = [_file("F1", date(2026, 4, 15), "-100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == []


@pytest.mark.unit
class TestMatcherDateTolerance:
    def test_date_diff_within_tolerance_matches(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]  # +3 dias = limite
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == [("F1", 1)]

    def test_date_diff_outside_tolerance_does_not_match(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 19), "100.00")]  # +4 dias
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == []

    def test_zero_tolerance_only_same_day(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 16), "100.00"),
            _omie(2, date(2026, 4, 15), "100.00"),
        ]
        result = match(files, omie, tolerance_days=0)
        assert _pairs(result) == [("F1", 2)]
        assert result.unmatched_omie_indices == [0]


@pytest.mark.unit
class TestMatcherTieBreaking:
    def test_smaller_days_diff_wins(self) -> None:
        """Mesmo amount, datas diferentes → mais próximo vence."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 18), "100.00"),  # +3 dias
            _omie(2, date(2026, 4, 16), "100.00"),  # +1 dia ← vence
            _omie(3, date(2026, 4, 17), "100.00"),  # +2 dias
        ]
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == [("F1", 2)]

    def test_smaller_amount_diff_wins_when_days_tied(self) -> None:
        """Mesmo days_diff, valores diferentes → mais próximo vence."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 15), "100.01"),  # diff 0.01
            _omie(2, date(2026, 4, 15), "100.00"),  # diff 0.00 ← vence
        ]
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == [("F1", 2)]

    def test_earliest_date_wins_when_days_and_amount_tied(self) -> None:
        """Mesmo days_diff e amount_diff exato → menor date vence."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(2, date(2026, 4, 16), "100.00"),  # +1 dia
            _omie(1, date(2026, 4, 14), "100.00"),  # -1 dia ← mesmo days_diff, date menor
        ]
        result = match(files, omie, tolerance_days=3)
        # |days_diff| = 1 em ambos; amount_diff = 0 em ambos; date menor → 14/04
        assert _pairs(result) == [("F1", 1)]


@pytest.mark.unit
class TestMatcherGreedyConsumption:
    def test_two_file_entries_compete_for_same_omie_first_wins(self) -> None:
        """F1 e F2 com mesmo valor/data — só F1 (primeiro na lista) consome."""
        files = [
            _file("F1", date(2026, 4, 15), "100.00"),
            _file("F2", date(2026, 4, 15), "100.00"),
        ]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie, tolerance_days=3)

        assert _pairs(result) == [("F1", 1)]
        assert result.unmatched_omie_indices == []  # 1 consumido
        # F2 fica sem match, mas isso é codificado pela ausência em `matches` —
        # o caller infere `sem_omie` para todo file_entry não presente.

    def test_two_file_entries_pegam_omies_diferentes_quando_disponiveis(self) -> None:
        """Confirma que o set de consumidos não bloqueia matches legítimos."""
        files = [
            _file("F1", date(2026, 4, 15), "100.00"),
            _file("F2", date(2026, 4, 20), "200.00"),
        ]
        omie = [
            _omie(1, date(2026, 4, 15), "100.00"),
            _omie(2, date(2026, 4, 20), "200.00"),
        ]
        result = match(files, omie, tolerance_days=3)
        assert sorted(_pairs(result)) == [("F1", 1), ("F2", 2)]
        assert result.unmatched_omie_indices == []


@pytest.mark.unit
class TestMatcherUnmatchedOrder:
    def test_unmatched_omie_indices_preserve_input_order(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 1), "999.00"),  # idx 0 — sem match
            _omie(2, date(2026, 4, 15), "100.00"),  # idx 1 — vai casar
            _omie(3, date(2026, 4, 28), "777.00"),  # idx 2 — sem match
        ]
        result = match(files, omie, tolerance_days=3)
        assert _pairs(result) == [("F1", 2)]
        # Ordem dos não-consumidos preservada: 0, 2 (não [2, 0])
        assert result.unmatched_omie_indices == [0, 2]


@pytest.mark.unit
class TestMatcherDaysDiff:
    """BACK 02.4 — o matcher devolve o days_diff ASSINADO por match.

    `days_diff = transaction_date(arquivo) - transaction_date(omie)`: 0 = data
    exata; + = arquivo depois do Omie; - = arquivo antes.
    """

    def test_exact_date_is_zero(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 1, 0)]

    def test_file_after_omie_is_positive(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 14), "100.00")]  # arquivo 1 dia depois
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 1, 1)]

    def test_file_before_omie_is_negative(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]  # arquivo 3 dias antes
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 1, -3)]

    def test_days_diff_of_chosen_candidate_after_tie_break(self) -> None:
        # Tie-break escolhe o de menor |days_diff| (+1); o valor persistido
        # guarda o sinal do escolhido.
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 12), "100.00"),  # -3? não, arquivo depois → +3
            _omie(2, date(2026, 4, 14), "100.00"),  # +1 ← vence
        ]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 2, 1)]
