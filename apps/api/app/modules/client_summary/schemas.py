"""Contrato de `GET /api/v1/clients/{client_id}/summary` (86e3k1q3j).

Só IDs, códigos, enums e números: **nenhum nome, descrição ou texto livre** (§4.5).
O nome do tipo de anomalia, do destino e de qualquer coisa que tenha nome é
resolvido pelo front a partir do catálogo que ele já tem. Dinheiro é `Decimal`
serializado como string (§3.4).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ReconciliationStatusCounts(_CamelModel):
    """As quatro chaves SEMPRE presentes, com zero onde não há sessão."""

    processing: int = Field(ge=0)
    reviewing: int = Field(ge=0)
    done: int = Field(ge=0)
    error: int = Field(ge=0)


class ReconciliationsSummary(_CamelModel):
    accounts_total: int = Field(
        ge=0,
        alias="accountsTotal",
        description="Contas do cliente no cache de contas (as que a conciliação oferece).",
    )
    accounts_with_session: int = Field(
        ge=0,
        alias="accountsWithSession",
        description="Contas distintas com conciliação ativa no mês.",
    )
    by_status: ReconciliationStatusCounts = Field(alias="byStatus")


class AnomalyTypeCount(_CamelModel):
    code: str = Field(description="Código do tipo de anomalia; o nome vem do catálogo.")
    count: int = Field(ge=0)


class AnomaliesSummary(_CamelModel):
    open_total: int = Field(ge=0, alias="openTotal")
    by_type: list[AnomalyTypeCount] = Field(
        alias="byType", description="Anomalias em aberto do mês, por código do tipo."
    )
    resolved_in_month: int = Field(ge=0, alias="resolvedInMonth")


class CardPurchasesToPost(_CamelModel):
    count: int = Field(ge=0)
    total_amount: Decimal = Field(
        alias="totalAmount",
        description="Soma das compras (valor negativo, como no extrato). `0.00` sem compras.",
    )


class MappingDestinationSummary(_CamelModel):
    destination_code: str = Field(
        alias="destinationCode", description="O tipo do destino no catálogo da organização."
    )
    without_decision: int = Field(
        ge=0, alias="withoutDecision", description="Categorias sem decisão na competência."
    )
    coverage_pct: Decimal | None = Field(
        alias="coveragePct",
        description=(
            "Cobertura da competência em %, uma casa decimal, a mesma da prévia do "
            "de-para. `null` quando não há movimento com categoria (nunca um 0% "
            "que pareça resultado)."
        ),
    )
    materialized: bool = Field(description="Há materialização deste destino na competência.")


class TitlesSideSummary(_CamelModel):
    overdue_count: int = Field(ge=0, alias="overdueCount")
    overdue_total: Decimal = Field(alias="overdueTotal")


class TitlesSummaryBlock(_CamelModel):
    overdue_count: int = Field(ge=0, alias="overdueCount")
    overdue_total: Decimal = Field(alias="overdueTotal")
    a_pagar: TitlesSideSummary = Field(alias="aPagar")
    a_receber: TitlesSideSummary = Field(alias="aReceber")
    synced_at: datetime | None = Field(alias="syncedAt")
    never_synced: bool = Field(alias="neverSynced")


class LatestSessionSummary(_CamelModel):
    id: UUID
    reference_month: str = Field(alias="referenceMonth", description="`YYYY-MM`.")
    status: str
    account_type: str = Field(alias="accountType")
    created_at: datetime = Field(alias="createdAt")


class ClientSummaryResponse(_CamelModel):
    reference_month: str = Field(alias="referenceMonth", description="`YYYY-MM`.")
    reconciliations: ReconciliationsSummary
    anomalies: AnomaliesSummary
    card_purchases_to_post: CardPurchasesToPost = Field(alias="cardPurchasesToPost")
    mapping: list[MappingDestinationSummary]
    titles: TitlesSummaryBlock | None = Field(
        description=(
            "`null` quando o papel de quem pede não lê a carteira "
            "(`view_client_receivables`); nunca 403."
        ),
    )
    latest_session: LatestSessionSummary | None = Field(alias="latestSession")


class ClientSummaryEnvelope(BaseModel):
    """Envelope `{data: ...}` de `GET /clients/{client_id}/summary`."""

    data: ClientSummaryResponse
