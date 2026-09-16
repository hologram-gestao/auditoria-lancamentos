"""Mede o efeito de fechar os pares por evidência dentro da passada (86e39p1wv).

Compara o `match()` atual (pares da passada fechados em ordem de evidência) com o
algoritmo anterior (cada linha decidindo na sua vez, em ordem `(data, id)`),
reimplementado aqui como referência, sobre cenários sintéticos com verdade
conhecida. O conjunto de candidatos de um cruzamento real não é persistido, então
não há replay a partir do banco — este script é a medição possível, e fica
versionado para a próxima mudança do motor ter linha de base.

Critérios (skill `matcher`, passo 3): maximalidade nos dois; pares de data exata
nunca caem; contagem de pares; e o que interessa ao usuário — quantos pares casam
a PESSOA errada. "Errado" é fornecedor diferente do da verdade sintética, não id
diferente: duas linhas da mesma pessoa com o mesmo valor são indistinguíveis por
construção e trocar o id entre elas não é erro.

Uso:
    cd apps/api && uv run --extra dev python -m scripts.measure_matcher_evidence_order
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.modules.reconciliations.processing.matcher import (
    AMOUNT_TOLERANCE,
    DATE_DIVERGENCE_RANGE,
    FileEntryForMatch,
    MatchResult,
    OmieMovement,
    TieStats,
    match,
)
from app.modules.reconciliations.processing.name_affinity import supplier_affinity

SEED = 20260915
SCENARIOS = 20_000

# Nomes FICTÍCIOS. Cobrem os padrões que importam para a afinidade: nome completo,
# sigla curta (nenhum token sobrevive ao mínimo de 3 letras), sobrenome repetido
# entre pessoas diferentes ("Silva") e razão social com tokens genéricos.
_NAMES = [
    "Marina Duarte Rocha",
    "Ricardo Teixeira Alves",
    "Helena Prado Silva",
    "Otavio Nunes da Silva",
    "Beatriz Camargo Lins",
    "Padaria Sol Nascente Ltda",
    "TK Solucoes Digitais",
    "Joao Pedro Almeida",
    "Comercio de Alimentos Ltda",
    "Comercio de Pecas Ltda",
    "Ana Beatriz Costa",
    "Transportadora Rapida ME",
]
_AMOUNTS = [Decimal(a) for a in ("-247.80", "-2800.00", "-100.00", "-1500.00", "-89.90", "-320.00")]
_BASE = date(2026, 8, 1)


def match_line_first(
    file_entries: list[FileEntryForMatch],
    omie_movements: list[OmieMovement],
    tolerance_days: int = DATE_DIVERGENCE_RANGE,
) -> MatchResult:
    """O algoritmo anterior (até 16/09/2026): cada linha decide na sua vez.

    Mesmas passadas por data, mesmo critério por linha `(|amount_diff|,
    -afinidade, date asc, posição na lista)`. A diferença para o `match()` atual
    é só a ordem das decisões DENTRO da passada: aqui é a ordem `(data, id)` das
    linhas; lá é a evidência do par.
    """
    used: set[int] = set()
    matched: set[str] = set()
    matches: list[tuple[str, int]] = []
    days_diff: dict[str, int] = {}
    ordered = sorted(file_entries, key=lambda fe: (fe.transaction_date, fe.id))
    for pass_days in range(tolerance_days + 1):
        for fe in ordered:
            if fe.id in matched:
                continue
            candidates = [
                idx
                for idx, om in enumerate(omie_movements)
                if idx not in used
                and abs((fe.transaction_date - om.transaction_date).days) == pass_days
                and abs(fe.amount - om.amount) <= AMOUNT_TOLERANCE
            ]
            if not candidates:
                continue
            chosen = min(
                candidates,
                key=lambda idx: (
                    abs(fe.amount - omie_movements[idx].amount),
                    -supplier_affinity(omie_movements[idx].supplier, fe.description),
                    omie_movements[idx].transaction_date,
                    idx,
                ),
            )
            used.add(chosen)
            matched.add(fe.id)
            matches.append((fe.id, omie_movements[chosen].omie_id))
            days_diff[fe.id] = pass_days
    order = {fe.id: pos for pos, fe in enumerate(ordered)}
    matches.sort(key=lambda pair: order[pair[0]])
    return MatchResult(
        matches=matches,
        unmatched_omie_indices=[i for i in range(len(omie_movements)) if i not in used],
        days_diff_by_file_id=days_diff,
        tie_stats=TieStats(),
    )


def _description(name: str, rng: random.Random) -> str:
    """Descrição de extrato realista: nome inteiro, com CNPJ, sigla, genérica ou parcial."""
    style = rng.random()
    if style < 0.45:
        return f"PIX ENVIADO {name.upper()}"
    if style < 0.65:
        cnpj = f"{rng.randint(10, 99)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}"
        return f"PIX ENVIADO {cnpj} {name.upper()}"
    if style < 0.80:
        # Sigla de 2 letras por palavra: nenhum token sobrevive ao mínimo de 3.
        return "PIX ENVIADO " + " ".join(word[:2] for word in name.upper().split()[:2])
    if style < 0.90:
        return "PAGAMENTO"
    return f"COMPRA CARTAO {name.upper().split()[0]}"


@dataclass(frozen=True, slots=True)
class Scenario:
    files: list[FileEntryForMatch]
    omie: list[OmieMovement]
    # file_entry.id -> nome do fornecedor legítimo (só para linhas com contraparte).
    truth: dict[str, str]


def _scenario(rng: random.Random) -> Scenario:
    files: list[FileEntryForMatch] = []
    omie: list[OmieMovement] = []
    truth: dict[str, str] = {}
    for _ in range(rng.randint(2, 8)):
        name = rng.choice(_NAMES)
        amount = rng.choice(_AMOUNTS)
        day = _BASE + timedelta(days=rng.randint(0, 20))
        file_id = str(uuid.UUID(int=rng.getrandbits(128)))
        files.append(FileEntryForMatch(file_id, day, amount, _description(name, rng)))
        roll = rng.random()
        if roll < 0.80:  # contraparte legítima, às vezes com data divergente
            shift = rng.choice([0, 0, 0, 0, 1, -1, 2, -2, 3, -3])
            omie.append(_movement(len(omie) + 1, day + timedelta(days=shift), amount, name))
            truth[file_id] = name
        elif roll < 0.90:  # pagamento dividido: duas parcelas que não casam 1-para-1
            half = (amount / 2).quantize(Decimal("0.01"))
            omie.append(_movement(len(omie) + 1, day, half, name))
            omie.append(_movement(len(omie) + 1, day, amount - half, name))
        # senão: linha sem contraparte nenhuma
    for _ in range(rng.randint(0, 3)):  # ruído: lançamentos de outras pessoas
        omie.append(
            _movement(
                len(omie) + 1,
                _BASE + timedelta(days=rng.randint(0, 20)),
                rng.choice(_AMOUNTS),
                rng.choice(_NAMES),
            )
        )
    rng.shuffle(omie)
    return Scenario(files=files, omie=omie, truth=truth)


def _movement(omie_id: int, day: date, amount: Decimal, supplier: str) -> OmieMovement:
    return OmieMovement(
        omie_id=omie_id,
        transaction_date=day,
        amount=amount,
        status="Conciliado",
        is_realized=True,
        supplier=supplier,
    )


def _is_maximal(scenario: Scenario, result: MatchResult) -> bool:
    """Nenhuma linha sem par com lançamento sem par a <= 3 dias e <= 0,01."""
    matched = {file_id for file_id, _ in result.matches}
    used = set(range(len(scenario.omie))) - set(result.unmatched_omie_indices)
    for fe in scenario.files:
        if fe.id in matched:
            continue
        for idx, om in enumerate(scenario.omie):
            if idx in used:
                continue
            if (
                abs((fe.transaction_date - om.transaction_date).days) <= DATE_DIVERGENCE_RANGE
                and abs(fe.amount - om.amount) <= AMOUNT_TOLERANCE
            ):
                return False
    return True


@dataclass(slots=True)
class Tally:
    pairs: int = 0
    exact_pairs: int = 0
    wrong_person: int = 0
    non_maximal: int = 0
    ties: int = 0
    broken_by_supplier: int = 0
    steals_prevented: int = 0

    def add(self, scenario: Scenario, result: MatchResult) -> int:
        by_id = {om.omie_id: om for om in scenario.omie}
        wrong = sum(
            1
            for file_id, omie_id in result.matches
            if file_id in scenario.truth and by_id[omie_id].supplier != scenario.truth[file_id]
        )
        self.pairs += len(result.matches)
        self.exact_pairs += sum(1 for d in result.days_diff_by_file_id.values() if d == 0)
        self.wrong_person += wrong
        self.non_maximal += 0 if _is_maximal(scenario, result) else 1
        self.ties += result.tie_stats.ties
        self.broken_by_supplier += result.tie_stats.broken_by_supplier
        self.steals_prevented += result.tie_stats.steals_prevented_by_supplier
        return wrong


def main() -> None:
    rng = random.Random(SEED)  # noqa: S311 - cenário sintético reprodutível, não é cripto
    before, after = Tally(), Tally()
    differ = fixed = introduced = fewer = more = exact_fewer = 0
    for _ in range(SCENARIOS):
        scenario = _scenario(rng)
        old = match_line_first(scenario.files, scenario.omie)
        new = match(scenario.files, scenario.omie)
        wrong_old = before.add(scenario, old)
        wrong_new = after.add(scenario, new)
        differ += dict(old.matches) != dict(new.matches)
        fixed += wrong_new < wrong_old
        introduced += wrong_new > wrong_old
        fewer += len(new.matches) < len(old.matches)
        more += len(new.matches) > len(old.matches)
        exact_old = sum(1 for d in old.days_diff_by_file_id.values() if d == 0)
        exact_new = sum(1 for d in new.days_diff_by_file_id.values() if d == 0)
        exact_fewer += exact_new < exact_old

    print(f"cenários: {SCENARIOS} (seed {SEED}) | resultado diferente em {differ}")
    print(f"{'medida':<34}{'linha a linha':>16}{'por evidência':>16}")
    # A referência não calcula os contadores de `TieStats` — mostra "-" para não
    # parecer que o algoritmo antigo tinha zero empates.
    rows: list[tuple[str, int | str, int]] = [
        ("pares", before.pairs, after.pairs),
        ("pares de data exata", before.exact_pairs, after.exact_pairs),
        ("pares com a PESSOA errada", before.wrong_person, after.wrong_person),
        ("cenários não-maximais", before.non_maximal, after.non_maximal),
        ("ties (contador)", "-", after.ties),
        ("broken_by_supplier (contador)", "-", after.broken_by_supplier),
        ("steals_prevented (contador)", "-", after.steals_prevented),
    ]
    for label, old_value, new_value in rows:
        print(f"{label:<34}{old_value!s:>16}{new_value:>16}")
    print(f"cenários com troca corrigida: {fixed} | introduzida: {introduced}")
    print(f"cenários com menos pares: {fewer} | com mais pares: {more}")
    print(f"cenários com MENOS pares de data exata: {exact_fewer} (tem de ser 0)")


if __name__ == "__main__":
    main()
