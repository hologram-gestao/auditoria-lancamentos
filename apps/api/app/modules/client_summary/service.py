"""Resumo do cliente (86e3k1q3j): as pendências do mês para o menu e o painel.

Um request, um punhado de contagens. **Nada aqui recalcula regra de outro módulo:**

- de-para: a situação de cada categoria é `client_mapping.listing.situation_counts`
  (a mesma vigência e o mesmo universo da lista) e a cobertura é a de
  `client_mapping.apply.apply_mapping` (a mesma da prévia);
- carteira: os vencidos são os de `ClientTitlesReadService.summary`, o mesmo bloco
  que a tela da carteira mostra.

Não lê a origem (Omie ou arquivo), não sincroniza nada e não decifra nada.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from app.core.authz import Permission, has_permission
from app.db.models import ReconciliationStatus
from app.modules.client_mapping.apply import apply_mapping
from app.modules.client_mapping.listing import situation_counts
from app.modules.client_mapping.vigencia import add_months
from app.modules.client_movements.competence import format_competence
from app.modules.client_summary.schemas import (
    AnomaliesSummary,
    AnomalyTypeCount,
    CardPurchasesToPost,
    ClientSummaryResponse,
    LatestSessionSummary,
    MappingDestinationSummary,
    ReconciliationsSummary,
    ReconciliationStatusCounts,
    TitlesSideSummary,
    TitlesSummaryBlock,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from app.core.authz import CurrentUser
    from app.db.models import Client, ClientMappingDecision
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.client_summary.repository import CategoryTotal, ClientSummaryRepository
    from app.modules.client_titles.repository import AgingTotals
    from app.modules.client_titles.service import ClientTitlesReadService
    from app.modules.mapping_catalog.repository import MappingCatalogRepository

#: A cobertura no resumo tem uma casa: é contador de menu, não relatório.
_COVERAGE_QUANTUM = Decimal("0.1")

#: Quantos meses ANTERIORES ao de referência definem as contas habituais (o mês de
#: referência entra sempre). A meta do card "Conciliações do mês" é o conjunto
#: habitual, e não o cache de contas do Omie: o cache traz caixinha,
#: adiantamento, reembolso e cartões que ninguém concilia todo mês, e com ele o
#: card media o fechamento contra contas que nunca entram nele. Conta conciliada
#: em algum destes meses é conta que o escritório concilia; a que ficou de fora
#: por mais tempo volta para o habitual na primeira conciliação.
HABITUAL_MONTHS = 3


class ClientSummaryService:
    def __init__(
        self,
        repository: ClientSummaryRepository,
        *,
        mapping: ClientMappingRepository,
        catalog: MappingCatalogRepository,
        titles: ClientTitlesReadService,
    ) -> None:
        self._repo = repository
        self._mapping = mapping
        self._catalog = catalog
        self._titles = titles

    async def summary(
        self, client: Client, user: CurrentUser, month: date
    ) -> ClientSummaryResponse:
        latest = await self._repo.latest_session(client.id, user)
        return ClientSummaryResponse(
            reference_month=format_competence(month),
            reconciliations=await self._reconciliations(client, user, month),
            anomalies=await self._anomalies(client, user, month),
            card_purchases_to_post=await self._card_purchases(client, user, month),
            mapping=await self._mapping_destinations(client, user, month),
            titles=(
                await self._titles_block(client)
                if has_permission(user, Permission.VIEW_CLIENT_RECEIVABLES)
                else None
            ),
            latest_session=(
                LatestSessionSummary(
                    id=latest.id,
                    reference_month=format_competence(latest.reference_month),
                    status=latest.status,
                    account_type=latest.account_type,
                    created_at=latest.created_at,
                )
                if latest is not None
                else None
            ),
        )

    async def _reconciliations(
        self, client: Client, user: CurrentUser, month: date
    ) -> ReconciliationsSummary:
        by_status = await self._repo.session_status_counts(client.id, month, user)
        return ReconciliationsSummary(
            accounts_total=await self._repo.accounts_total(client.id, user),
            accounts_with_session=await self._repo.accounts_with_session(client.id, month, user),
            habitual_account_ids=await self._repo.habitual_account_ids(
                client.id, add_months(month, -HABITUAL_MONTHS), month, user
            ),
            by_status=ReconciliationStatusCounts(
                processing=by_status.get(ReconciliationStatus.PROCESSING.value, 0),
                reviewing=by_status.get(ReconciliationStatus.REVIEWING.value, 0),
                done=by_status.get(ReconciliationStatus.DONE.value, 0),
                error=by_status.get(ReconciliationStatus.ERROR.value, 0),
            ),
        )

    async def _anomalies(self, client: Client, user: CurrentUser, month: date) -> AnomaliesSummary:
        counts = await self._repo.anomaly_counts(client.id, month, user)
        return AnomaliesSummary(
            open_total=sum(c.open for c in counts),
            by_type=[AnomalyTypeCount(code=c.code, count=c.open) for c in counts if c.open > 0],
            resolved_in_month=sum(c.resolved for c in counts),
        )

    async def _card_purchases(
        self, client: Client, user: CurrentUser, month: date
    ) -> CardPurchasesToPost:
        count, total = await self._repo.card_purchases_to_post(client.id, month, user)
        return CardPurchasesToPost(count=count, total_amount=total)

    async def _mapping_destinations(
        self, client: Client, user: CurrentUser, month: date
    ) -> list[MappingDestinationSummary]:
        """Um item por destino ATIVO do catálogo da organização do cliente."""
        rows = await self._catalog.list_destinations(
            viewer=user, organization_id=client.organization_id
        )
        destinations = [row.destination for row in rows if row.destination.active]
        if not destinations:
            return []
        movements = await self._repo.movement_totals_by_category(client.id, month, user)
        result: list[MappingDestinationSummary] = []
        for destination in destinations:
            decisions = await self._mapping.list_decisions(client.id, destination.id)
            counts = await situation_counts(self._mapping, client.id, decisions, month)
            result.append(
                MappingDestinationSummary(
                    destination_code=destination.destination_type,
                    without_decision=counts.sem_decisao,
                    coverage_pct=_coverage(movements, decisions, month),
                    materialized=(
                        await self._mapping.latest_version(client.id, destination.id, month) > 0
                    ),
                )
            )
        return result

    async def _titles_block(self, client: Client) -> TitlesSummaryBlock:
        summary = await self._titles.summary(client)
        a_pagar = _side(summary.a_pagar)
        a_receber = _side(summary.a_receber)
        return TitlesSummaryBlock(
            overdue_count=a_pagar.overdue_count + a_receber.overdue_count,
            overdue_total=a_pagar.overdue_total + a_receber.overdue_total,
            a_pagar=a_pagar,
            a_receber=a_receber,
            synced_at=summary.synced_at,
            never_synced=summary.nunca_sincronizada,
        )


def _side(totals: AgingTotals) -> TitlesSideSummary:
    return TitlesSideSummary(overdue_count=totals.qtd_vencido, overdue_total=totals.total_vencido)


def _coverage(
    movements: Sequence[CategoryTotal], decisions: Sequence[ClientMappingDecision], month: date
) -> Decimal | None:
    """A cobertura da `apply_mapping`, arredondada a uma casa para o menu.

    `target_code_of` vazio de propósito: a cobertura depende só da situação de cada
    linha (alvo, não mapear, sem decisão), nunca do código do alvo.
    """
    result = apply_mapping(movements, decisions, month, target_code_of={})
    pct = result.coverage_pct
    return None if pct is None else pct.quantize(_COVERAGE_QUANTUM, rounding=ROUND_HALF_EVEN)
