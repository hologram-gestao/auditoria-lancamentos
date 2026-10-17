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
    AccountingPartyType,
)
from app.modules.client_accounting_chart.parties import PartyLineProblem
from app.modules.client_accounting_chart.service import (
    ACCOUNTING_ACCOUNT_UNDECIPHERABLE,
    AccountPatch,
    not_postable_reason,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.db.models.client_accounting_account import ClientAccountingAccount
    from app.modules.client_accounting_chart.service import (
        ChartImportResult,
        ChartPreview,
        DecryptedNames,
        PartyDocuments,
        PartyImportResult,
        PartyLinksSummary,
    )

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
            "`false` = a conta foi inativada (pela edição; a reimportação não inativa). "
            "Continua existindo (decisões podem apontar para ela), mas não recebe "
            "decisão nova."
        )
    )
    absent_since: datetime | None = Field(
        default=None,
        alias="absentSince",
        description=(
            'O selo "não veio na última importação": o instante da importação em '
            "que a conta deixou de vir na planilha (a primeira, enquanto ela segue "
            "ausente). `null` = veio na última. A conta continua como estava; o selo "
            "avisa que ela pode não existir mais no sistema contábil."
        ),
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
            absent_since=row.absent_since,
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
    contas_alteradas: int = Field(
        ge=0,
        alias="contasAlteradas",
        description="Contas existentes em que nome, tipo ou classificação mudaram.",
    )
    contas_ausentes: int = Field(
        ge=0,
        alias="contasAusentes",
        description=(
            "Contas do cliente que não vieram nesta planilha: continuam como estavam "
            "(nunca são inativadas nem apagadas) e ganham o selo `absentSince`."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_result(cls, result: ChartImportResult) -> ChartImportPayload:
        return cls(
            contas=result.accounts,
            contas_novas=result.new,
            contas_alteradas=result.updated,
            contas_ausentes=result.absent,
        )


#: Teto de cada LISTA da prévia (as contagens são sempre as reais). Um plano real
#: tem centenas de contas; a primeira importação lista todas como novas, e passar
#: disso não ajuda ninguém a decidir.
CHART_PREVIEW_LIST_LIMIT = 200

#: O que a planilha muda numa conta existente — o vocabulário de `diff.ChartRowChange`.
ChartRowChangeField = Literal["nome", "tipo", "classificacao"]


class ChartPreviewNewAccount(BaseModel):
    """Uma conta que a importação incluiria (como veio na planilha)."""

    code: str
    name: str
    account_type: AccountingAccountType = Field(alias="type")
    classification: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ChartPreviewChangedAccount(BaseModel):
    """Uma conta existente que a importação alteraria: o nome NOVO e o que muda."""

    account_id: UUID = Field(alias="accountId")
    code: str
    name: str = Field(description="O nome como vem na planilha.")
    changes: list[ChartRowChangeField]

    model_config = ConfigDict(populate_by_name=True)


class ChartPreviewAbsentAccount(BaseModel):
    """Uma conta do cliente que não veio na planilha: fica como está, com o selo."""

    account_id: UUID = Field(alias="accountId")
    code: str
    name: str = Field(description="O nome atual, decifrado; `[indecifrável]` se não abrir.")
    name_resolved: bool = Field(alias="nameResolved")
    active: bool
    in_use: bool = Field(
        alias="inUse",
        description=(
            "Decisões do de-para (de qualquer vigência) ou a conta do banco usam a conta: "
            "se ela não existe mais no sistema contábil, o arquivo contábil levaria uma "
            "conta inexistente."
        ),
    )
    absent_since: datetime | None = Field(
        default=None,
        alias="absentSince",
        description="Já estava ausente desde esta importação; `null` = ganha o selo agora.",
    )

    model_config = ConfigDict(populate_by_name=True)


class ChartImportPreviewPayload(BaseModel):
    """O que a importação desta planilha faria — nada é gravado."""

    layout: Literal["modelo", "dominio"] = Field(
        description="`modelo` da plataforma ou o plano exportado do `dominio`."
    )
    contas: int = Field(ge=0, description="Contas na planilha.")
    contas_novas: int = Field(ge=0, alias="contasNovas")
    contas_alteradas: int = Field(ge=0, alias="contasAlteradas")
    contas_sem_mudanca: int = Field(ge=0, alias="contasSemMudanca")
    contas_ausentes: int = Field(ge=0, alias="contasAusentes")
    contas_ausentes_em_uso: int = Field(
        ge=0,
        alias="contasAusentesEmUso",
        description="Quantas das ausentes o de-para ou a conta do banco usam.",
    )
    contas_que_voltaram: int = Field(
        ge=0,
        alias="contasQueVoltaram",
        description="Contas com o selo de ausente que vieram nesta planilha (o selo sai).",
    )
    contas_inativas_na_planilha: int = Field(
        ge=0,
        alias="contasInativasNaPlanilha",
        description=(
            "Contas inativas que vieram na planilha: a importação NÃO as reativa (a "
            "situação só muda pela edição da conta)."
        ),
    )
    tem_mudancas: bool = Field(
        alias="temMudancas",
        description="`false` = importar esta planilha não muda nada no plano.",
    )
    novas: list[ChartPreviewNewAccount] = Field(
        description=f"Até {CHART_PREVIEW_LIST_LIMIT}, na ordem da planilha."
    )
    alteradas: list[ChartPreviewChangedAccount] = Field(
        description=f"Até {CHART_PREVIEW_LIST_LIMIT}, na ordem da planilha."
    )
    ausentes: list[ChartPreviewAbsentAccount] = Field(
        description=(
            f"Até {CHART_PREVIEW_LIST_LIMIT}: as em uso primeiro, depois na ordem do plano."
        )
    )
    list_limit: int = Field(
        alias="listLimit", description="O teto de cada lista; as contagens são as reais."
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_preview(cls, preview: ChartPreview) -> ChartImportPreviewPayload:
        diff = preview.diff
        names = preview.names
        limit = CHART_PREVIEW_LIST_LIMIT
        absent = sorted(diff.absent, key=lambda a: a.id not in preview.absent_in_use)
        return cls(
            layout=preview.layout,
            contas=preview.accounts,
            contas_novas=len(diff.new_rows),
            contas_alteradas=len(diff.changed),
            contas_sem_mudanca=len(diff.unchanged_rows),
            contas_ausentes=len(diff.absent),
            contas_ausentes_em_uso=len(preview.absent_in_use),
            contas_que_voltaram=len(diff.returned),
            contas_inativas_na_planilha=len(diff.inactive_present),
            tem_mudancas=diff.has_changes,
            novas=[
                ChartPreviewNewAccount(
                    code=row.code,
                    name=row.name,
                    account_type=row.account_type,
                    classification=row.classification,
                )
                for row in diff.new_rows[:limit]
            ],
            alteradas=[
                ChartPreviewChangedAccount(
                    account_id=item.account.id,
                    code=item.account.code,
                    name=item.row.name,
                    changes=list(item.changes),
                )
                for item in diff.changed[:limit]
            ],
            ausentes=[
                ChartPreviewAbsentAccount(
                    account_id=account.id,
                    code=account.code,
                    name=names.names.get(account.id, ACCOUNTING_ACCOUNT_UNDECIPHERABLE),
                    name_resolved=account.id in names.names and account.id not in names.failed,
                    active=account.active,
                    in_use=account.id in preview.absent_in_use,
                    absent_since=account.absent_since,
                )
                for account in absent[:limit]
            ],
            list_limit=limit,
        )


class ChartImportPreviewEnvelope(BaseModel):
    """Body de `POST /clients/{client_id}/accounting-chart/import/preview`."""

    data: ChartImportPreviewPayload


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


# ------------------------------------------------ vínculo conta ↔ pessoa (86e3qhhrn)

#: Teto da lista de linhas NÃO vinculadas na resposta da importação (as contagens
#: são sempre as reais). Mesmo teto das linhas inválidas do leitor.
PARTIES_SKIPPED_LIST_LIMIT = 50

PartyDocumentKind = Literal["cnpj", "cpf"]


class PartySkippedLine(BaseModel):
    """Uma linha do relatório que ficou SEM vínculo: linha, motivo fechado e o código da
    conta (nunca o documento, o nome nem a razão social)."""

    line: int
    reason: str = Field(
        description=(
            "`sem_conta` (conta 0 no Domínio), `documento_ausente`, `documento_invalido` "
            "(dígito verificador errado no Domínio) ou `conta_compartilhada` (a mesma conta "
            "em mais de uma pessoa: vínculo um-para-um impossível)."
        )
    )
    account_code: str | None = Field(default=None, alias="accountCode")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_problem(cls, problem: PartyLineProblem) -> PartySkippedLine:
        return cls(line=problem.line, reason=problem.reason, account_code=problem.account_code)


class PartiesImportPayload(BaseModel):
    """As contagens da importação de uma relação — só números, o tipo e as linhas puladas."""

    relatorio: AccountingPartyType = Field(
        description="`cliente` (RELAÇÃO DE CLIENTES) ou `fornecedor` (RELAÇÃO DE FORNECEDORES)."
    )
    linhas: int = Field(ge=0, description="Pessoas no relatório.")
    contas_vinculadas: int = Field(
        ge=0,
        alias="contasVinculadas",
        description="Contas do plano com vínculo depois desta importação (novas + atualizadas + sem mudança).",
    )
    vinculos_novos: int = Field(ge=0, alias="vinculosNovos")
    vinculos_atualizados: int = Field(
        ge=0,
        alias="vinculosAtualizados",
        description="Contas já vinculadas cujo documento ou tipo mudou.",
    )
    vinculos_sem_mudanca: int = Field(ge=0, alias="vinculosSemMudanca")
    nao_vinculadas: list[PartySkippedLine] = Field(
        alias="naoVinculadas",
        description=f"Até {PARTIES_SKIPPED_LIST_LIMIT} linhas que ficaram sem vínculo, na ordem do relatório.",
    )
    nao_vinculadas_total: int = Field(ge=0, alias="naoVinculadasTotal")
    list_limit: int = Field(alias="listLimit")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_result(cls, result: PartyImportResult) -> PartiesImportPayload:
        return cls(
            relatorio=result.party_type,
            linhas=result.lines,
            contas_vinculadas=result.linked,
            vinculos_novos=result.new,
            vinculos_atualizados=result.updated,
            vinculos_sem_mudanca=result.unchanged,
            nao_vinculadas=[
                PartySkippedLine.from_problem(p)
                for p in result.skipped[:PARTIES_SKIPPED_LIST_LIMIT]
            ],
            nao_vinculadas_total=len(result.skipped),
            list_limit=PARTIES_SKIPPED_LIST_LIMIT,
        )


class PartiesImportEnvelope(BaseModel):
    """Body de `POST /clients/{client_id}/accounting-chart/parties/import`."""

    data: PartiesImportPayload


class PartyLinkResponse(BaseModel):
    """Uma conta do plano COM vínculo: a conta, o tipo e o documento MASCARADO."""

    account_id: UUID = Field(alias="accountId")
    code: str
    name: str = Field(
        description="Nome da conta, decifrado na leitura (`[indecifrável]` se não abrir)."
    )
    name_resolved: bool = Field(alias="nameResolved")
    active: bool
    party_type: AccountingPartyType = Field(alias="partyType")
    document_kind: PartyDocumentKind | None = Field(
        default=None,
        alias="documentKind",
        description="`cnpj` ou `cpf`; `null` quando o documento não decifrou.",
    )
    document_masked: str | None = Field(
        default=None,
        alias="documentMasked",
        description=(
            "O documento com a raiz escondida (`**.***.***/0001-91`, `***.***.*89-00`). O "
            "documento inteiro nunca sai da API."
        ),
    )
    document_resolved: bool = Field(
        alias="documentResolved",
        description="`false` = o documento não decifrou (cliente encerrado) e sai sem máscara nenhuma.",
    )
    updated_at: datetime = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_row(
        cls, row: ClientAccountingAccount, *, names: DecryptedNames, documents: PartyDocuments
    ) -> PartyLinkResponse:
        resolved = row.id in documents.masked and row.id not in documents.failed
        return cls(
            account_id=row.id,
            code=row.code,
            name=names.names.get(row.id, ACCOUNTING_ACCOUNT_UNDECIPHERABLE),
            name_resolved=row.id in names.names and row.id not in names.failed,
            active=row.active,
            party_type=AccountingPartyType(row.party_type or AccountingPartyType.CLIENTE.value),
            document_kind=documents.kinds.get(row.id) if resolved else None,
            document_masked=documents.masked.get(row.id) if resolved else None,
            document_resolved=resolved,
            updated_at=row.updated_at,
        )


class PartyLinksSummaryPayload(BaseModel):
    """Quantas contas do plano têm vínculo, por tipo — só contagens."""

    contas_vinculadas: int = Field(ge=0, alias="contasVinculadas")
    clientes: int = Field(ge=0)
    fornecedores: int = Field(ge=0)

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_summary(cls, summary: PartyLinksSummary) -> PartyLinksSummaryPayload:
        return cls(
            contas_vinculadas=summary.linked,
            clientes=summary.clientes,
            fornecedores=summary.fornecedores,
        )


class PartyLinksListResponse(BaseModel):
    """Body de `GET /clients/{client_id}/accounting-chart/parties`."""

    data: list[PartyLinkResponse]
    pagination: PaginationMeta
    summary: PartyLinksSummaryPayload
