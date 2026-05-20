"""Busca lançamentos do Omie e converte em DTOs unificados (BACK 8.2 + 8.3).

Decisões de design:
    - Retorna `OmieMovement` (DTO interno do matcher) em vez de schemas brutos
      do Omie. O matcher não precisa conhecer `cNatureza`/`status_titulo`/etc;
      basta `(omie_id, date, signed_amount, status, is_realized)`.
    - Pagar = saída → sinal negativo. Receber = entrada → sinal positivo.
    - Período do extrato JÁ vem expandido pelo caller (CLAUDE.md §5.3 +
      doc §13). Aqui só repassamos para o cliente.
    - Para títulos (pagar/receber), usamos `[reference_month, last_day_of_month]`.
      Valores de `data_vencimento` fora desse intervalo não são problema do
      matcher — `tolerance_days` é o filtro real lá adiante.
    - Cada status é uma chamada separada ao Omie. CLAUDE.md TODO em
      `todo_omie_sandbox_credentials`: validar com Galhardo se
      `filtrar_por_status` aceita múltiplos valores; até lá, 2 chamadas.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from app.db.models import OmieEntryStatus
from app.integrations.omie.client import OmieClient
from app.integrations.omie.schemas import OmieTituloStatus
from app.modules.reconciliations.processing.matcher import OmieMovement

# Mapeia o valor do FILTRO `filtrar_por_status` (UPPERCASE, doc oficial Omie:
# ATRASADO, AVENCER, ...) para o status CANÔNICO do DB (CamelCase, ver
# `OmieEntryStatus`: Atrasado, Previsto, Conciliado). O canônico segue o
# `ListarExtrato.cSituacao`, que devolve em CamelCase — usamos a mesma escala
# em todo o domínio para que `anomalies.create_structural_anomalies` possa
# comparar contra o enum sem case-insensitive ad hoc.
#
# Por que `AVENCER → Previsto`: AVENCER é só o nome do FILTRO (request) na
# nomenclatura Omie; o conceito de negócio "título com vencimento futuro" é
# o que CLAUDE.md §5.7 chama de "Previsto". Mantemos `Previsto` no DB para
# consistência com o que vem de `ListarExtrato`.
_TITULO_STATUS_TO_CANONICAL: dict[str, str] = {
    OmieTituloStatus.ATRASADO.value: OmieEntryStatus.ATRASADO.value,
    OmieTituloStatus.AVENCER.value: OmieEntryStatus.PREVISTO.value,
}


def _last_day_of_month(reference_month: date) -> date:
    """Dado o 1º dia de um mês, retorna o último dia (inclusivo).

    `monthrange(y, m)` retorna `(weekday_of_first, num_days)`.
    """
    last_day = monthrange(reference_month.year, reference_month.month)[1]
    return reference_month.replace(day=last_day)


async def fetch_realized(
    omie_client: OmieClient,
    *,
    omie_conta_id: int,
    period_start: date,
    period_end: date,
    tolerance_days: int,
) -> list[OmieMovement]:
    """BACK 8.2 — busca lançamentos REALIZADOS via `ListarExtrato`.

    Período expandido (CLAUDE.md §5.3):
        [period_start - tolerance_days, period_end + tolerance_days]

    Mapeamento de campos (nomes refletem o response real do Omie —
    auditoria CRÍTICO-1/2, corrigido em 19/05/2026):
        - omie_id          ← n_cod_lancamento  (alias `nCodLancamento`)
        - transaction_date ← d_data_lancamento (alias `dDataLancamento`)
        - amount           ← signed_amount     (D negativo, C positivo)
        - status           ← c_situacao        (alias `cSituacao`)
        - is_realized      ← True (veio do extrato)

    Args:
        omie_client: cliente Omie já autenticado (factory descriptografa
            credenciais).
        omie_conta_id: nCodCC da conta a conciliar.
        period_start/period_end: período do arquivo (datas em claro do
            ParsedStatement). Tolerância é aplicada AQUI para que o caller
            não precise duplicar a regra.
        tolerance_days: dias subtraídos/adicionados ao período. Mesmo valor
            usado depois pelo matcher (CLAUDE.md §5.2 + §5.3).

    Returns:
        Lista (possivelmente vazia) de `OmieMovement`. Não persiste nada.
    """
    expanded_start = period_start - timedelta(days=tolerance_days)
    expanded_end = period_end + timedelta(days=tolerance_days)

    raw = await omie_client.listar_extrato(
        n_cod_cc=omie_conta_id,
        data_inicial=expanded_start,
        data_final=expanded_end,
    )
    return [
        OmieMovement(
            omie_id=item.n_cod_lancamento,
            transaction_date=item.d_data_lancamento,
            amount=item.signed_amount,
            status=item.c_situacao,
            is_realized=True,
        )
        for item in raw
    ]


async def fetch_pending(
    omie_client: OmieClient,
    *,
    omie_conta_id: int,
    reference_month: date,
) -> list[OmieMovement]:
    """BACK 8.3 — busca lançamentos PENDENTES (Atrasado + Previsto) em
    `ListarContasPagar` e `ListarContasReceber`.

    Faz 4 chamadas: pagar(ATRASADO), pagar(PREVISTO), receber(ATRASADO),
    receber(PREVISTO). Cada uma já pagina internamente até esgotar.

    Sinal aritmético:
        - Pagar     = saída de caixa → amount NEGATIVO
        - Receber   = entrada de caixa → amount POSITIVO
    O Omie devolve sempre valor absoluto positivo; o sinal é convenção
    interna nossa pra que o matcher use a MESMA escala do arquivo.

    Args:
        omie_client: cliente já autenticado.
        omie_conta_id: nCodCC.
        reference_month: 1º dia do mês de referência (como salvo no DB).
            Convertido em [reference_month, último_dia_do_mês].

    Returns:
        Lista combinada — `is_realized=False` para todos.
    """
    last_day = _last_day_of_month(reference_month)
    movements: list[OmieMovement] = []

    for status in (OmieTituloStatus.ATRASADO, OmieTituloStatus.AVENCER):
        canonical_status = _TITULO_STATUS_TO_CANONICAL[status.value]
        pagar = await omie_client.listar_contas_pagar(
            conta_corrente_id=omie_conta_id,
            data_de=reference_month,
            data_ate=last_day,
            status=status,
        )
        movements.extend(
            OmieMovement(
                omie_id=t.codigo_lancamento_omie,
                transaction_date=t.data_vencimento,
                # Pagar é saída: garante negativo independente do sinal vindo.
                amount=-abs(t.valor_documento),
                status=canonical_status,
                is_realized=False,
            )
            for t in pagar
        )

        receber = await omie_client.listar_contas_receber(
            conta_corrente_id=omie_conta_id,
            data_de=reference_month,
            data_ate=last_day,
            status=status,
        )
        movements.extend(
            OmieMovement(
                omie_id=t.codigo_lancamento_omie,
                transaction_date=t.data_vencimento,
                amount=abs(t.valor_documento),
                status=canonical_status,
                is_realized=False,
            )
            for t in receber
        )

    return movements


def deduplicate_by_id(movements: list[OmieMovement]) -> list[OmieMovement]:
    """Remove duplicatas por `omie_id` preservando a primeira ocorrência.

    Justificativa: um título "Atrasado" pode aparecer tanto em
    `ListarContasPagar(ATRASADO)` quanto no extrato (`ListarExtrato`) se já
    estiver parcialmente conciliado no Omie. Sem dedupe, o matcher veria 2
    candidatos para o mesmo `omie_lancamento_id` e poderia tentar consumir
    os dois — quebraria a UNIQUE implícita do `omie_lancamento_id` por
    sessão (Doc §0).

    Mantém a primeira aparição porque `fetch_realized` é chamado antes de
    `fetch_pending` no orquestrador — o estado conciliado/realizado tem
    precedência sobre o título pendente.
    """
    seen: set[int] = set()
    deduped: list[OmieMovement] = []
    for mov in movements:
        if mov.omie_id in seen:
            continue
        seen.add(mov.omie_id)
        deduped.append(mov)
    return deduped
