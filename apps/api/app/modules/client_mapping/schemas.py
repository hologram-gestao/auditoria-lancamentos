"""Contrato HTTP do de-para do cliente (Sprint 12, BACK 12.4 em diante).

Convenção de erro (23/09/2026, §4.8): forma inválida — competência fora de
`YYYY-MM`, decisão fora do vocabulário, `alvo` sem `targetCode`, `nao_mapear` com
`targetCode`, lote vazio — é **400 `VALIDATION_ERROR`** genérico do handler global,
nunca 422. O único 422 do domínio é o alvo inexistente (`ALVO_INEXISTENTE`), que é
exceção tipada porque precisa NOMEAR o alvo.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import MaterializedSituation
from app.db.models.client_mapping import MAX_DECISION_HISTORY_CHARS
from app.db.models.client_movement import MAX_MOVEMENT_CATEGORY_CODE_CHARS
from app.db.models.mapping_catalog import DESTINATION_TYPE_PATTERN, MAX_TARGET_CODE_CHARS
from app.modules.client_mapping.accounting import normalize_history
from app.modules.client_mapping.apply import _pct
from app.modules.client_movements.competence import (
    COMPETENCE_PATTERN,
    format_competence,
    parse_competence,
)
from app.modules.reconciliations.schemas import SessionAuthor
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.db.models import ClientMappingMaterialization
    from app.modules.client_mapping.apply import SituationTotal
    from app.modules.client_mapping.completeness import PartidaCompleteness
    from app.modules.client_mapping.materialization import (
        AccountingCategoryLine,
        MappingPreview,
        MaterializationOutcome,
    )
    from app.modules.client_mapping.origin_targets import OriginTargetsPreview
    from app.modules.client_mapping.portability import ImportPlan
    from app.modules.client_mapping.service import (
        DecisionView,
        DecisionWriteResult,
        InheritResult,
    )

#: Teto de decisões por lote — o mesmo do lote de alvos: um plano inteiro cabe.
MAX_DECISIONS_PER_BATCH = 500

#: Vocabulário da decisão na borda — espelho de `DecisionType` (teste trava).
DecisionTypeName = Literal["alvo", "nao_mapear"]


def _no_spaces(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or any(ch.isspace() for ch in cleaned):
        raise ValueError("código sem espaço")
    return cleaned


class _Competence(BaseModel):
    """Competência de início opcional e a confirmação de retroatividade."""

    effective_from: str | None = Field(
        default=None,
        alias="effectiveFrom",
        pattern=COMPETENCE_PATTERN,
        description=(
            "Competência de início da vigência (`YYYY-MM`). Ausente = a competência "
            "corrente do servidor."
        ),
    )
    confirm_retroactive: bool = Field(
        default=False,
        alias="confirmRetroactive",
        description=(
            "Confirmação explícita de vigência RETROATIVA. Sem ela, um início anterior "
            "à competência corrente responde 409 `RETROATIVA_REQUER_CONFIRMACAO` "
            "listando as competências afetadas em `details.competences`."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @property
    def effective_from_date(self) -> date | None:
        return parse_competence(self.effective_from) if self.effective_from else None


class DecisionItemRequest(BaseModel):
    """Uma decisão: categoria (CÓDIGO) → alvo do catálogo ou `nao_mapear`."""

    category_code: str = Field(
        alias="categoryCode", min_length=1, max_length=MAX_MOVEMENT_CATEGORY_CODE_CHARS
    )
    decision: DecisionTypeName
    target_code: str | None = Field(
        default=None, alias="targetCode", max_length=MAX_TARGET_CODE_CHARS
    )
    source_type: str = Field(
        default="omie",
        alias="sourceType",
        pattern=DESTINATION_TYPE_PATTERN,
        description="Tipo do provedor de origem da categoria (`omie`, `arquivo`…).",
    )
    accounting_account_id: UUID | None = Field(
        default=None,
        alias="accountingAccountId",
        description=(
            "S16 — SÓ no destino `conta_contabil`: a conta ANALÍTICA e ATIVA do plano "
            "contábil do próprio cliente (`GET …/accounting-chart?type=analitica&"
            "status=ativa`), no lugar de `targetCode`. Conta de outro cliente: 404; "
            "sintética ou inativa: 422 `CONTA_CONTABIL_NAO_LANCAVEL`; `targetCode` no "
            "`conta_contabil`: 422 `ALVO_EXIGE_PLANO_CONTABIL`; conta do plano em outro "
            "destino: 422 `CONTA_CONTABIL_FORA_DO_DESTINO`."
        ),
    )
    history: str | None = Field(
        default=None,
        description=(
            "S16 — histórico padrão da linha no arquivo contábil (texto fixo por decisão), "
            f"até {MAX_DECISION_HISTORY_CHARS} caracteres depois de aparar as pontas; vazio "
            "= sem histórico. Só acompanha `accountingAccountId`. Cifrado com a chave do "
            "cliente; mudar o texto é vigência NOVA. Acima do limite: 400."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("category_code")
    @classmethod
    def _clean_category(cls, value: str) -> str:
        return _no_spaces(value)

    @field_validator("history")
    @classmethod
    def _clean_history(cls, value: str | None) -> str | None:
        # Forma: 400 genérico (o handler não ecoa o valor). Nunca truncado.
        cleaned = normalize_history(value)
        if cleaned is not None and len(cleaned) > MAX_DECISION_HISTORY_CHARS:
            raise ValueError("histórico acima do limite")
        return cleaned

    @model_validator(mode="after")
    def _target_matches_decision(self) -> Self:
        has_catalog = bool(self.target_code)
        has_account = self.accounting_account_id is not None
        if self.decision == "alvo" and has_catalog == has_account:
            raise ValueError("alvo exige targetCode OU accountingAccountId (um dos dois)")
        if self.decision == "nao_mapear" and (has_catalog or has_account):
            raise ValueError("nao_mapear não aceita alvo")
        if self.history is not None and not has_account:
            raise ValueError("history só acompanha accountingAccountId")
        return self


class DecisionWriteRequest(DecisionItemRequest, _Competence):
    """Corpo de `POST …/decisions` — UMA decisão."""


class DecisionBatchRequest(_Competence):
    """Corpo de `POST …/decisions/batch` — várias decisões, mesma vigência, atômico."""

    decisions: list[DecisionItemRequest] = Field(min_length=1, max_length=MAX_DECISIONS_PER_BATCH)


class ConfirmInheritedRequest(_Competence):
    """Corpo de `POST …/decisions/confirm-inherited`."""

    confirm: bool = Field(
        default=False,
        description=(
            "Sem `true` nada é gravado: a resposta traz só `affected`, a quantidade de "
            "decisões HERDADAS que a confirmação atingiria."
        ),
    )
    code: str | None = Field(
        default=None,
        max_length=MAX_MOVEMENT_CATEGORY_CODE_CHARS,
        description=(
            "Recorte por CÓDIGO da categoria (começando por), o mesmo filtro da lista: "
            "confirma só as herdadas que casam. Ausente = todas as herdadas do destino. "
            "`affected` respeita o recorte."
        ),
    )

    @field_validator("code")
    @classmethod
    def _clean_code(cls, value: str | None) -> str | None:
        cleaned = _no_spaces(value) if value is not None else None
        return cleaned or None


class InheritRequest(_Competence):
    """Corpo de `POST …/inherit` — iniciar o de-para do destino."""


class DecisionWriteResponse(BaseModel):
    effective_from: str = Field(alias="effectiveFrom")
    created: int = Field(ge=0, description="Vigências novas gravadas.")
    resolved: int = Field(
        ge=0,
        description="Decisões HERDADAS resolvidas pela pessoa na mesma competência de início.",
    )
    unchanged: int = Field(ge=0, description="Idênticas à decisão confirmada vigente.")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, result: DecisionWriteResult) -> DecisionWriteResponse:
        return cls(
            effective_from=format_competence(result.effective_from),
            created=result.created,
            resolved=result.resolved,
            unchanged=result.unchanged,
        )


class DecisionWriteEnvelope(BaseModel):
    data: DecisionWriteResponse


class ConfirmInheritedResponse(BaseModel):
    affected: int = Field(ge=0, description="Herdadas vigentes que a confirmação atinge.")
    applied: bool = Field(description="`false` = só contagem; nada gravado.")
    result: DecisionWriteResponse | None = None

    model_config = ConfigDict(populate_by_name=True)


class ConfirmInheritedEnvelope(BaseModel):
    data: ConfirmInheritedResponse


class InheritResponse(BaseModel):
    state: Literal["ok", "sem_plano_de_contas", "destino_sem_heranca"] = Field(
        description=(
            "`ok` = herança aplicada; `sem_plano_de_contas` = o cliente não tem plano "
            "de contas sincronizado (nada a herdar, sem erro); `destino_sem_heranca` = "
            "só o `demonstrativo_contabil` herda — este destino abre sem decisão."
        )
    )
    effective_from: str = Field(alias="effectiveFrom")
    created: int = Field(ge=0)
    already_decided: int = Field(ge=0, alias="alreadyDecided")
    without_dre: int = Field(
        ge=0,
        alias="withoutDre",
        description="Categorias 'sem destino declarado' na origem: ficam SEM decisão.",
    )
    missing_target_categories: list[str] = Field(alias="missingTargetCategories")
    missing_target_codes: list[str] = Field(
        alias="missingTargetCodes",
        description="Contas de demonstrativo sem alvo no catálogo — nunca criadas implicitamente.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, result: InheritResult) -> InheritResponse:
        return cls(
            state=result.state,
            effective_from=format_competence(result.effective_from),
            created=result.created,
            already_decided=result.already_decided,
            without_dre=result.without_dre,
            missing_target_categories=list(result.missing_target_categories),
            missing_target_codes=list(result.missing_target_codes),
        )


class InheritEnvelope(BaseModel):
    data: InheritResponse


class OriginTargetCandidateResponse(BaseModel):
    """Uma conta de demonstrativo declarada pela origem, e o que o catálogo já tem dela."""

    code: str
    name: str | None = Field(
        description=(
            "Nome do alvo existente ou, se não existe, o `descricaoDRE` da origem "
            "resolvido agora. `null` = a origem não respondeu (nunca persistido aqui)."
        )
    )
    categories: int = Field(ge=0, description="Categorias ativas do cliente com esta conta.")
    exists: bool = Field(description="O catálogo do destino já tem um alvo com este código.")
    active: bool | None = Field(description="Situação do alvo existente; `null` se não existe.")
    creatable: bool = Field(
        description=(
            "Pode entrar no lote de criação: não existe, tem nome e cabe nas colunas do catálogo."
        )
    )


class OriginTargetsPreviewResponse(BaseModel):
    state: Literal["ok", "sem_plano_de_contas", "destino_sem_heranca"] = Field(
        description=(
            "`ok` = prévia montada (pode vir sem candidato, se nenhuma categoria declara "
            "conta de demonstrativo); `sem_plano_de_contas` = o cliente não tem plano de "
            "contas sincronizado; `destino_sem_heranca` = só o `demonstrativo_contabil` "
            "tem alvos na origem."
        )
    )
    destination_id: UUID | None = Field(
        alias="destinationId",
        description="O destino do catálogo onde os alvos confirmados são criados (lote).",
    )
    names_resolved: bool = Field(
        alias="namesResolved",
        description="`false` = alguma conta sem nome: a origem não respondeu agora.",
    )
    candidates: list[OriginTargetCandidateResponse]

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, preview: OriginTargetsPreview) -> OriginTargetsPreviewResponse:
        return cls(
            state=preview.state,
            destination_id=preview.destination_id,
            names_resolved=preview.names_resolved,
            candidates=[
                OriginTargetCandidateResponse(
                    code=c.code,
                    name=c.name,
                    categories=c.categories,
                    exists=c.exists,
                    active=c.active,
                    creatable=c.creatable,
                )
                for c in preview.candidates
            ],
        )


class OriginTargetsPreviewEnvelope(BaseModel):
    data: OriginTargetsPreviewResponse


class DecisionViewResponse(BaseModel):
    source_type: str = Field(alias="sourceType")
    category_code: str = Field(alias="categoryCode")
    decision: DecisionTypeName
    target_code: str | None = Field(default=None, alias="targetCode")
    origin: Literal["herdada", "confirmada"]
    effective_from: str = Field(alias="effectiveFrom")
    divergent: bool = Field(
        description=(
            "Só no destino que herda: a conta de demonstrativo ATUAL da origem difere "
            "da decisão vigente (re-sincronização). Nada é reescrito — a pessoa decide."
        )
    )
    origin_dre_code: str | None = Field(default=None, alias="originDreCode")
    accounting_account_id: UUID | None = Field(
        default=None,
        alias="accountingAccountId",
        description="S16 — `conta_contabil`: a conta do plano do cliente da vigência.",
    )
    history: str | None = Field(
        default=None,
        description=(
            "S16 — `conta_contabil`: o histórico padrão da vigência, decifrado na leitura "
            "(`null` = sem histórico; `[indecifrável]` = a chave do cliente não o abre)."
        ),
    )
    requires_redo: bool = Field(
        default=False,
        alias="requiresRedo",
        description=(
            "S16 — decisão de alvo em `conta_contabil` apontando o CATÁLOGO da organização "
            "(anterior ao plano do cliente). Segue legível e sem conversão automática; "
            "precisa ser REFEITA escolhendo uma conta do plano do cliente."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, view: DecisionView) -> DecisionViewResponse:
        return cls(
            source_type=view.source_type,
            category_code=view.category_code,
            decision=view.decision_type,
            target_code=view.target_code,
            origin=view.origin,
            effective_from=format_competence(view.effective_from),
            divergent=view.divergent,
            origin_dre_code=view.origin_dre_code,
            accounting_account_id=view.accounting_account_id,
            history=view.history,
            requires_redo=view.legacy_catalog_target,
        )


class DecisionHistoryResponse(BaseModel):
    data: list[DecisionViewResponse]


# ---------------------------------------------------------------------------
# Leitura por destino e portabilidade (BACK 12.5)
# ---------------------------------------------------------------------------

#: Filtro de situação da lista — espelho de `listing.MappingSituation`.
MappingSituationName = Literal["herdada", "confirmada", "nao_mapear", "sem_decisao"]


class MappingListItem(BaseModel):
    """Uma categoria do universo com a decisão vigente — códigos + nomes de runtime."""

    source_type: str = Field(alias="sourceType")
    category_code: str = Field(alias="categoryCode")
    category_name: str | None = Field(
        default=None,
        alias="categoryName",
        description="Resolvido em runtime (nunca persistido, nunca buscável).",
    )
    category_name_resolved: bool = Field(
        alias="categoryNameResolved",
        description="`false` = a origem não respondeu (ou a categoria não é do plano); "
        "a tela mostra o CÓDIGO.",
    )
    situation: MappingSituationName
    decision: DecisionTypeName | None = None
    target_code: str | None = Field(default=None, alias="targetCode")
    target_name: str | None = Field(default=None, alias="targetName")
    effective_from: str | None = Field(default=None, alias="effectiveFrom")
    divergent: bool
    origin_dre_code: str | None = Field(default=None, alias="originDreCode")
    accounting_account_id: UUID | None = Field(
        default=None,
        alias="accountingAccountId",
        description="S16 — `conta_contabil`: a conta do plano do cliente da vigente.",
    )
    accounting_account_code: str | None = Field(
        default=None, alias="accountingAccountCode", description="S16 — código reduzido."
    )
    accounting_account_name: str | None = Field(
        default=None,
        alias="accountingAccountName",
        description="S16 — nome da conta, decifrado na leitura (nunca persistido em claro).",
    )
    history: str | None = Field(
        default=None,
        description="S16 — histórico padrão da vigente, decifrado (`null` = sem histórico).",
    )
    requires_redo: bool = Field(
        default=False,
        alias="requiresRedo",
        description=(
            "S16 — decisão legada do catálogo da organização em `conta_contabil`: refazer "
            "escolhendo uma conta do plano do cliente."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)


class MappingSituationCountsResponse(BaseModel):
    """As quatro situações sobre o universo INTEIRO do destino (86e3f55bd).

    Independem de `page`, `situation` e `code`: `total` = soma das quatro.
    """

    total: int
    herdada: int
    confirmada: int
    nao_mapear: int = Field(alias="naoMapear")
    sem_decisao: int = Field(alias="semDecisao")

    model_config = ConfigDict(populate_by_name=True)


class MappingListResponse(BaseModel):
    data: list[MappingListItem]
    pagination: PaginationMeta
    competence: str = Field(description="Competência (servidor) em que a vigente foi resolvida.")
    counts: MappingSituationCountsResponse = Field(
        description=(
            "Contagem por situação sobre o universo inteiro do destino na competência "
            "corrente — a mesma em qualquer página e filtro."
        )
    )


class ImportRejectedLine(BaseModel):
    line: int = Field(description="Número da linha na planilha (1 = cabeçalho).")
    category_code: str = Field(alias="categoryCode")
    target_code: str | None = Field(default=None, alias="targetCode")
    reason: Literal[
        "categoria_inexistente",
        "alvo_inexistente",
        "decisao_invalida",
        "alvo_ausente",
        "alvo_nao_permitido",
        "destino_diferente",
        "linha_repetida",
        "conflito_na_vigencia",
        "historico_muito_longo",
    ]

    model_config = ConfigDict(populate_by_name=True)


class ImportPreviewResponse(BaseModel):
    effective_from: str = Field(alias="effectiveFrom")
    created: int = Field(ge=0, description="Categorias sem decisão que passam a ter.")
    altered: int = Field(ge=0, description="Decisões que mudam (vigência nova).")
    alters_confirmed: int = Field(
        ge=0,
        alias="altersConfirmed",
        description="Das alteradas, as que mudam decisão CONFIRMADA por pessoa.",
    )
    ignored: int = Field(ge=0, description="Iguais à vigente confirmada, ou sem decisão.")
    rejected: list[ImportRejectedLine]

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, plan: ImportPlan) -> ImportPreviewResponse:
        return cls(
            effective_from=format_competence(plan.effective_from),
            created=len(plan.created),
            altered=len(plan.altered),
            alters_confirmed=plan.alters_confirmed,
            ignored=plan.ignored,
            rejected=[
                ImportRejectedLine(
                    line=r.line,
                    category_code=r.category_code,
                    target_code=r.target_code,
                    reason=r.reason,
                )
                for r in plan.rejected
            ],
        )


class ImportPreviewEnvelope(BaseModel):
    data: ImportPreviewResponse


class ImportApplyResponse(BaseModel):
    preview: ImportPreviewResponse
    result: DecisionWriteResponse | None = None


class ImportApplyEnvelope(BaseModel):
    data: ImportApplyResponse


# ---------------------------------------------------------------------------
# Prévia e materialização (BACK 12.6)
# ---------------------------------------------------------------------------


class SituationTotalResponse(BaseModel):
    amount: Decimal = Field(description="Σ|valor| dos movimentos na situação (BRL).")
    count: int = Field(ge=0)

    @classmethod
    def build(cls, total: SituationTotal) -> SituationTotalResponse:
        return cls(amount=total.amount, count=total.count)


class SituationTotalsResponse(BaseModel):
    """As QUATRO situações da prévia (R5)."""

    alvo: SituationTotalResponse
    nao_mapear: SituationTotalResponse = Field(alias="naoMapear")
    sem_decisao: SituationTotalResponse = Field(alias="semDecisao")
    sem_categoria: SituationTotalResponse = Field(
        alias="semCategoria",
        description="Movimentos sem categoria de origem: FORA do denominador da cobertura.",
    )

    model_config = ConfigDict(populate_by_name=True)


class UndecidedCategoryResponse(BaseModel):
    source_type: str = Field(alias="sourceType")
    category_code: str = Field(alias="categoryCode")
    amount: Decimal
    count: int

    model_config = ConfigDict(populate_by_name=True)


class PartidaCompletenessResponse(BaseModel):
    """S16 (16.4) — a completude de partida: a métrica da sprint, por materialização.

    Σ|valor| das linhas com PARTIDA COMPLETA (conta do plano + conta do banco +
    histórico) ÷ Σ|valor| das linhas com alvo. Os três números vêm juntos para a conta
    ser conferível. `pct` nulo = nenhuma linha com alvo (nunca "0%" com cara de
    resultado).
    """

    complete_amount: Decimal = Field(alias="completeAmount")
    target_amount: Decimal = Field(alias="targetAmount")
    pct: Decimal | None = Field(
        default=None, description="Percentual quantizado a 0,01; `null` sem linha com alvo."
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def of(cls, value: PartidaCompleteness | None) -> PartidaCompletenessResponse | None:
        if value is None:
            return None
        return cls(
            complete_amount=value.complete_amount,
            target_amount=value.target_amount,
            pct=value.pct,
        )


class PendingSourceAccountResponse(BaseModel):
    """S16 (16.3) — uma conta de origem SEM conta do banco (só identificadores)."""

    source_type: str = Field(alias="sourceType")
    source_account_id: str | None = Field(
        default=None,
        alias="sourceAccountId",
        description="`null` = o slot da CONTA PADRÃO (linhas sem conta de origem).",
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def of(cls, keys: Iterable[tuple[str, str | None]]) -> list[PendingSourceAccountResponse]:
        return [cls(source_type=t, source_account_id=a) for t, a in keys]


class AccountingCategoryResponse(BaseModel):
    """S16 — uma categoria com ALVO no destino `conta_contabil`, na prévia."""

    source_type: str = Field(alias="sourceType")
    category_code: str = Field(alias="categoryCode")
    category_name: str | None = Field(
        default=None,
        alias="categoryName",
        description=(
            "86e3fxqqh — nome da categoria, pela MESMA resolução da lista de "
            "decisões (`null` = origem fora do ar ou sem decifrar; a tela mostra só "
            "o código)."
        ),
    )
    category_name_resolved: bool = Field(
        default=False,
        alias="categoryNameResolved",
        description="`false` com `categoryName` nulo OU com o marcador `[indecifrável]`.",
    )
    amount: Decimal = Field(description="Σ|valor| dos movimentos da categoria (BRL).")
    count: int = Field(ge=0)
    accounting_account_id: UUID | None = Field(default=None, alias="accountingAccountId")
    accounting_account_code: str | None = Field(
        default=None, alias="accountingAccountCode", description="Código reduzido da conta."
    )
    accounting_account_name: str | None = Field(
        default=None,
        alias="accountingAccountName",
        description="Nome da conta, decifrado na leitura (nunca guardado na materialização).",
    )
    history: str | None = Field(
        default=None, description="Histórico padrão decifrado (`null` = sem histórico)."
    )
    history_missing: bool = Field(
        alias="historyMissing",
        description=(
            "A decisão não tem histórico: a linha fica INCOMPLETA (sinalizada; nesta sprint "
            "não bloqueia a materialização — quem bloqueia o arquivo é a Sprint 13)."
        ),
    )
    requires_redo: bool = Field(
        alias="requiresRedo",
        description=(
            "Decisão LEGADA apontando o catálogo da organização: conta como incompleta e "
            "precisa ser refeita para o plano do cliente."
        ),
    )
    complete_amount: Decimal = Field(
        alias="completeAmount",
        description=(
            "S16 (16.3) — Σ|valor| das linhas da categoria com PARTIDA COMPLETA: conta do "
            "plano, conta do banco resolvida e histórico presente (um predicado só)."
        ),
    )
    complete_count: int = Field(ge=0, alias="completeCount")
    pending_source_accounts: list[PendingSourceAccountResponse] = Field(
        alias="pendingSourceAccounts",
        description="S16 (16.3) — contas de origem das linhas da categoria sem conta do banco.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, line: AccountingCategoryLine) -> AccountingCategoryResponse:
        account = line.account
        return cls(
            source_type=line.source_type,
            category_code=line.category_code,
            category_name=line.category_name,
            category_name_resolved=line.category_name_resolved,
            amount=line.amount,
            count=line.count,
            accounting_account_id=account.id if account else None,
            accounting_account_code=account.code if account else None,
            accounting_account_name=account.name if account else None,
            history=line.history,
            history_missing=line.history_missing,
            requires_redo=line.legacy_catalog_target,
            complete_amount=line.complete_amount,
            complete_count=line.complete_count,
            pending_source_accounts=PendingSourceAccountResponse.of(line.pending_source_accounts),
        )


class BaseStateResponse(BaseModel):
    synced_at: datetime | None = Field(default=None, alias="syncedAt")
    sync_failed_at: datetime | None = Field(default=None, alias="syncFailedAt")

    model_config = ConfigDict(populate_by_name=True)


class MappingPreviewResponse(BaseModel):
    competence: str
    destination: str = Field(description="Tipo do destino.")
    base_state: BaseStateResponse = Field(alias="baseState")
    situations: SituationTotalsResponse
    coverage_pct: Decimal | None = Field(
        default=None,
        alias="coveragePct",
        description=(
            "Σ|valor| com decisão (alvo + nao_mapear) ÷ Σ|valor| com categoria, em %. "
            "`null` quando o denominador é zero (nenhum movimento com categoria)."
        ),
    )
    nao_mapear_pct: Decimal | None = Field(
        default=None,
        alias="naoMapearPct",
        description="A CONTRA-MÉTRICA: Σ|valor| nao_mapear ÷ o mesmo denominador, em %.",
    )
    coverage_numerator: Decimal = Field(alias="coverageNumerator")
    coverage_denominator: Decimal = Field(alias="coverageDenominator")
    undecided_categories: list[UndecidedCategoryResponse] = Field(
        alias="undecidedCategories",
        description="Categorias sem decisão, por |valor| DECRESCENTE.",
    )
    preview_token: str = Field(
        alias="previewToken",
        description="Enviar na materialização: prova que ela é ESTA prévia.",
    )
    latest_version: int = Field(
        alias="latestVersion", description="Última versão materializada (0 = nenhuma)."
    )
    partida_completeness: PartidaCompletenessResponse | None = Field(
        default=None,
        alias="partidaCompleteness",
        description=(
            "S16 (16.4) — SÓ no `conta_contabil` (`null` nos outros): a completude de "
            "partida desta prévia — a MESMA conta que a materialização guardará."
        ),
    )
    pending_source_accounts: list[PendingSourceAccountResponse] | None = Field(
        default=None,
        alias="pendingSourceAccounts",
        description=(
            "S16 (16.3) — SÓ no `conta_contabil` (`null` nos outros): as contas de origem "
            "das linhas com alvo SEM conta contábil do banco. Com qualquer uma, a "
            "materialização é 409 `CONTA_DO_BANCO_PENDENTE` — associe em "
            "`/clients/{id}/source-accounts`."
        ),
    )
    accounting_categories: list[AccountingCategoryResponse] | None = Field(
        default=None,
        alias="accountingCategories",
        description=(
            "S16 — SÓ no destino `conta_contabil` (`null` nos outros): por categoria com "
            "alvo, a conta do plano do cliente (código e nome), o histórico padrão, se "
            "falta histórico e se a decisão é legada do catálogo."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, preview: MappingPreview) -> MappingPreviewResponse:
        result = preview.result
        totals = result.totals
        return cls(
            competence=format_competence(result.competence),
            destination=preview.destination.destination_type,
            base_state=BaseStateResponse(
                synced_at=preview.base_state.synced_at,
                sync_failed_at=preview.base_state.sync_failed_at,
            ),
            situations=SituationTotalsResponse(
                alvo=SituationTotalResponse.build(totals[MaterializedSituation.ALVO]),
                nao_mapear=SituationTotalResponse.build(totals[MaterializedSituation.NAO_MAPEAR]),
                sem_decisao=SituationTotalResponse.build(totals[MaterializedSituation.SEM_DECISAO]),
                sem_categoria=SituationTotalResponse.build(
                    totals[MaterializedSituation.SEM_CATEGORIA]
                ),
            ),
            coverage_pct=result.coverage_pct,
            nao_mapear_pct=result.nao_mapear_pct,
            coverage_numerator=result.numerator,
            coverage_denominator=result.denominator,
            undecided_categories=[
                UndecidedCategoryResponse(
                    source_type=u.source_type,
                    category_code=u.category_code,
                    amount=u.amount,
                    count=u.count,
                )
                for u in result.undecided_categories
            ],
            preview_token=preview.token,
            latest_version=preview.latest_version,
            accounting_categories=(
                [AccountingCategoryResponse.build(line) for line in preview.accounting_lines]
                if preview.accounting_lines is not None
                else None
            ),
            pending_source_accounts=(
                PendingSourceAccountResponse.of(preview.pending_source_accounts)
                if preview.pending_source_accounts is not None
                else None
            ),
            partida_completeness=PartidaCompletenessResponse.of(preview.partida_completeness),
        )


class MappingPreviewEnvelope(BaseModel):
    data: MappingPreviewResponse


class MaterializationRequest(BaseModel):
    """Corpo de `POST …/materializations`."""

    competence: str = Field(pattern=COMPETENCE_PATTERN, description="`YYYY-MM`.")
    preview_token: str = Field(
        alias="previewToken",
        min_length=64,
        max_length=64,
        description="O `previewToken` da prévia que a pessoa confirmou.",
    )
    confirm_partial_coverage: bool = Field(
        default=False,
        alias="confirmPartialCoverage",
        description=(
            "Obrigatório quando há movimento SEM decisão: confirma aplicar com cobertura "
            "parcial (fica registrado na materialização)."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @property
    def competence_date(self) -> date:
        return parse_competence(self.competence)


class MaterializationResponse(BaseModel):
    id: UUID
    version: int
    created_at: datetime = Field(alias="createdAt")
    partial_coverage_confirmed: bool = Field(alias="partialCoverageConfirmed")
    partida_completeness: PartidaCompletenessResponse | None = Field(
        default=None,
        alias="partidaCompleteness",
        description=(
            "S16 (16.4) — a completude de partida da versão gravada (só `conta_contabil`; "
            "`null` nos outros). O snapshot é imutável: o número nunca muda."
        ),
    )
    preview: MappingPreviewResponse

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, outcome: MaterializationOutcome) -> MaterializationResponse:
        return cls(
            id=outcome.id,
            version=outcome.version,
            created_at=outcome.created_at,
            partial_coverage_confirmed=outcome.partial_coverage_confirmed,
            partida_completeness=PartidaCompletenessResponse.of(
                outcome.preview.partida_completeness
            ),
            preview=MappingPreviewResponse.build(outcome.preview),
        )


class MaterializationEnvelope(BaseModel):
    data: MaterializationResponse


class MaterializationSummaryResponse(BaseModel):
    """Uma versão materializada, como a lista de versões da tela precisa.

    Follow-up 86e3f0ux7 (item 1): até aqui a tela derivava "Versão 1..N" de
    `latestVersion`, sem data, autor nem cobertura. Só o CABEÇALHO da versão
    (`client_mapping_materializations`); os itens são a leitura da Sprint 13. Os
    valores são Σ|valor| por situação, os mesmos da prévia, e a cobertura sai da
    MESMA função da prévia (`coverage_pct`), para as duas nunca divergirem. O autor
    passa por `author_for_viewer` na rota (§3.15): usuário de tenant vendo autor da
    equipe recebe "Equipe {org}" sem e-mail.
    """

    id: UUID
    competence: str = Field(description="`YYYY-MM`.")
    version: int
    created_at: datetime = Field(alias="createdAt")
    author: SessionAuthor
    partial_coverage_confirmed: bool = Field(alias="partialCoverageConfirmed")
    coverage_pct: Decimal | None = Field(
        alias="coveragePct",
        description=(
            "Σ|valor| com decisão (alvo + `nao_mapear`) ÷ Σ|valor| com categoria, em %, "
            "como na prévia. `null` quando não havia valor com categoria."
        ),
    )
    mapped_amount: Decimal = Field(alias="mappedAmount")
    mapped_count: int = Field(alias="mappedCount")
    not_mapped_amount: Decimal = Field(alias="notMappedAmount")
    not_mapped_count: int = Field(alias="notMappedCount")
    undecided_amount: Decimal = Field(alias="undecidedAmount")
    undecided_count: int = Field(alias="undecidedCount")
    uncategorized_amount: Decimal = Field(alias="uncategorizedAmount")
    uncategorized_count: int = Field(alias="uncategorizedCount")
    undecided_categories: int = Field(alias="undecidedCategories")
    decisions_used: int = Field(
        alias="decisionsUsed", description="Quantas vigências a versão usou (snapshot)."
    )
    partida_completeness: PartidaCompletenessResponse | None = Field(
        default=None,
        alias="partidaCompleteness",
        description=(
            "S16 (16.4) — completude de partida da versão, calculada sobre o SNAPSHOT dos "
            "itens (imutável). Só no `conta_contabil`; `null` nos outros destinos."
        ),
    )
    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(
        cls,
        row: ClientMappingMaterialization,
        author: SessionAuthor,
        completeness: PartidaCompleteness | None = None,
    ) -> MaterializationSummaryResponse:
        decided = row.mapped_amount + row.not_mapped_amount
        return cls(
            id=row.id,
            competence=format_competence(row.competence),
            version=row.version,
            created_at=row.created_at,
            author=author,
            partial_coverage_confirmed=row.partial_coverage_confirmed,
            coverage_pct=_pct(decided, decided + row.undecided_amount),
            mapped_amount=row.mapped_amount,
            mapped_count=row.mapped_count,
            not_mapped_amount=row.not_mapped_amount,
            not_mapped_count=row.not_mapped_count,
            undecided_amount=row.undecided_amount,
            undecided_count=row.undecided_count,
            uncategorized_amount=row.uncategorized_amount,
            uncategorized_count=row.uncategorized_count,
            undecided_categories=row.undecided_categories,
            decisions_used=len(row.decisions_used),
            partida_completeness=PartidaCompletenessResponse.of(completeness),
        )


class MaterializationListEnvelope(BaseModel):
    data: list[MaterializationSummaryResponse]
