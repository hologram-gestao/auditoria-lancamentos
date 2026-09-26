"""Prévia e materialização IMUTÁVEL do de-para (Sprint 12, BACK 12.6 — R3/R5).

**A prévia e a materialização fazem o MESMO cálculo** (`apply_mapping`, pura) sobre a
MESMA entrada — a base de movimentos presentes da competência e o conjunto completo de
vigências do destino. A prévia devolve um TOKEN (hash da entrada + saída); a
materialização RECALCULA no servidor (nunca confia em número vindo do cliente) e só
grava se o token bater. É isso que garante "nada materializa sem prévia confirmada".

**Ordem dos erros da prévia** (a do PRD, com o destino antes da vigência porque a
primeira vigência é DO destino):
  1. competência nunca sincronizada → 409 `BASE_NAO_SINCRONIZADA`;
  2. sincronizada e sem nenhum movimento presente → 409 `SEM_MOVIMENTOS`;
  3. destino não configurado → 409 `DESTINO_NAO_CONFIGURADO` nomeando o tipo;
  4. competência anterior à primeira vigência → 409 `ANTERIOR_A_PRIMEIRA_VIGENCIA`
     com a mais antiga em `details`.
Destino SEM nenhuma vigência não é o caso 4: a prévia sai (tudo `sem_decisao`), porque
ela é justamente o que mostra à pessoa por onde começar.

**Materialização:** versão N+1, nunca sobrescreve (não há `UPDATE`/`DELETE` de
materialização no sistema). Cobertura parcial exige `confirm_partial_coverage` e fica
registrada NO PRÓPRIO registro imutável (autor, data, valor pendente). Depois do
commit, emite `depara_aplicado` pelo emissor da 12.2, com os centavos do resultado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ClientClosedError,
    CompetenceBeforeFirstVigenciaError,
    ConflictError,
    MovementBaseNotSyncedError,
    NoMovementsToMapError,
    PartialCoverageRequiresConfirmationError,
    StaleMappingPreviewError,
)
from app.core.logging import get_logger
from app.db.models import ClientMappingMaterialization, MaterializedSituation, MovementStatus
from app.modules.client_mapping.apply import ApplyResult, apply_mapping
from app.modules.client_mapping.vigencia import earliest_start, resolve_vigentes
from app.modules.client_movements.competence import format_competence
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.db.models import Client
    from app.db.models.mapping_catalog import MappingDestination
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.client_mapping.service import ClientMappingDecisionService
    from app.modules.client_movements.repository import (
        ClientMovementsRepository,
        MovementSyncState,
    )

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MappingPreview:
    destination: MappingDestination
    result: ApplyResult
    base_state: MovementSyncState
    token: str
    latest_version: int


@dataclass(frozen=True, slots=True)
class MaterializationOutcome:
    id: UUID
    version: int
    preview: MappingPreview
    partial_coverage_confirmed: bool
    created_at: datetime


class ClientMappingApplyService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: ClientMappingRepository,
        movements: ClientMovementsRepository,
        decisions: ClientMappingDecisionService,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._db = db
        self._repo = repository
        self._movements = movements
        self._decisions = decisions
        # O emissor que já existe (12.2) — o default é emitir.
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    async def preview(
        self, client: Client, destination_type: str, competence: date
    ) -> MappingPreview:
        """A prévia das QUATRO situações, sobre a competência inteira, todas as contas."""
        state = await self._movements.get_sync_state(client.id, competence)
        if state.nunca_sincronizada:
            raise MovementBaseNotSyncedError(
                f"Cliente {client.id}: competência {competence} nunca sincronizada."
            )
        movements = await self._movements.list_for_competence(
            client.id, competence, status=MovementStatus.PRESENTE
        )
        if not movements:
            raise NoMovementsToMapError(
                f"Cliente {client.id}: competência {competence} sem movimento presente."
            )
        destination = await self._decisions.resolve_destination(client, destination_type)
        decisions = await self._repo.list_decisions(client.id, destination.id)
        earliest = earliest_start(decisions)
        if earliest is not None and competence < earliest:
            raise CompetenceBeforeFirstVigenciaError(
                f"Competência {competence} anterior à primeira vigência {earliest}.",
                user_message=(
                    "O de-para deste destino começa em "
                    f"{format_competence(earliest)}. Escolha essa competência ou uma "
                    "posterior."
                ),
                details={"earliestCompetence": format_competence(earliest)},
            )
        vigentes = resolve_vigentes(decisions, competence)
        codes = await self._repo.target_codes(v.target_id for v in vigentes.values() if v.target_id)
        result = apply_mapping(
            movements,
            decisions,
            competence,
            target_code_of={
                key: codes.get(v.target_id) if v.target_id else None for key, v in vigentes.items()
            },
        )
        return MappingPreview(
            destination=destination,
            result=result,
            base_state=state,
            token=result.fingerprint(destination_id=str(destination.id)),
            latest_version=await self._repo.latest_version(client.id, destination.id, competence),
        )

    async def materialize(
        self,
        client: Client,
        destination_type: str,
        competence: date,
        *,
        preview_token: str,
        confirm_partial_coverage: bool,
        author: CurrentUser,
    ) -> MaterializationOutcome:
        """Cria a versão N+1 — só se a prévia confirmada ainda for a verdade."""
        if client.closed_at is not None:
            raise ClientClosedError(f"Cliente {client.id} encerrado; materialização recusada.")
        preview = await self.preview(client, destination_type, competence)
        if preview.token != preview_token:
            raise StaleMappingPreviewError(
                f"Token da prévia não confere para {client.id}/{destination_type}/{competence}."
            )
        result = preview.result
        pending = result.totals[MaterializedSituation.SEM_DECISAO]
        partial = pending.count > 0
        if partial and not confirm_partial_coverage:
            raise PartialCoverageRequiresConfirmationError(
                f"{pending.count} movimento(s) sem decisão em {competence}.",
                details={
                    "undecidedAmount": str(pending.amount),
                    "undecidedCount": str(pending.count),
                },
            )

        version = preview.latest_version + 1
        materialization = ClientMappingMaterialization(
            client_id=client.id,
            destination_id=preview.destination.id,
            destination_type=preview.destination.destination_type,
            competence=competence,
            version=version,
            input_hash=preview.token,
            decisions_used=[
                {
                    "sourceType": d.source_type,
                    "categoryCode": d.category_code,
                    "decisionType": d.decision_type,
                    "targetCode": d.target_code,
                    "effectiveFrom": format_competence(d.effective_from),
                }
                for d in result.used_decisions
            ],
            mapped_amount=result.totals[MaterializedSituation.ALVO].amount,
            mapped_count=result.totals[MaterializedSituation.ALVO].count,
            not_mapped_amount=result.totals[MaterializedSituation.NAO_MAPEAR].amount,
            not_mapped_count=result.totals[MaterializedSituation.NAO_MAPEAR].count,
            undecided_amount=pending.amount,
            undecided_count=pending.count,
            uncategorized_amount=result.totals[MaterializedSituation.SEM_CATEGORIA].amount,
            uncategorized_count=result.totals[MaterializedSituation.SEM_CATEGORIA].count,
            undecided_categories=len(result.undecided_categories),
            partial_coverage_confirmed=partial,
            author_id=UUID(author.id),
        )
        items = [
            {
                "source_type": i.source_type,
                "source_movement_id": i.source_movement_id,
                "source_account_id": i.source_account_id,
                "movement_date": i.movement_date,
                "amount": i.amount,
                "category_code": i.category_code,
                "situation": i.situation.value,
                "target_code": i.target_code,
                "decision_effective_from": i.decision_effective_from,
            }
            for i in result.items
        ]
        try:
            await self._repo.insert_materialization(materialization, items)
        except IntegrityError as exc:
            # Outra materialização da mesma (cliente, destino, competência) pegou a
            # versão N+1 entre a prévia e agora. Nada foi gravado (SAVEPOINT).
            raise ConflictError(
                f"Versão {version} já materializada por outra requisição.",
                user_message=(
                    "Outra aplicação deste de-para acabou de ser registrada. Gere a "
                    "prévia de novo e confirme."
                ),
            ) from exc
        await self._db.refresh(materialization)
        # Barreira: a materialização é o fato; a métrica vem DEPOIS do commit dela.
        await self._db.commit()
        log.info(
            "client_mapping_materialized",
            client_id=str(client.id),
            destination=preview.destination.destination_type,
            competence=competence.isoformat(),
            version=version,
            items=len(items),
        )
        await self._usage_events.emit_depara_aplicado(
            client_id=client.id,
            destino=preview.destination.destination_type,
            valor_com_decisao=result.numerator,
            valor_nao_mapear=result.totals[MaterializedSituation.NAO_MAPEAR].amount,
            valor_sem_decisao=pending.amount,
            categorias_sem_decisao=len(result.undecided_categories),
        )
        return MaterializationOutcome(
            id=materialization.id,
            version=version,
            preview=preview,
            partial_coverage_confirmed=partial,
            created_at=materialization.created_at,
        )
