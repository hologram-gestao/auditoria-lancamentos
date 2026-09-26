"""Aplicação do de-para — função PURA e determinística (Sprint 12, BACK 12.6 — R3/R5).

`apply_mapping(movimentos, decisões, competência)` → resultado. Sem IA, sem I/O, sem
relógio e SEM dependência da ordem de entrada: a saída é ordenada por uma chave total
e todo agregado é soma comutativa em `Decimal`. Mesma base + mesmas vigências =
mesma saída, byte a byte — é o que torna a prévia confiável e o teste barato.

**Entrada (R3):** os movimentos PRESENTES da competência, de TODAS as contas
(`ausente_na_origem` fica fora — a origem já não os tem). Nunca as divergências de
conciliação, nunca leitura ao vivo da origem.

**Quatro situações por movimento (R5):**
  - `alvo` — a vigente na competência aponta um alvo;
  - `nao_mapear` — a vigente diz explicitamente "não levar a este destino";
  - `sem_decisao` — tem categoria, e nenhuma vigência vale nesta competência;
  - `sem_categoria` — a origem não mandou categoria: buraco de INGESTÃO, não de
    decisão — fica FORA do denominador da cobertura (R3).

**Cobertura:** numerador Σ|valor| (`alvo` + `nao_mapear`); denominador Σ|valor| com
categoria (`alvo` + `nao_mapear` + `sem_decisao`). Denominador zero → percentual
`None` (nunca divisão por zero, nunca um "0%" que pareça resultado).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol

from app.db.models import DecisionType, MaterializedSituation, MovementStatus
from app.modules.client_mapping.vigencia import DecisionKey, resolve_vigentes

ZERO = Decimal("0.00")
_PCT_QUANTUM = Decimal("0.01")


class MovementLike(Protocol):
    @property
    def source_type(self) -> str: ...
    @property
    def source_movement_id(self) -> str: ...
    @property
    def source_account_id(self) -> str | None: ...
    @property
    def movement_date(self) -> date: ...
    @property
    def amount(self) -> Decimal: ...
    @property
    def category_code(self) -> str | None: ...
    @property
    def status(self) -> str: ...


class DecisionLike(Protocol):
    @property
    def source_type(self) -> str: ...
    @property
    def category_code(self) -> str: ...
    @property
    def effective_from(self) -> date: ...
    @property
    def decision_type(self) -> str: ...


@dataclass(frozen=True, slots=True)
class AppliedItem:
    """Um movimento com a sua situação — o que a materialização guarda em snapshot."""

    source_type: str
    source_movement_id: str
    source_account_id: str | None
    movement_date: date
    amount: Decimal
    category_code: str | None
    situation: MaterializedSituation
    target_code: str | None
    decision_effective_from: date | None


@dataclass(frozen=True, slots=True)
class SituationTotal:
    #: Σ|valor| — o valor ABSOLUTO: entrada e saída contam para a cobertura igual.
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class UndecidedCategory:
    source_type: str
    category_code: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class UsedDecision:
    """Uma vigência que decidiu ao menos um movimento — registrada na materialização."""

    source_type: str
    category_code: str
    decision_type: str
    target_code: str | None
    effective_from: date


@dataclass(frozen=True, slots=True)
class ApplyResult:
    competence: date
    items: tuple[AppliedItem, ...]
    totals: dict[MaterializedSituation, SituationTotal]
    #: Ordenadas por |valor| DECRESCENTE (desempate por chave): a maior pendência
    #: primeiro, que é por onde a pessoa começa.
    undecided_categories: tuple[UndecidedCategory, ...]
    used_decisions: tuple[UsedDecision, ...]

    @property
    def numerator(self) -> Decimal:
        return (
            self.totals[MaterializedSituation.ALVO].amount
            + self.totals[MaterializedSituation.NAO_MAPEAR].amount
        )

    @property
    def denominator(self) -> Decimal:
        return self.numerator + self.totals[MaterializedSituation.SEM_DECISAO].amount

    @property
    def coverage_pct(self) -> Decimal | None:
        return _pct(self.numerator, self.denominator)

    @property
    def nao_mapear_pct(self) -> Decimal | None:
        """A CONTRA-MÉTRICA: sem ela, marcar tudo `nao_mapear` daria 100% de cobertura
        com o demonstrativo vazio."""
        return _pct(self.totals[MaterializedSituation.NAO_MAPEAR].amount, self.denominator)

    def fingerprint(self, *, destination_id: str) -> str:
        """Hash da ENTRADA e da SAÍDA: a prévia confirmada tem de ser ESTA.

        SHA-256 sobre JSON canônico (chaves ordenadas, sem espaço) da competência, do
        destino, de cada item (movimento + situação + alvo + vigência) e das vigências
        usadas. Qualquer mudança na base (valor, categoria, movimento novo ou ausente)
        ou nas decisões muda o hash — e a materialização recusa a prévia antiga.
        """
        payload = {
            "competence": self.competence.isoformat(),
            "destination": destination_id,
            "items": [
                [
                    i.source_type,
                    i.source_movement_id,
                    i.source_account_id,
                    i.movement_date.isoformat(),
                    # Escala fixa: `Decimal("100")` e `Decimal("100.00")` são o mesmo
                    # dinheiro e não podem dar hashes diferentes.
                    str(i.amount.quantize(ZERO)),
                    i.category_code,
                    i.situation.value,
                    i.target_code,
                    i.decision_effective_from.isoformat() if i.decision_effective_from else None,
                ]
                for i in self.items
            ],
            "decisions": [
                [
                    d.source_type,
                    d.category_code,
                    d.decision_type,
                    d.target_code,
                    d.effective_from.isoformat(),
                ]
                for d in self.used_decisions
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole == 0:
        return None
    return (part * 100 / whole).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_EVEN)


def apply_mapping[D: DecisionLike](
    movements: Iterable[MovementLike],
    decisions: Iterable[D],
    competence: date,
    *,
    target_code_of: dict[DecisionKey, str | None],
) -> ApplyResult:
    """Aplica as vigências da `competence` aos movimentos PRESENTES. PURA.

    `decisions` é o conjunto COMPLETO de vigências do cliente no destino; a vigente
    de cada chave NESTA competência sai de `resolve_vigentes` (12.4) — nunca a mais
    recente. `target_code_of` traz o CÓDIGO do alvo de cada chave vigente (o
    chamador resolve os ids do banco; aqui não há I/O).
    """
    vigentes = resolve_vigentes(decisions, competence)

    items: list[AppliedItem] = []
    totals_amount: dict[MaterializedSituation, Decimal] = dict.fromkeys(MaterializedSituation, ZERO)
    totals_count: dict[MaterializedSituation, int] = dict.fromkeys(MaterializedSituation, 0)
    undecided: dict[DecisionKey, tuple[Decimal, int]] = {}
    used: dict[DecisionKey, UsedDecision] = {}

    for mv in movements:
        if mv.status != MovementStatus.PRESENTE.value:
            continue
        target_code: str | None = None
        effective: date | None = None
        if mv.category_code is None:
            situation = MaterializedSituation.SEM_CATEGORIA
        else:
            key = (mv.source_type, mv.category_code)
            vigente = vigentes.get(key)
            if vigente is None:
                situation = MaterializedSituation.SEM_DECISAO
                amount, count = undecided.get(key, (ZERO, 0))
                undecided[key] = (amount + abs(mv.amount), count + 1)
            else:
                effective = vigente.effective_from
                if vigente.decision_type == DecisionType.NAO_MAPEAR.value:
                    situation = MaterializedSituation.NAO_MAPEAR
                else:
                    situation = MaterializedSituation.ALVO
                    target_code = target_code_of.get(key)
                used[key] = UsedDecision(
                    source_type=key[0],
                    category_code=key[1],
                    decision_type=vigente.decision_type,
                    target_code=target_code,
                    effective_from=vigente.effective_from,
                )
        totals_amount[situation] += abs(mv.amount)
        totals_count[situation] += 1
        items.append(
            AppliedItem(
                source_type=mv.source_type,
                source_movement_id=mv.source_movement_id,
                source_account_id=mv.source_account_id,
                movement_date=mv.movement_date,
                amount=mv.amount,
                category_code=mv.category_code,
                situation=situation,
                target_code=target_code,
                decision_effective_from=effective,
            )
        )

    items.sort(key=lambda i: (i.source_type, i.source_movement_id))
    return ApplyResult(
        competence=competence,
        items=tuple(items),
        totals={
            s: SituationTotal(amount=totals_amount[s], count=totals_count[s])
            for s in MaterializedSituation
        },
        undecided_categories=tuple(
            sorted(
                (
                    UndecidedCategory(source_type=k[0], category_code=k[1], amount=v[0], count=v[1])
                    for k, v in undecided.items()
                ),
                key=lambda u: (-u.amount, u.source_type, u.category_code),
            )
        ),
        used_decisions=tuple(sorted(used.values(), key=lambda d: (d.source_type, d.category_code))),
    )
