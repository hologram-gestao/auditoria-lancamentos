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
from app.db.models.client_movement import MAX_MOVEMENT_CATEGORY_CODE_CHARS
from app.db.models.mapping_catalog import DESTINATION_TYPE_PATTERN, MAX_TARGET_CODE_CHARS
from app.modules.client_movements.competence import (
    COMPETENCE_PATTERN,
    format_competence,
    parse_competence,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.modules.client_mapping.apply import SituationTotal
    from app.modules.client_mapping.materialization import MappingPreview, MaterializationOutcome
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

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("category_code")
    @classmethod
    def _clean_category(cls, value: str) -> str:
        return _no_spaces(value)

    @model_validator(mode="after")
    def _target_matches_decision(self) -> Self:
        if self.decision == "alvo" and not self.target_code:
            raise ValueError("alvo exige targetCode")
        if self.decision == "nao_mapear" and self.target_code:
            raise ValueError("nao_mapear não aceita targetCode")
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

    model_config = ConfigDict(populate_by_name=True)


class MappingListResponse(BaseModel):
    data: list[MappingListItem]
    pagination: PaginationMeta
    competence: str = Field(description="Competência (servidor) em que a vigente foi resolvida.")


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
    preview: MappingPreviewResponse

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, outcome: MaterializationOutcome) -> MaterializationResponse:
        return cls(
            id=outcome.id,
            version=outcome.version,
            created_at=outcome.created_at,
            partial_coverage_confirmed=outcome.partial_coverage_confirmed,
            preview=MappingPreviewResponse.build(outcome.preview),
        )


class MaterializationEnvelope(BaseModel):
    data: MaterializationResponse
