"""Algoritmo de cruzamento determinístico (BACK 8.4).

CLAUDE.md §5 — regras invioláveis:
    1. Tolerância de valor: |a - b| ≤ 0.01 BRL (hardcoded, em Decimal).
    2. Tolerância de data: parametrizável por sessão (default 3 dias).
    3. Um OmieMovement só pode matchar UMA FileEntry — controle via set de
       índices consumidos.
    4. Desempate (CLAUDE.md §5.5): menor |days_diff| → menor |amount_diff| →
       primeiro por `date asc`.
    5. Guloso por linha de arquivo (não global ótimo) — alinha com a
       implementação descrita no PLANO §S10 etapa 4.

Função pura: sem I/O, sem ORM, sem logging — facilita testar exaustivamente
matrizes de casos. O caller (`job.py`) é quem aplica o resultado no DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Tolerância fixa em centavos. CLAUDE.md §5.1: NÃO é parametrizável.
AMOUNT_TOLERANCE: Decimal = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class FileEntryForMatch:
    """View imutável de uma linha do arquivo, suficiente para o matcher.

    O id é opaco (UUID em string ou inteiro de teste): o matcher não
    interpreta — só o caller usa pra mapear de volta no DB.
    """

    id: str
    transaction_date: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class OmieMovement:
    """Movimentação Omie unificada — extrato (realized) ou título (pending).

    `amount` JÁ vem com sinal aritmético aplicado (débito negativo, crédito
    positivo). Normalização acontece no `omie_fetch`, não aqui.

    `status` é o `cStatus` do extrato (Conciliado/Atrasado/Previsto) ou o
    `status_titulo` (ATRASADO/PREVISTO) — usado adiante para classificar
    anomalias `missing_in_file`. O matcher não filtra por status.
    """

    omie_id: int
    transaction_date: date
    amount: Decimal
    status: str
    is_realized: bool


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Saída do matcher — apenas pares de índices + lista de Omie sobrando.

    `matches`: pares `(file_entry.id, OmieMovement.omie_id)`. O caller
    aplica isso atualizando `situation='conciliado'` e `omie_lancamento_id`.

    `unmatched_omie_indices`: índices da lista original de movimentos Omie
    que NÃO foram consumidos. Usar índice (e não objeto) preserva a ordem
    original e evita confusão se houver IDs duplicados (não deveria, mas
    defesa em profundidade).
    """

    matches: list[tuple[str, int]]
    unmatched_omie_indices: list[int]


def _amount_within_tolerance(a: Decimal, b: Decimal) -> bool:
    """|a - b| ≤ 0.01 — hardcoded por CLAUDE.md §5.1."""
    return abs(a - b) <= AMOUNT_TOLERANCE


def match(
    file_entries: list[FileEntryForMatch],
    omie_movements: list[OmieMovement],
    tolerance_days: int,
) -> MatchResult:
    """Cruza arquivo x Omie aplicando as regras invioláveis.

    Algoritmo (guloso por linha do arquivo):
        Para cada `file_entry` na ordem recebida:
            1. Filtra `omie_movements` ainda não consumidos onde
               |amount_diff| ≤ 0.01 E |days_diff| ≤ tolerance_days.
            2. Ordena candidatos por `(|days_diff|, |amount_diff|, date asc)`.
            3. Pega o primeiro, marca como consumido (set de índices).
            4. Se não há candidato → file_entry permanece sem match.

    Não é matching ótimo global (Hungarian/etc) por escolha explícita do plano:
    matching guloso é determinístico, fácil de auditar, e em prática casa com
    o que o analista esperaria (a primeira linha do arquivo "fica com" o seu
    candidato mais próximo).

    Args:
        file_entries: linhas do arquivo, NA ORDEM em que aparecem (ordem
            afeta o resultado por ser guloso). O caller normalmente passa em
            ordem cronológica do extrato.
        omie_movements: lista combinada de movimentações Omie (extrato +
            títulos). Ordem dentro da lista NÃO afeta o resultado — o desempate
            é determinístico por (days_diff, amount_diff, date).
        tolerance_days: tolerância parametrizável (CLAUDE.md §5.2). Default
            do produto é 3, mas o matcher aceita qualquer inteiro ≥ 0.

    Returns:
        `MatchResult` com pares (file_id, omie_id) e índices Omie sobrando.
    """
    used_omie_indices: set[int] = set()
    matches: list[tuple[str, int]] = []

    for file_entry in file_entries:
        candidate_indices: list[int] = []
        for idx, omie in enumerate(omie_movements):
            if idx in used_omie_indices:
                continue
            if not _amount_within_tolerance(file_entry.amount, omie.amount):
                continue
            days_diff = abs((file_entry.transaction_date - omie.transaction_date).days)
            if days_diff > tolerance_days:
                continue
            candidate_indices.append(idx)

        if not candidate_indices:
            continue

        # Desempate determinístico CLAUDE.md §5.5:
        #   1) menor |days_diff|
        #   2) menor |amount_diff|
        #   3) primeiro por date asc
        # `sorted` é estável; `key` retorna tupla ordenável diretamente.
        def _sort_key(
            idx: int, _file_entry: FileEntryForMatch = file_entry
        ) -> tuple[int, Decimal, date]:
            omie = omie_movements[idx]
            days_diff = abs((_file_entry.transaction_date - omie.transaction_date).days)
            amount_diff = abs(_file_entry.amount - omie.amount)
            return (days_diff, amount_diff, omie.transaction_date)

        candidate_indices.sort(key=_sort_key)
        chosen = candidate_indices[0]
        used_omie_indices.add(chosen)
        matches.append((file_entry.id, omie_movements[chosen].omie_id))

    unmatched_omie_indices = [
        idx for idx in range(len(omie_movements)) if idx not in used_omie_indices
    ]
    return MatchResult(matches=matches, unmatched_omie_indices=unmatched_omie_indices)
