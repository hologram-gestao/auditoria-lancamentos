"""Decisões do de-para: escrita com vigência, confirmação, herança e leitura (Sprint 12, BACK 12.4).

**Vigência append-only por competência (R4).** Alterar é INSERIR uma vigência nova
com a competência de início escolhida (padrão: a corrente, calculada no SERVIDOR);
a anterior vale até o mês anterior. A única escrita não-append é a resolução de uma
HERDADA na mesma competência de início (ver `ClientMappingRepository.resolve_inherited`
e ADR-075-BE).

**Retroatividade (R4).** Início anterior à competência corrente:
  - se alguma competência afetada (do início até a corrente) já tem materialização
    no destino → 409 `COMPETENCIA_MATERIALIZADA`, nem com confirmação;
  - senão, a 1ª chamada sem `confirm_retroactive` → 409
    `RETROATIVA_REQUER_CONFIRMACAO` listando as competências afetadas; só a chamada
    com confirmação grava.

**Herança (R7).** Só no destino `demonstrativo_contabil`: categoria ATIVA do plano de
contas com `dre_code` → decisão HERDADA apontando o alvo de MESMO código. `dre_code`
nulo ("sem destino declarado") fica SEM decisão — nunca `nao_mapear`. `dre_code` sem
alvo no catálogo fica sem decisão e é REPORTADO: o sistema nunca cria alvo implícito.
Re-sincronizar o plano de contas NUNCA reescreve decisão: a leitura SINALIZA a
divergência e a pessoa decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import (
    ClientClosedError,
    MappingDecisionDuplicateError,
    MappingDestinationNotConfiguredError,
    RetroactiveMappingRequiresConfirmationError,
    RetroactiveOverMaterializedError,
    ValidationAppError,
)
from app.db.models import (
    INHERITING_DESTINATION_TYPE,
    ClientMappingDecision,
    DecisionOrigin,
    DecisionType,
    ProviderType,
)
from app.modules.client_mapping.vigencia import (
    DecisionKey,
    affected_competences,
    decision_key,
    next_start_after,
    resolve_vigente,
    resolve_vigentes,
)
from app.modules.client_movements.competence import current_competence, format_competence

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.core.authz import CurrentUser
    from app.db.models import Client
    from app.db.models.mapping_catalog import MappingDestination
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.mapping_catalog.repository import MappingCatalogRepository
    from app.modules.mapping_catalog.service import MappingCatalogService

#: O plano de contas persistido (S10) vem da origem Omie — é o tipo de origem das
#: decisões HERDADAS dele. Quando a origem por arquivo (S14) tiver plano próprio,
#: isto vira dado da linha, não constante.
CHART_SOURCE_TYPE = ProviderType.OMIE.value

#: Estados da herança devolvidos como CAMPO (não erro) — a tela explica.
INHERIT_STATE_OK = "ok"
INHERIT_STATE_NO_CHART = "sem_plano_de_contas"
INHERIT_STATE_NOT_INHERITING = "destino_sem_heranca"


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Uma decisão pedida — já validada na forma pela borda."""

    category_code: str
    decision_type: DecisionType
    target_code: str | None
    source_type: str = CHART_SOURCE_TYPE


@dataclass(frozen=True, slots=True)
class DecisionWriteResult:
    effective_from: date
    #: Vigências novas gravadas.
    created: int
    #: Herdadas resolvidas pela pessoa na mesma competência de início.
    resolved: int
    #: Pedidos idênticos à decisão CONFIRMADA já vigente — nada a gravar.
    unchanged: int

    @property
    def affected(self) -> int:
        return self.created + self.resolved


@dataclass(frozen=True, slots=True)
class InheritResult:
    state: str
    effective_from: date
    created: int = 0
    #: Já tinham decisão (qualquer vigência) — a herança é idempotente.
    already_decided: int = 0
    #: `dre_code` nulo na origem: ficam sem decisão (nunca `nao_mapear`).
    without_dre: int = 0
    #: Categorias cujo `dre_code` não existe (ou está inativo) como alvo no catálogo.
    missing_target_categories: list[str] = field(default_factory=list)
    missing_target_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DecisionView:
    """A decisão como a leitura a expõe — só códigos (§4.5)."""

    source_type: str
    category_code: str
    decision_type: str
    target_code: str | None
    origin: str
    effective_from: date
    created_at: datetime
    #: Só no destino que herda: o `dre_code` atual da origem difere do que a
    #: decisão vigente diz (R7). A pessoa decide; nada é reescrito.
    divergent: bool = False
    origin_dre_code: str | None = None


class ClientMappingDecisionService:
    def __init__(
        self,
        repository: ClientMappingRepository,
        *,
        catalog: MappingCatalogRepository,
        catalog_service: MappingCatalogService,
    ) -> None:
        self._repo = repository
        self._catalog = catalog
        self._catalog_service = catalog_service

    # ------------------------------ destino ---------------------------

    async def resolve_destination(
        self, client: Client, destination_type: str
    ) -> MappingDestination:
        """O destino do TIPO pedido na organização DO CLIENTE — ou 409 nomeando o tipo.

        A organização vem do cliente já validado pela rota, nunca do payload.
        Destino inexistente e destino desativado recebem a MESMA resposta: nos dois
        casos a organização não o oferece para classificar (R2).
        """
        destination = await self._catalog.get_destination_by_type(
            client.organization_id, destination_type
        )
        if destination is None or not destination.active:
            raise MappingDestinationNotConfiguredError(
                f"Destino {destination_type!r} ausente/inativo na org {client.organization_id}.",
                user_message=(
                    f"O destino '{destination_type}' não está configurado para a organização "
                    "deste cliente."
                ),
            )
        return destination

    # ------------------------------ escrita ---------------------------

    async def write_decisions(
        self,
        client: Client,
        destination_type: str,
        inputs: Sequence[DecisionInput],
        *,
        author: CurrentUser,
        effective_from: date | None = None,
        confirm_retroactive: bool = False,
        today: date | None = None,
    ) -> DecisionWriteResult:
        """Grava uma ou várias decisões na competência de início escolhida."""
        _ensure_open(client)
        destination = await self.resolve_destination(client, destination_type)
        current = current_competence(today)
        start = effective_from or current

        keys = [(i.source_type, i.category_code) for i in inputs]
        repeated = sorted({f"{k[1]}" for k in keys if keys.count(k) > 1})
        if repeated:
            raise ValidationAppError(
                f"Categoria repetida no lote: {repeated}",
                user_message="Cada categoria pode aparecer uma vez só no lote: "
                + ", ".join(repeated)
                + ".",
            )

        targets = await self._catalog_service.require_targets(
            destination,
            [
                i.target_code
                for i in inputs
                if i.decision_type is DecisionType.ALVO and i.target_code
            ],
        )
        existing = await self._repo.list_decisions(
            client.id, destination.id, category_codes=[i.category_code for i in inputs]
        )
        by_key = _group_by_key(existing)
        target_codes = await self._repo.target_codes(d.target_id for d in existing if d.target_id)

        to_insert: list[ClientMappingDecision] = []
        to_resolve: list[tuple[ClientMappingDecision, DecisionInput]] = []
        duplicates: list[str] = []
        unchanged = 0
        for item in inputs:
            key_rows = by_key.get((item.source_type, item.category_code), [])
            at_start = next((d for d in key_rows if d.effective_from == start), None)
            if at_start is not None:
                if at_start.origin == DecisionOrigin.CONFIRMADA.value:
                    if _same_effect(at_start, item, target_codes):
                        unchanged += 1
                    else:
                        duplicates.append(item.category_code)
                    continue
                to_resolve.append((at_start, item))
                continue
            vigente = resolve_vigente(key_rows, start)
            if (
                vigente is not None
                and vigente.origin == DecisionOrigin.CONFIRMADA.value
                and _same_effect(vigente, item, target_codes)
                and next_start_after(key_rows, start) is None
            ):
                unchanged += 1
                continue
            to_insert.append(
                ClientMappingDecision(
                    client_id=client.id,
                    source_type=item.source_type,
                    category_code=item.category_code,
                    destination_id=destination.id,
                    decision_type=item.decision_type.value,
                    target_id=targets[item.target_code].id
                    if item.decision_type is DecisionType.ALVO and item.target_code
                    else None,
                    origin=DecisionOrigin.CONFIRMADA.value,
                    effective_from=start,
                    author_id=UUID(author.id),
                )
            )

        if duplicates:
            raise _duplicate_error(start, duplicates)

        touched_keys = [decision_key(d) for d in to_insert] + [
            decision_key(row) for row, _ in to_resolve
        ]
        await self._guard_retroactivity(
            client,
            destination,
            {key: by_key.get(key, []) for key in touched_keys},
            start=start,
            current=current,
            confirm_retroactive=confirm_retroactive,
        )

        for row, item in to_resolve:
            ok = await self._repo.resolve_inherited(
                row.id,
                client_id=client.id,
                decision_type=item.decision_type.value,
                target_id=targets[item.target_code].id
                if item.decision_type is DecisionType.ALVO and item.target_code
                else None,
                author_id=UUID(author.id),
            )
            if not ok:  # pragma: no cover - corrida: outra pessoa confirmou antes
                raise MappingDecisionDuplicateError(
                    f"Herdada {row.id} deixou de ser herdada durante a escrita."
                )
        if to_insert and not await self._repo.insert_decisions(to_insert):
            # Corrida: outra requisição gravou a MESMA chave e vigência entre a leitura
            # acima e esta escrita. A UNIQUE barrou; é a mesma resposta da duplicada.
            raise _duplicate_error(start, [d.category_code for d in to_insert])
        return DecisionWriteResult(
            effective_from=start,
            created=len(to_insert),
            resolved=len(to_resolve),
            unchanged=unchanged,
        )

    async def confirm_inherited(
        self,
        client: Client,
        destination_type: str,
        *,
        author: CurrentUser,
        confirm: bool,
        effective_from: date | None = None,
        confirm_retroactive: bool = False,
        today: date | None = None,
    ) -> tuple[int, DecisionWriteResult | None]:
        """Confirma EM LOTE as herdadas vigentes do destino (R6).

        Sem `confirm=True` não grava nada: devolve só a quantidade que seria
        afetada — a confirmação explícita mostra o número antes (R6). Com ela,
        cada herdada vira decisão CONFIRMADA de mesmo efeito, pela MESMA regra de
        escrita (vigência nova, ou resolução na mesma competência de início).
        """
        _ensure_open(client)
        destination = await self.resolve_destination(client, destination_type)
        start = effective_from or current_competence(today)
        decisions = await self._repo.list_decisions(client.id, destination.id)
        vigentes = resolve_vigentes(decisions, start)
        herdadas = [d for d in vigentes.values() if d.origin == DecisionOrigin.HERDADA.value]
        if not confirm or not herdadas:
            return len(herdadas), None
        codes = await self._repo.target_codes(d.target_id for d in herdadas if d.target_id)
        inputs = [
            DecisionInput(
                category_code=d.category_code,
                decision_type=DecisionType(d.decision_type),
                target_code=codes.get(d.target_id) if d.target_id else None,
                source_type=d.source_type,
            )
            for d in herdadas
        ]
        result = await self.write_decisions(
            client,
            destination_type,
            inputs,
            author=author,
            effective_from=start,
            confirm_retroactive=confirm_retroactive,
            today=today,
        )
        return len(herdadas), result

    async def inherit(
        self,
        client: Client,
        destination_type: str,
        *,
        author: CurrentUser,
        effective_from: date | None = None,
        confirm_retroactive: bool = False,
        today: date | None = None,
    ) -> InheritResult:
        """ "Iniciar o de-para do destino" (R7) — explícita e IDEMPOTENTE."""
        _ensure_open(client)
        destination = await self.resolve_destination(client, destination_type)
        current = current_competence(today)
        start = effective_from or current
        if destination.destination_type != INHERITING_DESTINATION_TYPE:
            # Os outros quatro abrem SEM decisão: não há de onde herdar, e herdar a
            # conta gerencial para natureza fiscal seria inventar.
            return InheritResult(state=INHERIT_STATE_NOT_INHERITING, effective_from=start)

        chart = await self._repo.active_chart(client.id)
        if not chart:
            return InheritResult(state=INHERIT_STATE_NO_CHART, effective_from=start)

        existing = await self._repo.list_decisions(client.id, destination.id)
        decided = {decision_key(d) for d in existing}
        with_dre = [row for row in chart if row.dre_code]
        targets = await self._catalog.get_targets_by_codes(
            destination.id, {row.dre_code for row in with_dre if row.dre_code}
        )

        to_insert: list[ClientMappingDecision] = []
        already = 0
        missing_categories: list[str] = []
        missing_codes: set[str] = set()
        for row in with_dre:
            if (CHART_SOURCE_TYPE, row.category_code) in decided:
                already += 1
                continue
            target = targets.get(row.dre_code or "")
            if target is None or not target.active:
                missing_categories.append(row.category_code)
                missing_codes.add(row.dre_code or "")
                continue
            to_insert.append(
                ClientMappingDecision(
                    client_id=client.id,
                    source_type=CHART_SOURCE_TYPE,
                    category_code=row.category_code,
                    destination_id=destination.id,
                    decision_type=DecisionType.ALVO.value,
                    target_id=target.id,
                    origin=DecisionOrigin.HERDADA.value,
                    effective_from=start,
                    author_id=UUID(author.id),
                )
            )

        await self._guard_retroactivity(
            client,
            destination,
            {decision_key(d): [] for d in to_insert},
            start=start,
            current=current,
            confirm_retroactive=confirm_retroactive,
        )
        # ON CONFLICT DO NOTHING: uma requisição concorrente que já herdou a mesma
        # chave e vigência vira no-op — `created` é o que entrou DE FATO.
        created = await self._repo.insert_inherited(to_insert)
        return InheritResult(
            state=INHERIT_STATE_OK,
            effective_from=start,
            created=created,
            already_decided=already + len(to_insert) - created,
            without_dre=len(chart) - len(with_dre),
            missing_target_categories=missing_categories,
            missing_target_codes=sorted(missing_codes),
        )

    # ------------------------------ leitura ---------------------------

    async def vigentes(
        self, client: Client, destination: MappingDestination, competence: date
    ) -> dict[DecisionKey, DecisionView]:
        """As decisões VIGENTES na competência, por chave, com a divergência (R7)."""
        decisions = await self._repo.list_decisions(client.id, destination.id)
        vigentes = resolve_vigentes(decisions, competence)
        codes = await self._repo.target_codes(d.target_id for d in vigentes.values() if d.target_id)
        dre_codes = (
            await self._repo.chart_dre_codes(client.id)
            if destination.destination_type == INHERITING_DESTINATION_TYPE
            else {}
        )
        return {
            key: _view(decision, codes, dre_codes=dre_codes, inheriting=bool(dre_codes))
            for key, decision in vigentes.items()
        }

    async def history(
        self, client: Client, destination_type: str, *, source_type: str, category_code: str
    ) -> list[DecisionView]:
        """TODAS as vigências de uma categoria no destino, da mais antiga à mais nova."""
        destination = await self.resolve_destination(client, destination_type)
        decisions = [
            d
            for d in await self._repo.list_decisions(
                client.id, destination.id, category_codes=[category_code]
            )
            if d.source_type == source_type
        ]
        codes = await self._repo.target_codes(d.target_id for d in decisions if d.target_id)
        return [_view(d, codes, dre_codes={}, inheriting=False) for d in decisions]

    # ------------------------------ internals -------------------------

    async def _guard_retroactivity(
        self,
        client: Client,
        destination: MappingDestination,
        rows_by_key: dict[DecisionKey, list[ClientMappingDecision]],
        *,
        start: date,
        current: date,
        confirm_retroactive: bool,
    ) -> None:
        """As duas regras de retroatividade do R4, sobre TODAS as chaves tocadas."""
        if start >= current or not rows_by_key:
            return
        affected: set[date] = set()
        guarded: set[date] = set()
        for key_rows in rows_by_key.values():
            past = affected_competences(key_rows, start, current)
            affected.update(past)
            guarded.update(past)
            following = next_start_after(key_rows, start)
            if following is None or following > current:
                # A vigência nova também passa a reger a competência CORRENTE.
                guarded.add(current)
        materialized = await self._repo.materialized_competences(client.id, destination.id, guarded)
        if materialized:
            listed = ",".join(format_competence(c) for c in materialized)
            raise RetroactiveOverMaterializedError(
                f"Retroativa sobre competências materializadas: {listed}",
                user_message=(
                    "Esta alteração atingiria competências que já tiveram o de-para "
                    f"aplicado ({listed.replace(',', ', ')}). Escolha uma competência de "
                    "início posterior."
                ),
                details={"competences": listed},
            )
        if affected and not confirm_retroactive:
            listed = ",".join(format_competence(c) for c in sorted(affected))
            raise RetroactiveMappingRequiresConfirmationError(
                f"Retroativa sem confirmação: {listed}",
                user_message=(
                    "Esta alteração vale para competências passadas: "
                    f"{listed.replace(',', ', ')}. Confirme para aplicá-la a elas."
                ),
                details={"competences": listed},
            )


def _ensure_open(client: Client) -> None:
    """Escrita em cliente ENCERRADO é 409 também no serviço (a rota já usa `OpenClientDep`)."""
    if client.closed_at is not None:
        raise ClientClosedError(f"Cliente {client.id} está encerrado; de-para só-leitura.")


def _duplicate_error(start: date, category_codes: Sequence[str]) -> MappingDecisionDuplicateError:
    """409 `DECISAO_DUPLICADA` — a checagem do serviço e a UNIQUE (corrida) respondem igual."""
    codes = sorted(set(category_codes))
    return MappingDecisionDuplicateError(
        f"Decisão confirmada já existe em {start} para {codes}",
        user_message=(
            "Já existe decisão confirmada a partir de "
            f"{format_competence(start)} para: {', '.join(codes)}. "
            "Para alterar, escolha outra competência de início."
        ),
        details={"categoryCodes": ",".join(codes)},
    )


def _group_by_key(
    decisions: Sequence[ClientMappingDecision],
) -> dict[DecisionKey, list[ClientMappingDecision]]:
    grouped: dict[DecisionKey, list[ClientMappingDecision]] = {}
    for decision in decisions:
        grouped.setdefault(decision_key(decision), []).append(decision)
    return grouped


def _same_effect(
    decision: ClientMappingDecision, item: DecisionInput, target_codes: dict[UUID, str]
) -> bool:
    if decision.decision_type != item.decision_type.value:
        return False
    if item.decision_type is DecisionType.NAO_MAPEAR:
        return True
    return (
        decision.target_id is not None and target_codes.get(decision.target_id) == item.target_code
    )


def _view(
    decision: ClientMappingDecision,
    codes: dict[UUID, str],
    *,
    dre_codes: dict[str, str | None],
    inheriting: bool,
) -> DecisionView:
    target_code = codes.get(decision.target_id) if decision.target_id else None
    origin_dre: str | None = None
    divergent = False
    if inheriting and decision.category_code in dre_codes:
        origin_dre = dre_codes[decision.category_code]
        divergent = origin_dre != target_code
    return DecisionView(
        source_type=decision.source_type,
        category_code=decision.category_code,
        decision_type=decision.decision_type,
        target_code=target_code,
        origin=decision.origin,
        effective_from=decision.effective_from,
        created_at=decision.created_at,
        divergent=divergent,
        origin_dre_code=origin_dre,
    )
