"""Vigência por competência — funções PURAS, sem I/O (Sprint 12, BACK 12.4 — R4).

**A pergunta é sempre "qual decisão vale NAQUELA competência", nunca "qual é a mais
recente".** Refazer junho depois de uma vigência nova de setembro tem de aplicar a
regra de junho e produzir o mesmo resultado entregue na época.

Uma vigência começa em `effective_from` (dia 1 do mês) e vale até a véspera da
próxima vigência da MESMA chave `(source_type, category_code)` — dentro de um
cliente e de um destino, que são o recorte de quem chama.

Consumida pela escrita de decisão (12.4), pela leitura por destino (12.5) e pela
aplicação/materialização (12.6). Uma implementação só: duas versões de "qual vale"
fariam a prévia e a materialização discordarem.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol


class HasVigencia(Protocol):
    """O mínimo que uma decisão precisa expor para a resolução de vigência."""

    @property
    def source_type(self) -> str: ...

    @property
    def category_code(self) -> str: ...

    @property
    def effective_from(self) -> date: ...


#: Chave de uma decisão dentro de (cliente, destino).
type DecisionKey = tuple[str, str]


def decision_key(decision: HasVigencia) -> DecisionKey:
    return (decision.source_type, decision.category_code)


def resolve_vigente[D: HasVigencia](decisions: Iterable[D], competence: date) -> D | None:
    """A decisão VIGENTE na `competence` entre as vigências de UMA chave.

    A de maior `effective_from` que ainda não começou depois da competência.
    `None` = sem decisão naquela competência (inclusive quando a primeira vigência
    começa depois dela). Não depende da ordem de entrada.
    """
    vigente: D | None = None
    for decision in decisions:
        if decision.effective_from > competence:
            continue
        if vigente is None or decision.effective_from > vigente.effective_from:
            vigente = decision
    return vigente


def resolve_vigentes[D: HasVigencia](
    decisions: Iterable[D], competence: date
) -> dict[DecisionKey, D]:
    """`resolve_vigente` para TODAS as chaves de uma vez — uma passada, O(n)."""
    by_key: dict[DecisionKey, D] = {}
    for decision in decisions:
        if decision.effective_from > competence:
            continue
        key = decision_key(decision)
        current = by_key.get(key)
        if current is None or decision.effective_from > current.effective_from:
            by_key[key] = decision
    return by_key


def next_start_after(decisions: Iterable[HasVigencia], start: date) -> date | None:
    """Início da PRÓXIMA vigência da mesma chave depois de `start` — onde uma
    vigência nova em `start` deixaria de valer."""
    later = [d.effective_from for d in decisions if d.effective_from > start]
    return min(later) if later else None


def earliest_start(decisions: Iterable[HasVigencia]) -> date | None:
    """A competência mais antiga com alguma vigência — o piso da aplicação (R4)."""
    starts = [d.effective_from for d in decisions]
    return min(starts) if starts else None


def add_months(competence: date, months: int) -> date:
    """`competence` (dia 1) + `months` meses, ainda no dia 1."""
    index = competence.year * 12 + (competence.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def months_between(start: date, end: date) -> list[date]:
    """As competências de `start` a `end`, INCLUSIVAS (vazia se `start > end`)."""
    months: list[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def affected_competences(
    key_decisions: Sequence[HasVigencia], start: date, current: date
) -> list[date]:
    """Competências PASSADAS cujo resultado muda com uma vigência nova em `start`.

    De `start` até a véspera da próxima vigência da chave (que continua mandando
    dali em diante), limitado ao mês ANTERIOR ao corrente — o corrente e o futuro
    não são retroatividade. Vazia quando `start >= current`.
    """
    if start >= current:
        return []
    limit = add_months(current, -1)
    following = next_start_after(key_decisions, start)
    if following is not None:
        limit = min(limit, add_months(following, -1))
    return months_between(start, limit)
