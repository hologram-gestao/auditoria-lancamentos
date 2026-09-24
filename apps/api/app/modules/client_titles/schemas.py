"""A tradução DTO neutro → linha da carteira (Sprint 11, BACK 11.2).

Fica num módulo só e é **uma função** porque é aqui que duas coisas podem dar
errado em silêncio: gravar nome (§4.5) e derivar o `status` errado. Um mapa
escrito à mão dentro do serviço faria as duas passarem em revisão visual.

**O que é DELIBERADAMENTE descartado** do que a origem manda: `observacao` (texto
livre em que a Omie ecoa descrição de compra e nome de fornecedor), qualquer
campo de nome, e o rótulo de situação verbatim — este último serve para derivar o
`status` e morre aqui, porque guardá-lo criaria um segundo vocabulário de estado
ao lado de `TitleStatus`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.client_title import TitleStatus, TitleType
from app.db.models.title_context import TitleContextType
from app.modules.client_titles.aging import AgingBucket, bucket_for_days
from app.modules.reconciliations.schemas import SessionAuthor
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.db.models.client_title import ClientTitle
    from app.db.models.title_context import TitleContext
    from app.integrations.providers.base import ProviderOpenTitle
    from app.modules.client_titles.repository import (
        AgingTotals,
        ReceivablesGroupTotals,
        ReceivablesReport,
        ReceivablesSideReport,
        TitlesSummary,
    )

#: Teto do texto livre do contexto — mesmo teto de `resolution_note` da revisão
#: de anomalias (`reconciliations/review/schemas.py`), convenção da casa para
#: nota de texto livre.
MAX_TITLE_CONTEXT_TEXT_CHARS = 2000

#: Marcador de falha de decifragem — mesma convenção do glossário
#: (`glossary/schemas.py`). Célula NUNCA fica vazia em silêncio (CLAUDE.md §4.1).
TITLE_CONTEXT_UNDECIPHERABLE = "[indecifrável]"

#: Rótulos de situação com que a origem diz "este título foi liquidado".
#: Fechado de propósito, e é a **única** porta para `TitleStatus.LIQUIDADO`.
#: Qualquer outro rótulo mantém o título `em_aberto`: a consulta que a carteira
#: faz pede só os NÃO liquidados, e inferir pagamento de um rótulo desconhecido
#: seria a plataforma afirmando uma quitação que ninguém verificou. Quem SOME da
#: origem vira `ausente_na_origem`, nunca isto — ver `close_titles_absent_from`.
SETTLED_SITUATIONS = frozenset({"PAGO", "RECEBIDO", "LIQUIDADO"})


def title_status_from_situation(situation: str) -> TitleStatus:
    """O `status` da carteira a partir do rótulo de situação da origem.

    Comparação normalizada (maiúsculas, sem espaço nas pontas) porque o rótulo é
    texto de terceiro: `'pago'` e `'PAGO '` são o mesmo fato, e deixar a
    diferença decidir o estado de um título seria um defeito que só aparece num
    cliente.
    """
    if situation.strip().upper() in SETTLED_SITUATIONS:
        return TitleStatus.LIQUIDADO
    return TitleStatus.EM_ABERTO


def client_title_row(title: ProviderOpenTitle) -> dict[str, Any]:
    """Uma linha de `client_titles` a partir do DTO neutro.

    As chaves são EXATAMENTE as colunas que o `ON CONFLICT DO UPDATE` do
    repositório atualiza — chave a mais estoura no `INSERT`, chave a menos deixa
    a coluna com o default em vez do valor da origem. `client_id` e
    `last_synced_at` NÃO entram aqui: são do ciclo, e o repositório os injeta.

    `account_external_id` e `supplier_code` chegam como texto (o DTO é neutro) e
    viram inteiro na borda, que é onde a coluna é numérica. Valor não-numérico
    vira `None` em vez de derrubar a sincronização inteira: é código de terceiro,
    e um cadastro esquisito num título não pode custar a carteira do cliente.
    """
    return {
        "external_id": title.external_id,
        "title_type": TitleType(title.kind.value).value,
        "due_date": title.due_date,
        "amount": title.amount,
        "status": title_status_from_situation(title.situation).value,
        "category_code": title.category_code,
        "supplier_code": _as_int(title.supplier_code),
        "omie_conta_id": _as_int(title.account_external_id),
        "document_number": title.document_number,
    }


def _as_int(value: str | None) -> int | None:
    """Código numérico de terceiro como `int`; qualquer outra coisa é `None`."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# Respostas da API (Sprint 11, BACK 11.5)
# ----------------------------------------------------------------------

#: Tipos aceitos no filtro `?type=`. `Literal` (e não `str`) porque valor fora do
#: vocabulário tem de virar **400 `VALIDATION_ERROR`** pelo handler global — não
#: uma lista vazia que o usuário lê como "este cliente não tem nada a pagar".
TitleTypeFilter = Literal["a_pagar", "a_receber"]

#: Situações aceitas no filtro `?situation=`. Derivadas do VENCIMENTO, não do
#: `status` da linha — ver `TitleSituation` no repositório.
TitleSituationFilter = Literal["em_aberto", "vencido"]

#: Baldes aceitos no filtro `?bucket=`. As strings são as de `AgingBucket`.
TitleBucketFilter = Literal["a_vencer", "1_30", "31_60", "61_90", "90_mais"]

#: Campos de ordenação aceitos em `?sortBy=`. Fechado aqui para que a rota não
#: monte `ORDER BY` a partir de string livre do cliente.
TitleSortFieldFilter = Literal["due_date", "amount"]

TitleSortOrderFilter = Literal["asc", "desc"]


class ClientTitleResponse(BaseModel):
    """Um título da carteira, como a API o devolve.

    ⚠️ `supplierName` **não** vem do banco: é resolvido em RUNTIME pelo mesmo
    `ConsultarCliente` + `OmieClientesCache` que a aba de Divergências usa, e é
    `None` quando a origem não responde (fail-soft). Persistir o nome resolveria o
    `None` e quebraria a §4.5.

    `supplierNameResolved` existe para a tela **não** ter de adivinhar o que um
    `null` significa: `false` diz "mostre o código e marque como não resolvido",
    que é o comportamento que o R4 pede — e é diferente de "este título não tem
    fornecedor" (aí `supplierCode` também é nulo).
    """

    id: UUID = Field(
        description=(
            "PK do título. O tenant já lê a linha inteira nesta resposta — não "
            "há razão de segurança para esconder o identificador, e sem ele a "
            "tela não tem como montar a URL de `.../titles/{title_id}/context` "
            "(Sprint 15)."
        )
    )
    external_id: str = Field(alias="externalId", description="Identificador do título na origem.")
    title_type: TitleType = Field(alias="titleType", description="`a_pagar` ou `a_receber`.")
    due_date: date = Field(alias="dueDate", description="Data de vencimento — base do aging.")
    amount: Decimal = Field(
        description=(
            "Valor do documento, SEM sinal: `titleType` já diz se é obrigação ou "
            "direito. `Numeric(14,2)` — nunca ponto flutuante."
        )
    )
    status: TitleStatus = Field(
        description=(
            "`em_aberto` é o que a carteira cobra. `liquidado` e "
            "`ausente_na_origem` são SAÍDAS: a linha fica (pode haver contexto "
            "apontando para ela), mas não entra nos agregados."
        )
    )
    overdue_days: int = Field(
        alias="overdueDays",
        description=(
            "Dias de atraso contra a data de referência do SERVIDOR. `0` = ainda "
            "não venceu (ou vence hoje) — nunca negativo."
        ),
    )
    bucket: AgingBucket | None = Field(
        default=None,
        description=(
            "Balde de aging da linha. `null` para título que já saiu do aberto "
            "(`liquidado`/`ausente_na_origem`), que não pertence a balde nenhum."
        ),
    )
    category_code: str | None = Field(
        default=None, alias="categoryCode", description="Código da categoria na origem."
    )
    supplier_code: int | None = Field(
        default=None,
        alias="supplierCode",
        description="Código do devedor/credor no cadastro da origem. Nunca o nome.",
    )
    supplier_name: str | None = Field(
        default=None,
        alias="supplierName",
        description=(
            "Razão social resolvida em RUNTIME pelo cache de clientes. `null` "
            "quando não há código ou a origem não respondeu — a lista continua "
            "sendo servida assim mesmo."
        ),
    )
    supplier_name_resolved: bool = Field(
        alias="supplierNameResolved",
        description=(
            "`false` quando existe `supplierCode` e o nome NÃO pôde ser "
            "resolvido: a tela mostra o código com a marcação de 'não "
            "resolvido', nunca um campo vazio."
        ),
    )
    omie_conta_id: int | None = Field(
        default=None, alias="omieContaId", description="Conta corrente do título na origem."
    )
    document_number: str | None = Field(
        default=None, alias="documentNumber", description="Número do documento, quando houver."
    )
    last_synced_at: datetime = Field(
        alias="lastSyncedAt",
        description="Quando esta linha foi vista pela origem pela última vez.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_row(
        cls,
        row: ClientTitle,
        *,
        supplier_names: Mapping[int, str],
        today: date,
    ) -> ClientTitleResponse:
        """Monta a resposta juntando a linha com os nomes resolvidos em runtime.

        Ausência no mapa vira `supplierNameResolved=False` — **nunca** o código
        repetido como se fosse nome, que faria a tela mostrar "2624256082" na
        coluna Fornecedor e parecer dado bom.
        """
        name = supplier_names.get(row.supplier_code) if row.supplier_code is not None else None
        aberto = row.status == TitleStatus.EM_ABERTO.value
        atraso = max((today - row.due_date).days, 0)
        return cls(
            id=row.id,
            external_id=row.external_id,
            title_type=TitleType(row.title_type),
            due_date=row.due_date,
            amount=row.amount,
            status=TitleStatus(row.status),
            overdue_days=atraso,
            bucket=bucket_for_days(atraso) if aberto else None,
            category_code=row.category_code,
            supplier_code=row.supplier_code,
            supplier_name=name,
            # Título sem código de fornecedor não é "não resolvido": não há o que
            # resolver. A marcação só liga quando há código E falta nome.
            supplier_name_resolved=row.supplier_code is None or name is not None,
            omie_conta_id=row.omie_conta_id,
            document_number=row.document_number,
            last_synced_at=row.last_synced_at,
        )


class ClientTitlesListResponse(BaseModel):
    """Body de `GET /clients/{client_id}/titles`."""

    data: list[ClientTitleResponse]
    pagination: PaginationMeta


class AgingTotalsResponse(BaseModel):
    """Agregados de UM tipo de título, com os quatro baldes.

    Os baldes são campos NOMEADOS (e não um dicionário): o contrato do front
    precisa ser tipado, e um mapa com chave dinâmica deixaria um balde renomeado
    passar pelo `gen:types` sem erro.
    """

    total_em_aberto: Decimal = Field(
        alias="totalEmAberto", description="Tudo que está em aberto, vencido ou não."
    )
    total_a_vencer: Decimal = Field(
        alias="totalAVencer", description="Em aberto com vencimento futuro (ou hoje)."
    )
    total_vencido: Decimal = Field(
        alias="totalVencido",
        description="Em aberto com vencimento passado. **Igual à soma dos quatro baldes.**",
    )
    bucket_1_30: Decimal = Field(alias="bucket1a30", description="Atraso de 1 a 30 dias.")
    bucket_31_60: Decimal = Field(alias="bucket31a60", description="Atraso de 31 a 60 dias.")
    bucket_61_90: Decimal = Field(alias="bucket61a90", description="Atraso de 61 a 90 dias.")
    bucket_90_mais: Decimal = Field(alias="bucket90Mais", description="Atraso de 91 dias ou mais.")
    qtd_em_aberto: int = Field(ge=0, alias="qtdEmAberto")
    qtd_a_vencer: int = Field(ge=0, alias="qtdAVencer")
    qtd_vencido: int = Field(ge=0, alias="qtdVencido")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_totals(cls, totals: AgingTotals) -> AgingTotalsResponse:
        return cls(
            total_em_aberto=totals.total_em_aberto,
            total_a_vencer=totals.total_a_vencer,
            total_vencido=totals.total_vencido,
            bucket_1_30=totals.baldes[AgingBucket.D1_30],
            bucket_31_60=totals.baldes[AgingBucket.D31_60],
            bucket_61_90=totals.baldes[AgingBucket.D61_90],
            bucket_90_mais=totals.baldes[AgingBucket.D90_MAIS],
            qtd_em_aberto=totals.qtd_em_aberto,
            qtd_a_vencer=totals.qtd_a_vencer,
            qtd_vencido=totals.qtd_vencido,
        )


class TitlesSummaryResponse(BaseModel):
    """Bloco de agregados da carteira, com o estado da sincronização.

    `neverSynced` é campo EXPLÍCITO, e não algo a derivar de `syncedAt == null`
    no front: é a diferença entre "este cliente não deve nada" e "ninguém nunca
    consultou a origem deste cliente", e ela não pode depender de cada tela
    lembrar da regra (R3).
    """

    a_pagar: AgingTotalsResponse = Field(alias="aPagar")
    a_receber: AgingTotalsResponse = Field(alias="aReceber")
    never_synced: bool = Field(
        alias="neverSynced",
        description=(
            "`true` = a carteira NUNCA foi sincronizada. A tela oferece "
            "'sincronizar' e **não** mostra zeros como se fossem resultado."
        ),
    )
    synced_at: datetime | None = Field(
        default=None,
        alias="syncedAt",
        description="Última sincronização ÍNTEGRA. `null` = nunca houve uma.",
    )
    sync_failed_at: datetime | None = Field(
        default=None,
        alias="syncFailedAt",
        description=(
            "Última tentativa que FALHOU, quando a mais recente falhou. Vem "
            "junto com `syncedAt` de propósito: os agregados acima são os da "
            "última íntegra, e a tela precisa dizer de quando eles são."
        ),
    )
    reference_date: date = Field(
        alias="referenceDate",
        description=(
            "Data do SERVIDOR usada como referência do aging. A tela exibe os "
            "baldes com ela — recalcular com o relógio do navegador poria o "
            "mesmo título em baldes diferentes para pessoas em fusos diferentes."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_summary(cls, summary: TitlesSummary) -> TitlesSummaryResponse:
        return cls(
            a_pagar=AgingTotalsResponse.from_totals(summary.a_pagar),
            a_receber=AgingTotalsResponse.from_totals(summary.a_receber),
            never_synced=summary.nunca_sincronizada,
            synced_at=summary.synced_at,
            sync_failed_at=summary.sync_failed_at,
            reference_date=summary.referencia,
        )


class TitlesSummaryEnvelope(BaseModel):
    """Envelope `{data: ...}` de `GET /clients/{client_id}/titles/summary`."""

    data: TitlesSummaryResponse


class TitlesSyncResponse(BaseModel):
    """O que uma sincronização manual fez, em contagens — e o bloco resultante.

    Devolver o `summary` junto evita que a tela dispare uma segunda requisição só
    para redesenhar os baldes que acabaram de mudar.
    """

    titulos_pagar: int = Field(ge=0, alias="titulosPagar")
    titulos_receber: int = Field(ge=0, alias="titulosReceber")
    vencidos: int = Field(ge=0)
    mais_antigo_dias: int = Field(ge=0, alias="maisAntigoDias")
    summary: TitlesSummaryResponse

    model_config = ConfigDict(populate_by_name=True)


class TitlesSyncEnvelope(BaseModel):
    """Envelope `{data: ...}` de `POST /clients/{client_id}/titles/sync`."""

    data: TitlesSyncResponse


# ----------------------------------------------------------------------
# Contexto do título (Sprint 15, BACK 15.1)
# ----------------------------------------------------------------------

#: Vocabulário aceito em `POST .../context`. `Literal` (não `str`) para que um
#: valor fora do conjunto seja validação de FORMA — 400 `VALIDATION_ERROR`
#: genérico pelo handler global (convenção de 23/09/2026), nunca um 422
#: inventado. As seis strings são as de `TitleContextType`.
TitleContextTypeFilter = Literal[
    "acordo_de_pagamento",
    "pagamento_antecipado",
    "nota_a_cancelar",
    "cobranca_suspensa",
    "perda_provavel",
    "outro",
]


class TitleContextCreateRequest(BaseModel):
    """Body de `POST /clients/{client_id}/titles/{title_id}/context`."""

    type: TitleContextTypeFilter = Field(
        description=(
            "Um dos seis tipos fechados. Fora do conjunto: 400 `VALIDATION_ERROR` "
            "(validação de forma, não 422)."
        )
    )
    text: str = Field(
        min_length=1,
        max_length=MAX_TITLE_CONTEXT_TEXT_CHARS,
        description="Texto livre do analista — nasce cifrado com a chave do cliente.",
    )

    model_config = ConfigDict(populate_by_name=True)


class TitleContextResponse(BaseModel):
    """Uma entrada de contexto, como a API a devolve — já decifrada.

    `author` é o objeto ENXUTO e MASCARADO por escopo (`author_for_viewer`,
    `reconciliations/service.py`) — o mesmo precedente da autoria de sessão
    (§3.15): nunca a linha de `users`, nunca o `id` do autor.
    """

    id: UUID
    title_id: UUID = Field(alias="titleId")
    type: TitleContextType
    text: str
    decrypt_failed: bool = Field(
        default=False,
        alias="decryptFailed",
        description="`true` quando o texto não pôde ser decifrado — nunca célula vazia em silêncio.",
    )
    author: SessionAuthor
    created_at: datetime = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(
        cls,
        context: TitleContext,
        *,
        text: str,
        decrypt_failed: bool = False,
        author: SessionAuthor,
    ) -> TitleContextResponse:
        return cls(
            id=context.id,
            title_id=context.title_id,
            type=TitleContextType(context.context_type),
            text=text,
            decrypt_failed=decrypt_failed,
            author=author,
            created_at=context.created_at,
        )


class TitleContextListResponse(BaseModel):
    """Body de `GET /clients/{client_id}/titles/{title_id}/context`.

    Histórico COMPLETO, mais recente primeiro — sem paginação: é o registro de
    um título, não uma coleção que cresce sem teto (CLAUDE.md: append-only,
    mas por título, não por cliente).
    """

    data: list[TitleContextResponse]


class TitleContextEnvelope(BaseModel):
    """Envelope `{data: ...}` de `POST /clients/{client_id}/titles/{title_id}/context`."""

    data: TitleContextResponse


# ----------------------------------------------------------------------
# Relatório de recebíveis (Sprint 15, BACK 15.2)
# ----------------------------------------------------------------------


class ReceivablesGroupResponse(BaseModel):
    """Os agregados de UM grupo (inadimplência OU vencido-com-contexto) de UM
    lado — todos vencidos por construção, sem balde `a_vencer` (ver
    `ReceivablesGroupTotals`)."""

    total: Decimal
    bucket_1_30: Decimal = Field(alias="bucket1a30", description="Atraso de 1 a 30 dias.")
    bucket_31_60: Decimal = Field(alias="bucket31a60", description="Atraso de 31 a 60 dias.")
    bucket_61_90: Decimal = Field(alias="bucket61a90", description="Atraso de 61 a 90 dias.")
    bucket_90_mais: Decimal = Field(alias="bucket90Mais", description="Atraso de 91 dias ou mais.")
    qtd: int = Field(ge=0)

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_totals(cls, totals: ReceivablesGroupTotals) -> ReceivablesGroupResponse:
        return cls(
            total=totals.total,
            bucket_1_30=totals.baldes[AgingBucket.D1_30],
            bucket_31_60=totals.baldes[AgingBucket.D31_60],
            bucket_61_90=totals.baldes[AgingBucket.D61_90],
            bucket_90_mais=totals.baldes[AgingBucket.D90_MAIS],
            qtd=totals.qtd,
        )


class ReceivablesSideResponse(BaseModel):
    """Os dois grupos de UM lado (a pagar OU a receber)."""

    inadimplencia: ReceivablesGroupResponse
    vencido_com_contexto: ReceivablesGroupResponse = Field(alias="vencidoComContexto")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_side(cls, side: ReceivablesSideReport) -> ReceivablesSideResponse:
        return cls(
            inadimplencia=ReceivablesGroupResponse.from_totals(side.inadimplencia),
            vencido_com_contexto=ReceivablesGroupResponse.from_totals(side.vencido_com_contexto),
        )


class ReceivablesReportResponse(BaseModel):
    """Body de `GET /clients/{client_id}/titles/receivables-report`.

    Os DOIS lados, cada um com os DOIS grupos — calculados no SERVIDOR sobre a
    carteira INTEIRA. `referenceDate` é o "hoje" do servidor usado no aging dos
    baldes, mesma razão de `TitlesSummaryResponse.referenceDate`.
    """

    a_pagar: ReceivablesSideResponse = Field(alias="aPagar")
    a_receber: ReceivablesSideResponse = Field(alias="aReceber")
    reference_date: date = Field(alias="referenceDate")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_report(cls, report: ReceivablesReport) -> ReceivablesReportResponse:
        return cls(
            a_pagar=ReceivablesSideResponse.from_side(report.a_pagar),
            a_receber=ReceivablesSideResponse.from_side(report.a_receber),
            reference_date=report.referencia,
        )


class ReceivablesReportEnvelope(BaseModel):
    """Envelope `{data: ...}` de `GET /clients/{client_id}/titles/receivables-report`."""

    data: ReceivablesReportResponse
