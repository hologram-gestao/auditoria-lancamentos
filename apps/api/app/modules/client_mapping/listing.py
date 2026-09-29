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

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from app.core.logging import get_logger
from app.db.models import DecisionOrigin, DecisionType, ProviderType
from app.modules.client_mapping.service import CHART_SOURCE_TYPE
from app.modules.client_movements.competence import current_competence

if TYPE_CHECKING:
    from datetime import date

    from app.db.models import Client
    from app.db.models.mapping_catalog import MappingDestination
    from app.modules.client_accounting_chart.service import AccountRef
    from app.modules.client_chart_of_accounts.schemas import ResolvedNames
    from app.modules.client_file_categories.registry import ResolvedFileCategoryNames
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.client_mapping.service import ClientMappingDecisionService, DecisionView
    from app.modules.mapping_catalog.repository import MappingCatalogRepository

log = get_logger(__name__)

type MappingSituation = Literal["herdada", "confirmada", "nao_mapear", "sem_decisao"]


class CategoryNameResolver(Protocol):
    """O acessor de nome de categoria — na produção, `ChartOfAccountsSyncService`."""

    async def resolve_names(self, client: Client) -> ResolvedNames: ...


class FileCategoryNameResolver(Protocol):
    """O acessor de rótulo das categorias de ARQUIVO — na produção, `FileCategoryRegistry` (S14)."""

    async def resolve_names(self, client: Client) -> ResolvedFileCategoryNames: ...


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
    #: S16 (destino `conta_contabil`): conta do plano do cliente, histórico decifrado
    #: e a marca de decisão legada do catálogo.
    accounting_account_id: UUID | None = None
    history: str | None = None
    requires_redo: bool = False

    def __repr__(self) -> str:
        # O histórico é texto do cliente: nunca num repr que acabe em log.
        return (
            f"<MappingRow {self.source_type}:{self.category_code} {self.situation} "
            f"target={self.target_code!r} account={self.accounting_account_id}>"
        )


@dataclass(frozen=True, slots=True)
class NamedRow:
    """A linha com os nomes resolvidos NA LEITURA (nunca persistidos)."""

    row: MappingRow
    category_name: str | None
    category_name_resolved: bool
    target_name: str | None
    #: S16: código e nome (decifrado) da conta do plano do cliente.
    account: AccountRef | None = None


@dataclass(frozen=True, slots=True)
class SituationCounts:
    """As quatro situações contadas sobre o universo INTEIRO do destino (86e3f55bd).

    Contadas sobre a MESMA lista que `page` filtra, na mesma competência, antes
    de qualquer recorte: é o que faz "clicar em Sem decisão" devolver no total
    da paginação o número do contador. A situação não é coluna (sai de
    `resolve_vigente`); um `GROUP BY` em SQL seria uma segunda implementação da
    vigência, e a lista já está carregada inteira aqui — contar custa zero query.
    """

    total: int
    herdada: int
    confirmada: int
    nao_mapear: int
    sem_decisao: int

    @classmethod
    def of(cls, rows: list[MappingRow]) -> SituationCounts:
        by_situation = Counter(row.situation for row in rows)
        return cls(
            total=len(rows),
            herdada=by_situation["herdada"],
            confirmada=by_situation["confirmada"],
            nao_mapear=by_situation["nao_mapear"],
            sem_decisao=by_situation["sem_decisao"],
        )


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
        file_names: FileCategoryNameResolver | None = None,
    ) -> None:
        self._repo = repository
        self._decisions = decisions
        self._catalog = catalog
        self._names = names
        # S14 (BACK 14.4): o rótulo das categorias `arquivo`. Opcional para quem
        # monta a leitura sem registry (testes) — sem ele, a linha de arquivo sai
        # com o código e `categoryNameResolved=false`, como qualquer origem muda.
        self._file_names = file_names

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
                    accounting_account_id=view.accounting_account_id if view else None,
                    history=view.history if view else None,
                    requires_redo=view.legacy_catalog_target if view else False,
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
        named, total, competence, _counts = await self.page_and_counts(
            client,
            destination_type,
            situation=situation,
            code_prefix=code_prefix,
            page=page,
            page_size=page_size,
            today=today,
        )
        return named, total, competence

    async def page_and_counts(
        self,
        client: Client,
        destination_type: str,
        *,
        situation: MappingSituation | None,
        code_prefix: str | None,
        page: int,
        page_size: int,
        today: date | None = None,
    ) -> tuple[list[NamedRow], int, date, SituationCounts]:
        """A página e as contagens por situação, de UMA carga do universo.

        As contagens saem ANTES de qualquer filtro: independem de página, situação
        e código, e são as mesmas em qualquer recorte da lista.
        """
        destination = await self._decisions.resolve_destination(client, destination_type)
        competence = current_competence(today)
        rows = await self.universe(client, destination, competence)
        counts = SituationCounts.of(rows)
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
            counts,
        )

    async def with_names(
        self, client: Client, destination: MappingDestination, rows: list[MappingRow]
    ) -> list[NamedRow]:
        """Nomes da categoria (origem, fail-soft) e do alvo (catálogo da organização).

        Duas fontes de nome, uma por tipo de origem: o plano de contas (Omie, cache
        de 6 h) e o registry de categorias do ARQUIVO (S14 — rótulo cifrado com a
        DEK do cliente, decifrado na leitura). A "marcação derivada do arquivo" é o
        próprio `source_type` da linha; não existe coluna nova.
        """
        category_names = await self._category_names(client, rows)
        file_names = await self._file_category_names(client, rows)
        targets = await self._catalog.get_targets_by_codes(
            destination.id, {r.target_code for r in rows if r.target_code}
        )
        # S16: a conta do plano do cliente (código + nome decifrado NA LEITURA).
        accounts = (
            await self._decisions.accounting.accounts_by_id(
                client, (r.accounting_account_id for r in rows)
            )
            if any(r.accounting_account_id for r in rows)
            else {}
        )
        named: list[NamedRow] = []
        for row in rows:
            name: str | None = None
            resolved = False
            if row.source_type == CHART_SOURCE_TYPE:
                name = category_names.get(row.category_code)
                resolved = name is not None
            elif row.source_type == ProviderType.ARQUIVO.value:
                name = file_names.names.get(row.category_code)
                # `[indecifrável]` sai como nome e `resolved=false`: a tela mostra o
                # marcador, não o código, e sabe que não é um rótulo.
                resolved = name is not None and row.category_code not in file_names.failed
            target = targets.get(row.target_code) if row.target_code else None
            named.append(
                NamedRow(
                    row=row,
                    category_name=name,
                    category_name_resolved=resolved,
                    target_name=target.name if target else None,
                    account=accounts.get(row.accounting_account_id)
                    if row.accounting_account_id
                    else None,
                )
            )
        return named

    async def _file_category_names(
        self, client: Client, rows: list[MappingRow]
    ) -> ResolvedFileCategoryNames:
        from app.modules.client_file_categories.registry import ResolvedFileCategoryNames

        if self._file_names is None or not any(
            r.source_type == ProviderType.ARQUIVO.value for r in rows
        ):
            return ResolvedFileCategoryNames()
        try:
            return await self._file_names.resolve_names(client)
        except Exception:
            # Fail-soft como o plano de contas: a linha sai com o código. Só IDs.
            log.warning("client_mapping_file_category_names_unavailable", client_id=str(client.id))
            return ResolvedFileCategoryNames()

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
