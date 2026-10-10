"""Schemas do plano contábil do cliente (Sprint 16, BACK 16.1).

Request/Response separados por endpoint. O nome da conta só aparece DECIFRADO na
resposta de leitura — nunca na de importação, nem em erro, nem em log.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.client_accounting_account import (
    MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS,
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    MAX_ACCOUNTING_ACCOUNT_NAME_CHARS,
    AccountingAccountType,
)
from app.modules.client_accounting_chart.service import (
    ACCOUNTING_ACCOUNT_UNDECIPHERABLE,
    AccountPatch,
    not_postable_reason,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.db.models.client_accounting_account import ClientAccountingAccount
    from app.modules.client_accounting_chart.service import ChartImportResult, DecryptedNames

#: Situações do filtro `?status=`. `Literal` para valor fora do vocabulário virar
#: erro de forma, não uma lista vazia que a pessoa leria como "não há contas".
AccountingAccountStatusFilter = Literal["ativa", "inativa"]


class AccountingAccountResponse(BaseModel):
    """Uma conta do plano contábil, como a API a devolve."""

    id: UUID = Field(description="Identificador da conta (o alvo da decisão do de-para).")
    code: str = Field(description="Código reduzido — o que vai no arquivo contábil.")
    classification: str | None = Field(
        default=None, description="Classificação hierárquica, quando a planilha trouxe."
    )
    name: str = Field(
        description=(
            "Nome da conta, decifrado na leitura. `[indecifrável]` quando a chave do "
            "cliente não o abre (cliente encerrado) — ver `nameResolved`."
        )
    )
    name_resolved: bool = Field(
        alias="nameResolved",
        description="`false` = o nome não decifrou e `name` é o marcador `[indecifrável]`.",
    )
    account_type: AccountingAccountType = Field(
        alias="type",
        description="`analitica` recebe lançamento; `sintetica` só agrupa.",
    )
    active: bool = Field(
        description=(
            "`false` = a conta sumiu da última planilha importada. Continua existindo "
            "(decisões podem apontar para ela), mas não recebe decisão nova."
        )
    )
    postable: bool = Field(
        description=(
            "Pode receber decisão NOVA do de-para ou virar conta do banco: analítica "
            "E ativa. É o filtro do seletor de conta."
        )
    )
    updated_at: datetime = Field(alias="updatedAt", description="Última importação que a tocou.")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_row(
        cls, row: ClientAccountingAccount, *, names: DecryptedNames
    ) -> AccountingAccountResponse:
        return cls(
            id=row.id,
            code=row.code,
            classification=row.classification,
            name=names.names.get(row.id, ACCOUNTING_ACCOUNT_UNDECIPHERABLE),
            name_resolved=row.id in names.names and row.id not in names.failed,
            account_type=AccountingAccountType(row.account_type),
            active=row.active,
            postable=not_postable_reason(row) is None,
            updated_at=row.updated_at,
        )


class AccountingChartListResponse(BaseModel):
    """Body de `GET /clients/{client_id}/accounting-chart`."""

    data: list[AccountingAccountResponse]
    pagination: PaginationMeta


class ChartImportPayload(BaseModel):
    """As contagens da importação — só números, nunca código nem nome de conta."""

    contas: int = Field(ge=0, description="Contas na planilha importada.")
    contas_novas: int = Field(
        ge=0, alias="contasNovas", description="Contas que o cliente ainda não tinha."
    )
    contas_inativadas: int = Field(
        ge=0,
        alias="contasInativadas",
        description=(
            "Contas que estavam ativas e não vieram nesta planilha: passaram a inativas "
            "(nunca são apagadas)."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_result(cls, result: ChartImportResult) -> ChartImportPayload:
        return cls(
            contas=result.accounts,
            contas_novas=result.new,
            contas_inativadas=result.inactivated,
        )


class ChartImportEnvelope(BaseModel):
    """Body de `POST /clients/{client_id}/accounting-chart/import`."""

    data: ChartImportPayload


class AccountingAccountEnvelope(BaseModel):
    """Body da inclusão e da edição manual de UMA conta (86e3nb816)."""

    data: AccountingAccountResponse


class AccountingAccountCreateRequest(BaseModel):
    """Body de `POST /clients/{client_id}/accounting-chart/accounts` (86e3nb816).

    Os tetos são os das COLUNAS (§7 Backend: o limite do schema é o da coluna de
    destino), e a forma do código, o nome vazio depois de aparar e o resto da regra
    de linha vêm de `sheet.validate_account`, a MESMA da planilha (422 no campo).
    A conta nasce ativa.
    """

    code: str = Field(
        min_length=1,
        max_length=MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
        description="Código reduzido: letras, números, `.` e `-`; único no plano do cliente.",
    )
    name: str = Field(
        min_length=1,
        max_length=MAX_ACCOUNTING_ACCOUNT_NAME_CHARS,
        description="Nome da conta. Cifrado com a chave do cliente.",
    )
    account_type: AccountingAccountType = Field(
        alias="type", description="`analitica` recebe lançamento; `sintetica` só agrupa."
    )
    classification: str | None = Field(
        default=None,
        max_length=MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS,
        description="Classificação hierárquica opcional (`1.1.1.02.001`); decide a ordem.",
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AccountingAccountUpdateRequest(BaseModel):
    """Body de `PATCH /clients/{client_id}/accounting-chart/accounts/{account_id}`.

    Todo campo é opcional; o que não vem fica como está. O CÓDIGO não se edita (é a
    chave da reimportação e o que já foi para as materializações). `classification:
    null` LIMPA a classificação; ausente, mantém.
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=MAX_ACCOUNTING_ACCOUNT_NAME_CHARS
    )
    account_type: AccountingAccountType | None = Field(default=None, alias="type")
    classification: str | None = Field(
        default=None, max_length=MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS
    )
    active: bool | None = Field(
        default=None,
        description=(
            "`false` inativa a conta (ela fica, sem receber decisão nova); `true` reativa."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    def to_patch(self) -> AccountPatch:
        return AccountPatch(
            name=self.name,
            account_type=self.account_type,
            classification=self.classification,
            classification_set="classification" in self.model_fields_set,
            active=self.active,
        )
