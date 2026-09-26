"""Leitura do de-para por destino: o UNIVERSO de categorias e a situação de cada uma (Sprint 12, BACK 12.5).

**Uma linha por categoria do universo**, não por decisão: plano de contas
sincronizado + categorias vistas na base de movimentos (R0) + categorias que já têm
decisão. É o que deixa "sem decisão" aparecer como pendência em vez de sumir.

**Quatro situações, calculadas no SERVIDOR** sobre o universo inteiro, antes de
paginar: `herdada` (alvo proposto pelo plano de contas), `confirmada` (alvo assumido
por pessoa), `nao_mapear` (decisão explícita — de qualquer origem) e `sem_decisao`.
Filtrar depois de paginar devolveria "3 pendentes" quando existem 80.

**Nome é runtime, nunca buscável (§4.5).** A busca é por CÓDIGO. O nome da categoria
vem do MESMO acessor que o plano de contas usa (`ChartOfAccountsSyncService.
resolve_names`, cache de 6 h), fail-soft: origem fora do ar devolve o código com
`categoryNameResolved=false` e 200.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from app.core.logging import get_logger
from app.db.models import DecisionOrigin, DecisionType, ProviderType
from app.modules.client_mapping.service import CHART_SOURCE_TYPE
from app.modules.client_movements.competence import current_competence

if TYPE_CHECKING:
    from datetime import date

    from app.db.models import Client
    from app.db.models.mapping_catalog import MappingDestination
    from app.modules.client_chart_of_accounts.schemas import ResolvedNames
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.client_mapping.service import ClientMappingDecisionService, DecisionView
    from app.modules.mapping_catalog.repository import MappingCatalogRepository

log = get_logger(__name__)

type MappingSituation = Literal["herdada", "confirmada", "nao_mapear", "sem_decisao"]


class CategoryNameResolver(Protocol):
    """O acessor de nome de categoria — na produção, `ChartOfAccountsSyncService`."""

    async def resolve_names(self, client: Client) -> ResolvedNames: ...


@dataclass(frozen=True, slots=True)
class MappingRow:
    """Uma categoria do universo com a decisão vigente — só códigos."""

    source_type: str
    category_code: str
    situation: MappingSituation
    decision_type: str | None
    target_code: str | None
    effective_from: date | None
    divergent: bool
    origin_dre_code: str | None


@dataclass(frozen=True, slots=True)
class NamedRow:
    """A linha com os nomes resolvidos NA LEITURA (nunca persistidos)."""

    row: MappingRow
    category_name: str | None
    category_name_resolved: bool
    target_name: str | None


def situation_of(view: DecisionView | None) -> MappingSituation:
    """A situação de uma categoria a partir da decisão vigente — um lugar só."""
    if view is None:
        return "sem_decisao"
    if view.decision_type == DecisionType.NAO_MAPEAR.value:
        return "nao_mapear"
    if view.origin == DecisionOrigin.HERDADA.value:
        return "herdada"
    return "confirmada"


class ClientMappingListService:
    def __init__(
        self,
        repository: ClientMappingRepository,
        *,
        decisions: ClientMappingDecisionService,
        catalog: MappingCatalogRepository,
        names: CategoryNameResolver,
    ) -> None:
        self._repo = repository
        self._decisions = decisions
        self._catalog = catalog
        self._names = names

    async def universe(
        self, client: Client, destination: MappingDestination, competence: date
    ) -> list[MappingRow]:
        """O universo inteiro, ordenado por (tipo de origem, código) — ordem TOTAL."""
        vigentes = await self._decisions.vigentes(client, destination, competence)
        decided_keys = set(await self._decisions_keys(client, destination))
        keys: set[tuple[str, str]] = {
            (CHART_SOURCE_TYPE, code) for code in await self._repo.chart_category_codes(client.id)
        }
        keys |= await self._repo.movement_category_keys(client.id)
        keys |= decided_keys
        rows: list[MappingRow] = []
        for key in sorted(keys):
            view = vigentes.get(key)
            rows.append(
                MappingRow(
                    source_type=key[0],
                    category_code=key[1],
                    situation=situation_of(view),
                    decision_type=view.decision_type if view else None,
                    target_code=view.target_code if view else None,
                    effective_from=view.effective_from if view else None,
                    divergent=view.divergent if view else False,
                    origin_dre_code=view.origin_dre_code if view else None,
                )
            )
        return rows

    async def page(
        self,
        client: Client,
        destination_type: str,
        *,
        situation: MappingSituation | None,
        code_prefix: str | None,
        page: int,
        page_size: int,
        today: date | None = None,
    ) -> tuple[list[NamedRow], int, date]:
        """Página filtrada NO SERVIDOR, com os nomes resolvidos só para ela."""
        destination = await self._decisions.resolve_destination(client, destination_type)
        competence = current_competence(today)
        rows = await self.universe(client, destination, competence)
        if situation is not None:
            rows = [r for r in rows if r.situation == situation]
        if code_prefix:
            rows = [r for r in rows if r.category_code.startswith(code_prefix)]
        total = len(rows)
        start = (page - 1) * page_size
        return (
            await self.with_names(client, destination, rows[start : start + page_size]),
            total,
            competence,
        )

    async def with_names(
        self, client: Client, destination: MappingDestination, rows: list[MappingRow]
    ) -> list[NamedRow]:
        """Nomes da categoria (origem, fail-soft) e do alvo (catálogo da organização)."""
        category_names = await self._category_names(client, rows)
        targets = await self._catalog.get_targets_by_codes(
            destination.id, {r.target_code for r in rows if r.target_code}
        )
        named: list[NamedRow] = []
        for row in rows:
            name = (
                category_names.get(row.category_code)
                if row.source_type == CHART_SOURCE_TYPE
                else None
            )
            target = targets.get(row.target_code) if row.target_code else None
            named.append(
                NamedRow(
                    row=row,
                    category_name=name,
                    category_name_resolved=name is not None,
                    target_name=target.name if target else None,
                )
            )
        return named

    async def _category_names(self, client: Client, rows: list[MappingRow]) -> dict[str, str]:
        if not any(r.source_type == ProviderType.OMIE.value for r in rows):
            return {}
        try:
            resolved = await self._names.resolve_names(client)
        except Exception:
            # `resolve_names` já é fail-soft; isto cobre o 409 da taxonomia da S9 ao
            # montar o acessor. Só IDs no log (§3.3) — nunca `except: pass`.
            log.info("client_mapping_category_names_unavailable", client_id=str(client.id))
            return {}
        return dict(resolved.categories)

    async def _decisions_keys(
        self, client: Client, destination: MappingDestination
    ) -> list[tuple[str, str]]:
        decisions = await self._repo.list_decisions(client.id, destination.id)
        return [(d.source_type, d.category_code) for d in decisions]
