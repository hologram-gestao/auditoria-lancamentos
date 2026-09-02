"""Algoritmo de cruzamento determinístico (BACK 8.4).

CLAUDE.md §5 — regras invioláveis:
    1. Tolerância de valor: |a - b| ≤ 0.01 BRL (hardcoded, em Decimal).
    2. Range de data FIXO (DATE_DIVERGENCE_RANGE = 3 dias) — FASE 1 deixou de
       ser parametrizável por sessão (CLAUDE.md §5.2). O matcher casa
       candidatos até este range; o caller (job.py) classifica pelo
       `days_diff`: 0 → conciliado (data exata); 1-3 → conciliado_data_
       divergente (+ anomalia wrong_date); > 3 → sem match.
    3. Um OmieMovement só pode matchar UMA FileEntry — controle via set de
       índices consumidos.
    4. Desempate (CLAUDE.md §5.5): a proximidade de data manda primeiro, e manda
       GLOBALMENTE — o casamento acontece em passadas por |days_diff| crescente
       (0, 1, ..., DATE_DIVERGENCE_RANGE). Dentro de uma passada, desempata por
       menor |amount_diff| → `date asc`.
    5. Guloso dentro de cada passada (não global ótimo) — determinístico e
       auditável, sem heurística e sem IA (§5.9).

Função pura: sem I/O, sem ORM, sem logging — facilita testar exaustivamente
matrizes de casos. O caller (`job.py`) é quem aplica o resultado no DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.modules.reconciliations.processing.name_affinity import supplier_affinity

# Tolerância fixa em centavos. CLAUDE.md §5.1: NÃO é parametrizável.
AMOUNT_TOLERANCE: Decimal = Decimal("0.01")

# Range fixo de divergência de data, em dias. CLAUDE.md §5.2: NÃO é
# parametrizável (FASE 1 — antes era tolerância por sessão, default 3). O
# matcher casa candidatos até este range; quem classifica conciliado (exato)
# vs conciliado_data_divergente (1-3 dias) é o caller, via `days_diff`.
DATE_DIVERGENCE_RANGE: int = 3


@dataclass(frozen=True, slots=True)
class FileEntryForMatch:
    """View imutável de uma linha do arquivo, suficiente para o matcher.

    O id é opaco (UUID em string ou inteiro de teste): o matcher não
    interpreta — só o caller usa pra mapear de volta no DB.
    """

    id: str
    transaction_date: date
    amount: Decimal
    # Descrição JÁ DECIFRADA, usada só como desempate por afinidade de nome
    # (ver `name_affinity`). Default vazio: o matcher funciona sem ela, e os
    # testes que não exercitam fornecedor não precisam informá-la.
    # NUNCA logar nem persistir — dado identificável do cliente final (§4.5).
    description: str = ""


@dataclass(frozen=True, slots=True)
class OmieMovement:
    """Movimentação Omie unificada — extrato (realized) ou título (pending).

    `amount` JÁ vem com sinal aritmético aplicado (débito negativo, crédito
    positivo). Normalização acontece no `omie_fetch`, não aqui.

    `status` é o `cSituacao` do extrato (Conciliado/Atrasado/Previsto) ou o
    canônico DB derivado do filtro `status_titulo` (ATRASADO/AVENCER → Previsto)
    para títulos — usado adiante para classificar anomalias `missing_in_file`.
    O matcher não filtra por status.
    """

    omie_id: int
    transaction_date: date
    amount: Decimal
    status: str
    is_realized: bool
    # Razão social/nome fantasia do cliente-fornecedor, quando o Omie informa.
    # `None` para títulos a pagar/receber: `ListarContasPagar/Receber` devolve
    # apenas `codigo_cliente_fornecedor` (um ID), e resolver o nome exigiria uma
    # chamada extra a `ListarClientes` por lançamento. Só o extrato traz o nome.
    # NUNCA logar nem persistir (§4.5).
    supplier: str | None = None
    # `nCodLancRelac` do extrato — o id que o Omie usa para agrupar parcelas de
    # um mesmo lançamento. NÃO decide match (§5): serve só para a sonda de
    # pagamento dividido medir se o agrupamento vem de graça no response.
    related_launch_id: int | None = None
    # Código contábil da categoria (`cCodCategoria` no extrato,
    # `codigo_categoria` no título). NÃO decide match: persiste na divergência
    # (task 86e33bmkb) para a tela resolver a descrição via ListarCategorias —
    # única fonte de categoria para títulos, que ficam fora do extrato.
    category_code: str | None = None


@dataclass(frozen=True, slots=True)
class TieStats:
    """Quantas decisões empataram e quantas o fornecedor desempatou.

    Só números — nome de fornecedor e descrição jamais saem daqui (§3.3, §4.5).

    Attributes:
        ties: decisões em que mais de um lançamento Omie empatou no melhor
            `|amount_diff|` dentro da passada. É o denominador.
        broken_by_supplier: dessas, quantas o fornecedor resolveu escolhendo um
            candidato DIFERENTE do que a data sozinha escolheria. É o numerador
            — se ficar em zero depois de rodar em produção, o desempate por
            fornecedor não está pagando a complexidade que custa.
    """

    ties: int = 0
    broken_by_supplier: int = 0


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Saída do matcher — pares de índices + Omie sobrando + days_diff por par.

    `matches`: pares `(file_entry.id, OmieMovement.omie_id)`, ordenados por
    `(data da linha do arquivo, id)` — não pela passada em que o par foi
    fechado, e não pela ordem de leitura do arquivo. O caller aplica
    atualizando `omie_lancamento_id` e a `situation` — que agora depende do
    `days_diff` (ver `days_diff_by_file_id`): exato → `conciliado`, 1-3 dias →
    `conciliado_data_divergente`.

    `unmatched_omie_indices`: índices da lista original de movimentos Omie
    que NÃO foram consumidos. Usar índice (e não objeto) preserva a ordem
    original e evita confusão se houver IDs duplicados (não deveria, mas
    defesa em profundidade).

    `days_diff_by_file_id`: para cada `file_entry.id` em `matches`, o
    |dias de diferença| entre a data do arquivo e a do lançamento Omie casado
    (0 ≤ valor ≤ DATE_DIVERGENCE_RANGE). É o que permite o caller separar
    `conciliado` (== 0) de `conciliado_data_divergente` (1-3) sem recalcular.

    `tie_stats`: contadores puros (sem PII) para o caller logar. É a única forma
    de responder "com que frequência o desempate por fornecedor importa?" —
    o conjunto de candidatos de um cruzamento NÃO é persistido, então a pergunta
    não tem resposta retroativa no banco.
    """

    matches: list[tuple[str, int]]
    unmatched_omie_indices: list[int]
    days_diff_by_file_id: dict[str, int]
    tie_stats: TieStats


def _amount_within_tolerance(a: Decimal, b: Decimal) -> bool:
    """|a - b| ≤ 0.01 — hardcoded por CLAUDE.md §5.1."""
    return abs(a - b) <= AMOUNT_TOLERANCE


def match(
    file_entries: list[FileEntryForMatch],
    omie_movements: list[OmieMovement],
    tolerance_days: int = DATE_DIVERGENCE_RANGE,
) -> MatchResult:
    """Cruza arquivo x Omie aplicando as regras invioláveis.

    Algoritmo (passadas por proximidade de data):
        Para `dias` de 0 até `tolerance_days`, nesta ordem:
            Para cada `file_entry` ainda sem par, em ordem `(data, id)`:
                1. Filtra `omie_movements` ainda não consumidos onde
                   |amount_diff| ≤ 0.01 E |days_diff| == `dias`.
                2. Ordena candidatos por `(|amount_diff|, date asc)`.
                3. Pega o primeiro e marca como consumido.
        Quem sobrar dos dois lados fica sem par.

    Por que passadas, e não um laço só guloso por linha do arquivo: quando cada
    linha escolhia na sua vez, uma linha cuja contraparte real NÃO casa por
    valor (caso clássico: o pagamento está dividido em duas parcelas no Omie e
    o matcher é 1-para-1) levava o lançamento de OUTRA linha, desde que
    estivesse dentro dos 3 dias. A linha roubada ficava `sem_omie`, e a IA de
    qualificação acusava incoerência na primeira por comparar fornecedores
    diferentes — UM pareamento errado gerando DUAS anomalias falsas. Casando
    primeiro todos os pares de data exata, o par certo é fechado antes de
    qualquer candidato distante poder disputá-lo.

    Continua determinístico e auditável: não é matching ótimo global
    (Hungarian/etc) nem heurística — é guloso DENTRO de cada passada, e a
    ordem de todas as decisões é derivada dos dados, não da ordem de leitura
    do arquivo.

    Args:
        file_entries: linhas do arquivo. A ordem da lista NÃO afeta mais o
            resultado — as linhas são percorridas em `(transaction_date, id)`
            dentro de cada passada.
        omie_movements: lista combinada de movimentações Omie (extrato +
            títulos). Ordem dentro da lista NÃO afeta o resultado — o desempate
            é determinístico por (amount_diff, date).
        tolerance_days: número de passadas além da exata (CLAUDE.md §5.2).
            Default é `DATE_DIVERGENCE_RANGE` (3) — fixo no produto desde a
            FASE 1. O parâmetro existe só para testar o algoritmo com outros
            ranges; o sistema sempre usa o default. Aceita qualquer inteiro ≥ 0.

    Returns:
        `MatchResult` com pares (file_id, omie_id), índices Omie sobrando e o
        `days_diff_by_file_id` (para o caller classificar conciliado x
        conciliado_data_divergente).
    """
    used_omie_indices: set[int] = set()
    matched_file_ids: set[str] = set()
    matches: list[tuple[str, int]] = []
    days_diff_by_file_id: dict[str, int] = {}
    ties = 0
    broken_by_supplier = 0

    # Ordem de decisão das linhas dentro de cada passada. Derivada dos dados
    # (data, depois id) em vez da ordem de leitura: o resultado deixa de
    # depender de o parser ter entregue o extrato cronológico ou não.
    ordered_entries = sorted(file_entries, key=lambda fe: (fe.transaction_date, fe.id))

    for pass_days in range(tolerance_days + 1):
        for file_entry in ordered_entries:
            if file_entry.id in matched_file_ids:
                continue

            candidate_indices: list[int] = []
            for idx, omie in enumerate(omie_movements):
                if idx in used_omie_indices:
                    continue
                if abs((file_entry.transaction_date - omie.transaction_date).days) != pass_days:
                    continue
                if not _amount_within_tolerance(file_entry.amount, omie.amount):
                    continue
                candidate_indices.append(idx)

            if not candidate_indices:
                continue

            # Desempate DENTRO da passada (CLAUDE.md §5.5): o |days_diff| já é
            # o mesmo para todos os candidatos aqui, então sobram
            #   1) menor |amount_diff|
            #   2) MAIOR afinidade de fornecedor com a descrição do extrato
            #   3) primeiro por date asc
            # A afinidade entra DEPOIS do valor porque valor é fato e nome é
            # indício. E entra só como ordenação — nenhum candidato é removido
            # por nome que não bate (ver `name_affinity`).
            def _sort_key(
                idx: int, _file_entry: FileEntryForMatch = file_entry
            ) -> tuple[Decimal, int, date]:
                omie = omie_movements[idx]
                affinity = supplier_affinity(omie.supplier, _file_entry.description)
                return (
                    abs(_file_entry.amount - omie.amount),
                    -affinity,  # negativo: mais tokens em comum ordena antes
                    omie.transaction_date,
                )

            def _sort_key_sem_fornecedor(
                idx: int, _file_entry: FileEntryForMatch = file_entry
            ) -> tuple[Decimal, date]:
                """O critério anterior — serve só para medir se o nome mudou algo."""
                omie = omie_movements[idx]
                return (abs(_file_entry.amount - omie.amount), omie.transaction_date)

            chosen = min(candidate_indices, key=_sort_key)

            # Instrumentação: um "empate" é mais de um candidato disputando o
            # melhor |amount_diff|. Sem isto não há como saber se o desempate
            # por fornecedor importa — o conjunto de candidatos não é persistido.
            best_amount_diff = min(
                abs(file_entry.amount - omie_movements[i].amount) for i in candidate_indices
            )
            tied = [
                i
                for i in candidate_indices
                if abs(file_entry.amount - omie_movements[i].amount) == best_amount_diff
            ]
            if len(tied) > 1:
                ties += 1
                if chosen != min(candidate_indices, key=_sort_key_sem_fornecedor):
                    broken_by_supplier += 1

            used_omie_indices.add(chosen)
            matched_file_ids.add(file_entry.id)
            matches.append((file_entry.id, omie_movements[chosen].omie_id))
            days_diff_by_file_id[file_entry.id] = pass_days

    # `matches` sai na ordem das linhas do arquivo, não na ordem das passadas —
    # o consumidor não deve enxergar o detalhe do algoritmo.
    order_by_file_id = {fe.id: pos for pos, fe in enumerate(ordered_entries)}
    matches.sort(key=lambda pair: order_by_file_id[pair[0]])

    unmatched_omie_indices = [
        idx for idx in range(len(omie_movements)) if idx not in used_omie_indices
    ]
    return MatchResult(
        matches=matches,
        unmatched_omie_indices=unmatched_omie_indices,
        days_diff_by_file_id=days_diff_by_file_id,
        tie_stats=TieStats(ties=ties, broken_by_supplier=broken_by_supplier),
    )
