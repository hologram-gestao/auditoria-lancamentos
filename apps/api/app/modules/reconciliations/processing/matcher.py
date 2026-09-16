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
       (0, 1, ..., DATE_DIVERGENCE_RANGE). Dentro de uma passada, os PARES
       fecham em ordem de evidência: menor |amount_diff| → maior afinidade de
       fornecedor → `date asc` → ordem `(data, id)` da linha. Quem decide
       primeiro é o par mais forte, não a primeira linha.
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
    # ID do cliente/fornecedor no cadastro Omie (`nCodCliente` no extrato,
    # `codigo_cliente_fornecedor` no título). NÃO decide match: persiste na
    # divergência para a tela resolver o NOME via ConsultarCliente cacheado —
    # única fonte de fornecedor para títulos (§5.5).
    supplier_code: int | None = None


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
        steals_prevented_by_supplier: pares fechados por evidência que uma linha
            ANTERIOR na ordem `(data, id)`, ainda sem par e decidindo na vez
            dela, teria levado (report da Bruna, 15/09/2026: a linha sem sinal
            de nome vinha antes e levava o lançamento de quem tinha). É o sinal
            de produção da ordem por evidência dentro da passada — se ficar em
            zero, a mudança não está mudando resultado nenhum.
    """

    ties: int = 0
    broken_by_supplier: int = 0
    steals_prevented_by_supplier: int = 0


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


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Um lançamento Omie elegível para uma linha, dentro de uma passada.

    Guarda a evidência do par já calculada — `supplier_affinity` é a única conta
    não-trivial do matcher e não precisa ser refeita a cada ordenação.
    """

    amount_diff: Decimal
    affinity: int
    omie_date: date
    omie_index: int

    def evidence_key(self) -> tuple[Decimal, int, date, int]:
        """Critério de uma linha entre os candidatos DELA (CLAUDE.md §5.5).

        Menor |amount_diff| → MAIOR afinidade (negativa: mais tokens em comum
        ordena antes) → `date asc` → posição na lista Omie. A afinidade entra
        DEPOIS do valor porque valor é fato e nome é indício. E entra só como
        ordenação — nenhum candidato é removido por nome que não bate (ver
        `name_affinity`).
        """
        return (self.amount_diff, -self.affinity, self.omie_date, self.omie_index)

    def blind_key(self) -> tuple[Decimal, date, int]:
        """O critério anterior ao fornecedor — serve só para medir se o nome mudou algo."""
        return (self.amount_diff, self.omie_date, self.omie_index)


def _earlier_line_would_take(
    omie_index: int,
    *,
    position: int,
    ordered_entries: list[FileEntryForMatch],
    matched_file_ids: set[str],
    candidates_by_line: dict[int, list[_Candidate]],
    used_omie_indices: set[int],
) -> bool:
    """Alguma linha ANTERIOR, ainda sem par, levaria este lançamento na vez dela?

    É a pergunta que o algoritmo antigo (linha a linha) respondia com "sim" em
    silêncio: a linha anterior escolhia entre os candidatos dela pelo critério
    de linha e consumia o lançamento antes de a linha com evidência chegar.
    Serve só para o contador `steals_prevented_by_supplier` — não decide nada.
    """
    for earlier in range(position):
        if ordered_entries[earlier].id in matched_file_ids:
            continue
        free = [
            candidate
            for candidate in candidates_by_line.get(earlier, [])
            if candidate.omie_index not in used_omie_indices
        ]
        if free and min(free, key=_Candidate.evidence_key).omie_index == omie_index:
            return True
    return False


def match(
    file_entries: list[FileEntryForMatch],
    omie_movements: list[OmieMovement],
    tolerance_days: int = DATE_DIVERGENCE_RANGE,
) -> MatchResult:
    """Cruza arquivo x Omie aplicando as regras invioláveis.

    Algoritmo (passadas por proximidade de data, pares por evidência):
        Para `dias` de 0 até `tolerance_days`, nesta ordem:
            1. Monta todos os pares (linha ainda sem par, lançamento ainda não
               consumido) com |days_diff| == `dias` E |amount_diff| ≤ 0.01.
            2. Ordena os pares por `(|amount_diff|, -afinidade de fornecedor,
               data do lançamento, ordem (data, id) da linha, posição na lista)`.
            3. Percorre nessa ordem e fecha cada par cujos dois lados ainda
               estão livres.
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

    Por que pares por evidência DENTRO da passada, e não linha a linha: o
    mesmo roubo acontecia com data e valor iguais. Report da Bruna (15/09/2026):
    dois PIX de mesmo valor no mesmo dia; a descrição de um trazia só uma sigla
    de 2 letras (a afinidade descarta tokens curtos, e o cadastro Omie traz o
    nome da pessoa), então essa linha não compartilhava token com fornecedor
    NENHUM; a outra compartilhava dois com o lançamento dela. Decidindo linha a
    linha em ordem `(data, id)` — id é UUID, aleatório — a linha sem sinal vinha
    antes metade das vezes, levava o lançamento da outra pela ordem da lista, e
    a outra ficava com a sobra: cara ou coroa por sessão, um pareamento errado e
    duas anomalias falsas. Ordenando os PARES da passada pela evidência, o par
    com nome fecha antes de a linha sem sinal escolher, e a linha sem sinal fica
    com o que sobra — o certo. Para cada linha, o par escolhido continua sendo o
    melhor candidato livre DELA no momento em que fecha; o que muda é só QUEM
    decide primeiro.

    Continua determinístico e auditável: não é matching ótimo global
    (Hungarian/etc) nem heurística — é guloso DENTRO de cada passada, e a
    ordem de todas as decisões é derivada dos dados, não da ordem de leitura
    do arquivo.

    Args:
        file_entries: linhas do arquivo. A ordem da lista NÃO afeta o
            resultado — as linhas entram na ordem `(transaction_date, id)`.
        omie_movements: lista combinada de movimentações Omie (extrato +
            títulos). A posição na lista só desempata pares idênticos em valor,
            afinidade, data e linha (candidatos indistinguíveis).
        tolerance_days: número de passadas além da exata (CLAUDE.md §5.2).
            Default é `DATE_DIVERGENCE_RANGE` (3) — fixo no produto desde a
            FASE 1. O parâmetro existe só para testar o algoritmo com outros
            ranges; o sistema sempre usa o default. Aceita qualquer inteiro ≥ 0.

    Returns:
        `MatchResult` com pares (file_id, omie_id), índices Omie sobrando, o
        `days_diff_by_file_id` (para o caller classificar conciliado x
        conciliado_data_divergente) e os contadores de `TieStats`.
    """
    used_omie_indices: set[int] = set()
    matched_file_ids: set[str] = set()
    matches: list[tuple[str, int]] = []
    days_diff_by_file_id: dict[str, int] = {}
    ties = 0
    broken_by_supplier = 0
    steals_prevented_by_supplier = 0

    # Ordem das linhas. Derivada dos dados (data, depois id) em vez da ordem de
    # leitura: o resultado deixa de depender de o parser ter entregue o extrato
    # cronológico ou não. Dentro da passada ela é só o PENÚLTIMO critério — o
    # par mais forte fecha primeiro, venha de que linha vier.
    ordered_entries = sorted(file_entries, key=lambda fe: (fe.transaction_date, fe.id))

    for pass_days in range(tolerance_days + 1):
        # 1) Todos os pares possíveis da passada, cada um com a sua evidência.
        candidates_by_line: dict[int, list[_Candidate]] = {}
        for position, file_entry in enumerate(ordered_entries):
            if file_entry.id in matched_file_ids:
                continue
            for idx, omie in enumerate(omie_movements):
                if idx in used_omie_indices:
                    continue
                if abs((file_entry.transaction_date - omie.transaction_date).days) != pass_days:
                    continue
                if not _amount_within_tolerance(file_entry.amount, omie.amount):
                    continue
                candidates_by_line.setdefault(position, []).append(
                    _Candidate(
                        amount_diff=abs(file_entry.amount - omie.amount),
                        affinity=supplier_affinity(omie.supplier, file_entry.description),
                        omie_date=omie.transaction_date,
                        omie_index=idx,
                    )
                )

        # 2) O par com mais evidência fecha primeiro. A chave é a da linha
        #    (`evidence_key`, sem a posição na lista) seguida da ordem da linha
        #    e só então da posição na lista Omie: entre pares indistinguíveis,
        #    a linha anterior decide, e escolhe o primeiro da lista — o mesmo
        #    desempate que já valia linha a linha.
        pairs = sorted(
            (
                (candidate.evidence_key()[:3], position, candidate.omie_index, candidate)
                for position, candidates in candidates_by_line.items()
                for candidate in candidates
            ),
            key=lambda pair: pair[:3],
        )

        # 3) Fecha na ordem, pulando o que já foi consumido.
        for _evidence, position, _omie_index, candidate in pairs:
            file_entry = ordered_entries[position]
            if file_entry.id in matched_file_ids or candidate.omie_index in used_omie_indices:
                continue

            # Instrumentação — a MESMA definição de antes, avaliada no momento em
            # que a linha fecha: um "empate" é mais de um candidato livre desta
            # linha disputando o melhor |amount_diff|, e "o nome desempatou" é
            # o escolhido ser diferente do que valor + data escolheriam.
            free = [
                other
                for other in candidates_by_line[position]
                if other.omie_index not in used_omie_indices
            ]
            best_amount_diff = min(other.amount_diff for other in free)
            if sum(1 for other in free if other.amount_diff == best_amount_diff) > 1:
                ties += 1
                if min(free, key=_Candidate.blind_key).omie_index != candidate.omie_index:
                    broken_by_supplier += 1

            # O sinal desta correção: sem evidência, uma linha anterior teria
            # levado este lançamento. Só faz sentido quando o par TEM evidência
            # — sem afinidade, o par anterior ordenaria antes deste.
            if candidate.affinity > 0 and _earlier_line_would_take(
                candidate.omie_index,
                position=position,
                ordered_entries=ordered_entries,
                matched_file_ids=matched_file_ids,
                candidates_by_line=candidates_by_line,
                used_omie_indices=used_omie_indices,
            ):
                steals_prevented_by_supplier += 1

            used_omie_indices.add(candidate.omie_index)
            matched_file_ids.add(file_entry.id)
            matches.append((file_entry.id, omie_movements[candidate.omie_index].omie_id))
            days_diff_by_file_id[file_entry.id] = pass_days

    # `matches` sai na ordem das linhas do arquivo, não na ordem das passadas nem
    # da evidência — o consumidor não deve enxergar o detalhe do algoritmo.
    order_by_file_id = {fe.id: pos for pos, fe in enumerate(ordered_entries)}
    matches.sort(key=lambda pair: order_by_file_id[pair[0]])

    unmatched_omie_indices = [
        idx for idx in range(len(omie_movements)) if idx not in used_omie_indices
    ]
    return MatchResult(
        matches=matches,
        unmatched_omie_indices=unmatched_omie_indices,
        days_diff_by_file_id=days_diff_by_file_id,
        tie_stats=TieStats(
            ties=ties,
            broken_by_supplier=broken_by_supplier,
            steals_prevented_by_supplier=steals_prevented_by_supplier,
        ),
    )
