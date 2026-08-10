"""Mede o potencial de pagamento dividido — NÃO altera nenhum match.

Fatia 1 da task 86e2n4r6p, desdobramento da sugestão 1 da Bruna (04/08/2026):
*"Há uma saída de R$ 2.800 no extrato e DUAS SAÍDAS NO OMIE QUE SOMAM O VALOR"*.

O cruzamento é 1-para-1 (CLAUDE.md §5.4), então uma saída de R$ 2.800 lançada no
Omie como R$ 1.500 + R$ 1.300 é matematicamente invisível: a linha vira
`sem_omie` (um falso positivo) e os dois lançamentos viram `missing_in_file`
(mais dois). Permitir soma é mudança ESTRUTURAL — mexe em invariante de domínio,
modelo de dados, tela e relatório.

Antes de pagar esse custo é preciso saber se acontece com frequência que
justifique. E essa pergunta **não tem resposta retroativa**: o conjunto de
lançamentos Omie de um cruzamento não é persistido (`reconciliation_omie_entries`
guarda só os sem par, e sem valor) e o cache de lançamentos é in-memory com TTL.
Só dá para medir daqui para a frente, no momento do processamento — que é o que
este módulo faz.

**Este módulo é uma SONDA.** Não escolhe, não altera, não persiste. Devolve
contadores que o caller loga. Se for removido, o matching não muda em nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from app.modules.reconciliations.processing.matcher import AMOUNT_TOLERANCE

if TYPE_CHECKING:
    from app.modules.reconciliations.processing.matcher import (
        FileEntryForMatch,
        OmieMovement,
    )

# Máximo de parcelas somadas por linha. Dois e três cobrem o caso relatado
# (pagamento único quitando dois títulos) sem entrar em espaço combinatório
# grande. Subir isto sem medir antes é o caminho para um processamento lento.
MAX_PARCELAS = 3

# Teto de candidatos por linha antes de desistir da avaliação. C(40,3) ≈ 9.880
# combinações por linha já é o limite do que se paga numa sonda. Acima disso a
# linha entra em `nao_avaliadas` — NUNCA é contada como "sem soma possível", que
# faria a métrica mentir para baixo justamente nas sessões maiores.
MAX_CANDIDATOS_POR_LINHA = 40


@dataclass(frozen=True, slots=True)
class SplitPaymentProbe:
    """Contadores puros, sem PII. Respondem os critérios da Fatia 1.

    Attributes:
        sem_omie: linhas do arquivo que ficaram sem par. É o denominador.
        fechariam_por_soma: dessas, quantas fechariam somando 2 ou 3 lançamentos
            Omie sem par, dentro da tolerância de R$ 0,01 e da janela de datas.
            É o número que decide se a task estrutural se paga.
        com_agrupamento_omie: das que fechariam, quantas o fizeram com
            lançamentos que COMPARTILHAM o mesmo `nCodLancRelac`. Se for alto, o
            Omie entrega o agrupamento de graça e a implementação é barata e
            determinística; se for baixo, seria preciso testar combinações às
            cegas — bem mais caro.
        nao_avaliadas: linhas que excederam `MAX_CANDIDATOS_POR_LINHA`. Contadas
            à parte de propósito: silêncio aqui leria como "não fecha por soma".
        omie_sem_par: lançamentos Omie sem par (contexto para ler o resto).
        omie_com_lanc_relac: quantos deles trazem `nCodLancRelac` preenchido —
            responde "o Omie de fato agrupa parcelas?" contra response REAL,
            que é o que a §6.8 exige.
    """

    sem_omie: int = 0
    fechariam_por_soma: int = 0
    com_agrupamento_omie: int = 0
    nao_avaliadas: int = 0
    omie_sem_par: int = 0
    omie_com_lanc_relac: int = 0


def probe_split_payments(
    unmatched_file_entries: list[FileEntryForMatch],
    unmatched_omie: list[OmieMovement],
    *,
    tolerance_days: int,
) -> SplitPaymentProbe:
    """Conta quantas linhas sem par fechariam por SOMA. Não altera nada.

    Args:
        unmatched_file_entries: linhas do arquivo que o matcher não casou.
        unmatched_omie: lançamentos Omie que sobraram.
        tolerance_days: mesma janela do matcher (`DATE_DIVERGENCE_RANGE`), para
            a sonda medir sob as MESMAS regras que uma implementação real teria.

    Returns:
        `SplitPaymentProbe` — só contadores.
    """
    fechariam = 0
    com_agrupamento = 0
    nao_avaliadas = 0

    for entry in unmatched_file_entries:
        candidatos = [
            mov
            for mov in unmatched_omie
            if abs((entry.transaction_date - mov.transaction_date).days) <= tolerance_days
        ]
        if len(candidatos) > MAX_CANDIDATOS_POR_LINHA:
            nao_avaliadas += 1
            continue

        combinacao = _primeira_combinacao_que_fecha(entry, candidatos)
        if combinacao is None:
            continue
        fechariam += 1
        relacs = {mov.related_launch_id for mov in combinacao}
        if len(relacs) == 1 and None not in relacs:
            com_agrupamento += 1

    return SplitPaymentProbe(
        sem_omie=len(unmatched_file_entries),
        fechariam_por_soma=fechariam,
        com_agrupamento_omie=com_agrupamento,
        nao_avaliadas=nao_avaliadas,
        omie_sem_par=len(unmatched_omie),
        omie_com_lanc_relac=sum(1 for mov in unmatched_omie if mov.related_launch_id is not None),
    )


def _primeira_combinacao_que_fecha(
    entry: FileEntryForMatch,
    candidatos: list[OmieMovement],
) -> tuple[OmieMovement, ...] | None:
    """Menor combinação (2, depois 3) cuja soma fecha dentro da tolerância.

    Para de procurar na primeira que serve: a sonda mede SE fecharia, não qual
    seria o par escolhido. Escolher é problema da Fatia 2, e aí a regra de
    desempate precisará ser decidida de propósito, não herdada daqui.
    """
    for n in range(2, MAX_PARCELAS + 1):
        for combinacao in combinations(candidatos, n):
            total = sum(mov.amount for mov in combinacao)
            if abs(entry.amount - total) <= AMOUNT_TOLERANCE:
                return combinacao
    return None
