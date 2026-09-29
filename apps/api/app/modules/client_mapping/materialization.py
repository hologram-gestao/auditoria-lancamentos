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
commit, emite `depara_aplicado` pelo emissor da 12.2, com os centavos do resultado —
e, desde a S14 (BACK 14.2), `fechamento_produzido` por tipo de origem materializado:
é o evento que alimenta a métrica "clientes sem ERP com fechamento na plataforma".

**Destino `conta_contabil` (S16, BACK 16.2):** a prévia traz, por categoria com alvo, a
conta do plano do cliente (código + nome resolvido na leitura), o histórico padrão
decifrado, a marca de decisão LEGADA (catálogo, precisa ser refeita) e se falta
histórico — linha sem histórico é SINALIZADA e não bloqueia a materialização nesta
sprint. O item materializado guarda o código reduzido e o id da vigência; o histórico
de uma materialização é lido por essa vigência (`materialized_lines`), nunca pela atual.

**Conta do banco (S16, BACK 16.3):** no `conta_contabil`, cada linha resolve a conta do
BANCO pela associação da conta de origem (`partida.resolve_bank_code`) e o item grava o
código dela e se a vigência tinha histórico (snapshot). Linha com ALVO sem conta do
banco → 409 `CONTA_DO_BANCO_PENDENTE` nomeando as contas de origem, nada gravado (depois
do token e antes da cobertura parcial). Os outros destinos ignoram a associação.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    BankAccountPendingError,
    ClientClosedError,
    CompetenceBeforeFirstVigenciaError,
    ConflictError,
    MovementBaseNotSyncedError,
    NoMovementsToMapError,
    PartialCoverageRequiresConfirmationError,
    StaleMappingPreviewError,
)
from app.core.logging import get_logger
from app.db.models import (
    ACCOUNTING_DESTINATION_TYPE,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    MaterializedSituation,
    MovementStatus,
)
from app.modules.client_mapping.apply import AppliedItem, ApplyResult, apply_mapping
from app.modules.client_mapping.completeness import PartidaCompleteness, partida_completeness
from app.modules.client_mapping.partida import BindingKey, pending_source_accounts
from app.modules.client_mapping.service import is_legacy_catalog_target
from app.modules.client_mapping.vigencia import DecisionKey, earliest_start, resolve_vigentes
from app.modules.client_movements.competence import format_competence
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.db.models import Client, ClientMappingDecision, User
    from app.db.models.mapping_catalog import MappingDestination
    from app.modules.client_accounting_chart.service import AccountRef
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.client_mapping.service import ClientMappingDecisionService
    from app.modules.client_movements.repository import (
        ClientMovementsRepository,
        MovementSyncState,
    )

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AccountingCategoryLine:
    """Uma categoria com ALVO no destino `conta_contabil`, como a prévia a mostra (S16).

    `history` é o texto DECIFRADO na leitura (nunca vai a log); `account` é `None` na
    decisão LEGADA (catálogo da organização, `legacy_catalog_target=True`).
    """

    source_type: str
    category_code: str
    amount: Decimal
    count: int
    decision_id: UUID
    account: AccountRef | None
    history: str | None
    legacy_catalog_target: bool
    #: S16 (16.3): Σ|valor| e quantidade das linhas com PARTIDA COMPLETA (o predicado
    #: único) e as contas de origem das linhas sem conta do banco.
    complete_amount: Decimal = Decimal("0.00")
    complete_count: int = 0
    pending_source_accounts: tuple[BindingKey, ...] = ()

    @property
    def history_missing(self) -> bool:
        return self.history is None

    def __repr__(self) -> str:
        return (
            f"<AccountingCategoryLine {self.source_type}:{self.category_code} "
            f"decision={self.decision_id} legacy={self.legacy_catalog_target}>"
        )


@dataclass(frozen=True, slots=True)
class MappingPreview:
    destination: MappingDestination
    result: ApplyResult
    base_state: MovementSyncState
    token: str
    latest_version: int
    #: S16: só no destino `conta_contabil` (nos outros, `None`).
    accounting_lines: tuple[AccountingCategoryLine, ...] | None = None
    #: S16 (16.3): no agregado, as contas de origem das linhas com alvo SEM conta do
    #: banco — com qualquer uma aqui, a materialização é 409. `None` fora do destino.
    pending_source_accounts: tuple[BindingKey, ...] | None = None
    #: S16 (16.4): a completude de partida da prévia (a MESMA função da leitura das
    #: materializações). `None` fora do `conta_contabil`.
    partida_completeness: PartidaCompleteness | None = None


@dataclass(frozen=True, slots=True)
class MaterializedLine:
    """Um item de materialização lido de volta (S16): o snapshot + o histórico DA VIGÊNCIA."""

    item: ClientMappingMaterializationItem
    history: str | None


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
        accounting = destination.destination_type == ACCOUNTING_DESTINATION_TYPE
        accounts: dict[UUID, AccountRef] = (
            await self._decisions.accounting.accounts_by_id(
                client, (v.accounting_account_id for v in vigentes.values())
            )
            if accounting
            else {}
        )
        # S16 (16.3): a conta do BANCO de cada linha, pela associação da conta de origem.
        bank_bindings = await self._decisions.accounting.bank_codes(client) if accounting else None
        result = apply_mapping(
            movements,
            decisions,
            competence,
            target_code_of={
                key: codes.get(v.target_id) if v.target_id else None for key, v in vigentes.items()
            },
            accounting_code_of=(
                {
                    key: accounts[v.accounting_account_id].code
                    for key, v in vigentes.items()
                    if v.accounting_account_id is not None and v.accounting_account_id in accounts
                }
                if accounting
                else None
            ),
            bank_bindings=bank_bindings,
            history_present_of=(
                {key: v.history_encrypted is not None for key, v in vigentes.items()}
                if accounting
                else None
            ),
        )
        return MappingPreview(
            destination=destination,
            result=result,
            base_state=state,
            token=result.fingerprint(destination_id=str(destination.id)),
            latest_version=await self._repo.latest_version(client.id, destination.id, competence),
            accounting_lines=(
                await self._accounting_lines(client, result, vigentes, accounts)
                if accounting
                else None
            ),
            pending_source_accounts=pending_source_accounts(result.items) if accounting else None,
            partida_completeness=partida_completeness(result.items) if accounting else None,
        )

    async def _accounting_lines(
        self,
        client: Client,
        result: ApplyResult,
        vigentes: dict[DecisionKey, ClientMappingDecision],
        accounts: dict[UUID, AccountRef],
    ) -> tuple[AccountingCategoryLine, ...]:
        """Por categoria com ALVO: conta, histórico, legado, pendências e partida (S16).

        A "partida completa" de cada linha é o predicado ÚNICO
        (`AppliedItem.partida_completa` → `partida.is_partida_completa`): aqui só se
        SOMA quem ele aprova — nenhum segundo cálculo de completude.
        """
        totals: dict[DecisionKey, tuple[Decimal, int]] = {}
        complete: dict[DecisionKey, tuple[Decimal, int]] = {}
        by_key: dict[DecisionKey, list[AppliedItem]] = {}
        for item in result.items:
            if item.situation is not MaterializedSituation.ALVO or item.category_code is None:
                continue
            key = (item.source_type, item.category_code)
            amount, count = totals.get(key, (Decimal("0.00"), 0))
            totals[key] = (amount + abs(item.amount), count + 1)
            by_key.setdefault(key, []).append(item)
            if item.partida_completa:
                done, done_count = complete.get(key, (Decimal("0.00"), 0))
                complete[key] = (done + abs(item.amount), done_count + 1)
        decided = [vigentes[key] for key in totals]
        histories = (await self._decisions.accounting.decrypt_histories(client, decided)).texts
        lines: list[AccountingCategoryLine] = []
        for key in sorted(totals):
            decision = vigentes[key]
            account_id = decision.accounting_account_id
            lines.append(
                AccountingCategoryLine(
                    source_type=key[0],
                    category_code=key[1],
                    amount=totals[key][0],
                    count=totals[key][1],
                    decision_id=decision.id,
                    account=accounts.get(account_id) if account_id is not None else None,
                    history=histories.get(decision.id),
                    legacy_catalog_target=is_legacy_catalog_target(decision, accounting=True),
                    complete_amount=complete.get(key, (Decimal("0.00"), 0))[0],
                    complete_count=complete.get(key, (Decimal("0.00"), 0))[1],
                    pending_source_accounts=pending_source_accounts(by_key[key]),
                )
            )
        return tuple(lines)

    async def materialized_lines(
        self, client: Client, materialization_id: UUID
    ) -> list[MaterializedLine]:
        """Os itens de UMA materialização, com o histórico lido PELA VIGÊNCIA do item.

        O snapshot (código da conta, valor, data) é o do item; o histórico vem da
        vigência que decidiu a linha (`decision_id`), imutável — mudar o histórico
        vigente, renomear ou inativar a conta depois NÃO muda o que se lê aqui.
        Cliente encerrado (decisões purgadas, DEK destruída): histórico `None` ou
        `[indecifrável]`, nunca 500. Toda query filtra `client_id`.
        """
        items = await self._repo.list_items(client.id, materialization_id)
        histories = await self._decisions.accounting.item_histories(
            client, (i.decision_id for i in items if i.accounting_account_code)
        )
        return [
            MaterializedLine(
                item=i,
                history=histories.get(i.decision_id) if i.decision_id else None,
            )
            for i in items
        ]

    async def completeness_by_materialization(
        self, client: Client, materializations: Iterable[ClientMappingMaterialization]
    ) -> dict[UUID, PartidaCompleteness]:
        """A completude de partida de cada materialização do `conta_contabil` (S16, 16.4).

        Sai do SNAPSHOT dos itens (imutável), pela função pura única — nunca de uma
        coluna calculada à parte nem de SQL paralelo. As de outros destinos ficam de
        fora (a leitura devolve `null` para elas). Uma query para o lote, com
        `client_id` no WHERE.
        """
        wanted = [
            m.id for m in materializations if m.destination_type == ACCOUNTING_DESTINATION_TYPE
        ]
        if not wanted:
            return {}
        items = await self._repo.items_for_materializations(client.id, wanted)
        by_materialization: dict[UUID, list[ClientMappingMaterializationItem]] = {
            m: [] for m in wanted
        }
        for item in items:
            by_materialization[item.materialization_id].append(item)
        return {m: partida_completeness(rows) for m, rows in by_materialization.items()}

    async def list_materializations(
        self, client: Client, destination_type: str, *, competence: date | None = None
    ) -> list[tuple[ClientMappingMaterialization, User]]:
        """As versões do destino (uma competência ou todas), com o autor (item 1)."""
        destination = await self._decisions.resolve_destination(client, destination_type)
        return await self._repo.list_materializations(
            client.id, destination.id, competence=competence
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
        # O mesmo lock transacional das escritas de decisão (follow-up 86e3f0ux7,
        # item 5): entre "a prévia ainda vale?" e o INSERT, nenhuma decisão
        # retroativa deste (cliente, destino) entra — e vice-versa.
        destination = await self._decisions.resolve_destination(client, destination_type)
        await self._repo.lock_client_destination(client.id, destination.id)
        preview = await self.preview(client, destination_type, competence)
        if preview.token != preview_token:
            raise StaleMappingPreviewError(
                f"Token da prévia não confere para {client.id}/{destination_type}/{competence}."
            )
        if preview.pending_source_accounts:
            # S16 (16.3): partida sem um dos lados não entra — nada é gravado. Só
            # identificadores em `details` (nunca nome de conta).
            raise BankAccountPendingError(
                f"{len(preview.pending_source_accounts)} conta(s) de origem sem conta do banco "
                f"em {client.id}/{competence}.",
                details={
                    "pendingSourceAccounts": [
                        {"sourceType": source_type, "sourceAccountId": source_account_id}
                        for source_type, source_account_id in preview.pending_source_accounts
                    ]
                },
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
                    # S16: a conta do plano do cliente (destino `conta_contabil`).
                    "accountingAccountCode": d.accounting_account_code,
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
                "accounting_account_code": i.accounting_account_code,
                "decision_id": i.decision_id,
                "bank_account_code": i.bank_account_code,
                "history_present": i.history_present,
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
            competencia=competence,
            # S13 (BACK 13.1): a chave que casa com `arquivo_contabil_gerado`.
            materializacao_id=materialization.id,
            valor_com_decisao=result.numerator,
            valor_nao_mapear=result.totals[MaterializedSituation.NAO_MAPEAR].amount,
            valor_sem_decisao=pending.amount,
            categorias_sem_decisao=len(result.undecided_categories),
        )
        # S14 (BACK 14.2) — a métrica da Sprint 14: UMA linha por tipo de origem
        # distinto entre os itens materializados (cliente só-arquivo → `arquivo`;
        # cliente Omie → `omie`). Também depois do commit e fail-soft: a métrica
        # nunca derruba a materialização já gravada. Ordenado para o log e o
        # teste serem determinísticos.
        for source_type in sorted({item.source_type for item in result.items}):
            await self._usage_events.emit_fechamento_produzido(
                client_id=client.id, tipo_origem=source_type, competencia=competence
            )
        return MaterializationOutcome(
            id=materialization.id,
            version=version,
            preview=preview,
            partial_coverage_confirmed=partial,
            created_at=materialization.created_at,
        )
