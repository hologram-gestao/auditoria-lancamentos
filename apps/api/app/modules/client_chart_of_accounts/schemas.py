"""Schemas e tradução do plano de contas do cliente (Sprint 10, BACK 10.1).

Aqui mora a **única** tradução `CategoriaOmie` → linha de
`client_chart_of_accounts`. Um lugar só, porque é ele que decide o que É e o que
NÃO É persistido — e essa decisão não pode existir em duas cópias:

    - vai para o banco: código, hierarquia, conta de demonstrativo (código,
      nível, sinal), código da conta contábil, flags e situação;
    - **não** vai: `descricao`, `dadosDRE.descricaoDRE` e `tag_conta_contabil`.
      Os três são NOME (de categoria ou de conta), e a §4.5 do primer mantém
      nome fora do disco em claro — a tela resolve os nomes em runtime pelo
      cache de 6h que já serve a tela de revisão. O contrato (`CategoriaOmie`)
      declara os três porque a origem os manda; a persistência para aqui.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.client_chart_of_accounts import ChartOfAccountsStatus
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.db.models.client_chart_of_accounts import ClientChartOfAccount
    from app.integrations.omie.schemas import CategoriaOmie
    from app.modules.client_chart_of_accounts.repository import ChartOfAccountsCoverage

#: Situações aceitas no filtro `?status=`. `Literal` (e não `str`) porque valor
#: fora do vocabulário tem de virar **422 automático** do Pydantic, não uma
#: lista vazia que o usuário lê como "este cliente não tem nada ativo".
ChartOfAccountsStatusFilter = Literal["ativa", "inativa", "ausente_na_origem"]

#: Teto do termo de busca por código. Código de plano de contas não passa de
#: uns 15 caracteres; 50 é o tamanho da coluna. Validar na borda evita que um
#: `ILIKE` com 10 KB de termo chegue ao banco.
MAX_CODE_SEARCH_CHARS = 50


def chart_of_accounts_row(categoria: CategoriaOmie) -> dict[str, Any]:
    """Uma categoria da origem como linha do plano de contas.

    Devolve `dict` (e não o modelo ORM) de propósito: o gravador é um
    `INSERT ... ON CONFLICT DO UPDATE` em lote, que trabalha sobre mapas. Sem
    `client_id` nem `synced_at` — quem sabe o tenant e o instante é o
    repositório, e passá-los por aqui abriria caminho para uma linha nascer com
    o tenant do caller errado.

    **Destino nunca é inferido**: `dre_code` vem do que a origem declarou ou é
    `None` ("sem destino declarado"). Na amostra real, os `None` são as
    transferências e as totalizadoras — que por definição contábil não têm
    conta de demonstrativo própria. Preencher esse buraco por heurística seria
    inventar classificação contábil em nome do cliente.
    """
    dre = categoria.dados_dre
    return {
        "category_code": categoria.codigo,
        "parent_code": categoria.parent_code,
        "dre_code": categoria.destino_code,
        "dre_level": dre.nivel_dre,
        "dre_sign": dre.sinal_dre,
        "conta_contabil_code": categoria.conta_contabil_code,
        "totalizadora": categoria.is_totalizadora,
        "transferencia": categoria.is_transferencia,
        "nao_exibir": categoria.is_nao_exibir,
        "status": (
            ChartOfAccountsStatus.ATIVA.value
            if categoria.is_active
            else ChartOfAccountsStatus.INATIVA.value
        ),
    }


# ----------------------------------------------------------------------
# Respostas da API (Sprint 10, BACK 10.3)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedNames:
    """Nomes resolvidos em RUNTIME, em dois mapas SEPARADOS.

    Um dicionário só estaria errado: código de categoria e código de conta de
    demonstrativo são namespaces diferentes que COLIDEM. Na resposta real, a
    categoria `1.01.01` chama-se "BPO Controller - RB" e a conta de
    demonstrativo `1.01.01` chama-se "Receita Bruta de Vendas" — misturá-los
    mostraria o nome de uma no lugar da outra, e a tela pareceria certa.

    Vazio = a origem não respondeu. O caminho fail-soft: a lista sai com os
    nomes nulos, e não com 502.
    """

    categories: dict[str, str]
    dre: dict[str, str]


class ChartOfAccountEntryResponse(BaseModel):
    """Uma linha do plano de contas, como a API a devolve.

    ⚠️ `name` **não** vem do banco: é resolvido em RUNTIME pelo mesmo
    `OmieCategoriasService` que a tela de revisão usa, e é `None` quando a
    origem não responde (fail-soft). Persistir o nome resolveria o `None` e
    quebraria a §4.5 — e faria as duas telas divergirem no dia em que uma
    descrição mudasse no Omie.
    """

    category_code: str = Field(alias="categoryCode", description="Código da categoria na origem.")
    name: str | None = Field(
        default=None,
        description=(
            "Descrição da categoria, resolvida em runtime pelo cache de "
            "categorias. `null` quando a origem não respondeu — a lista "
            "continua sendo servida assim mesmo."
        ),
    )
    parent_code: str | None = Field(
        default=None,
        alias="parentCode",
        description="Código da categoria pai. `null` na raiz.",
    )
    dre_code: str | None = Field(
        default=None,
        alias="dreCode",
        description=(
            "Código da conta de demonstrativo vinculada. `null` = **sem destino "
            "declarado** — informação, não erro: transferências e totalizadoras "
            "não têm conta de demonstrativo própria."
        ),
    )
    dre_name: str | None = Field(
        default=None,
        alias="dreName",
        description=(
            "Descrição da conta de demonstrativo, resolvida em runtime como o "
            "`name`. `null` quando não há destino ou a origem não respondeu."
        ),
    )
    dre_level: int | None = Field(
        default=None, alias="dreLevel", description="Profundidade no demonstrativo."
    )
    dre_sign: str | None = Field(
        default=None, alias="dreSign", description="`+` ou `-` no demonstrativo."
    )
    conta_contabil_code: str | None = Field(
        default=None,
        alias="contaContabilCode",
        description="Código da conta contábil vinculada, quando houver.",
    )
    totalizadora: bool = Field(description="Só soma as filhas; não recebe lançamento.")
    transferencia: bool = Field(description="Categoria de transferência entre contas.")
    nao_exibir: bool = Field(alias="naoExibir", description="Escondida nas telas do Omie.")
    status: ChartOfAccountsStatus = Field(
        description=(
            "`ativa` e `inativa` espelham a origem; `ausente_na_origem` é a "
            "categoria que sumiu do cadastro e **não** foi apagada aqui."
        )
    )
    synced_at: datetime = Field(
        alias="syncedAt", description="Quando esta linha foi vista pela origem pela última vez."
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_row(
        cls,
        row: ClientChartOfAccount,
        *,
        names: ResolvedNames,
    ) -> ChartOfAccountEntryResponse:
        """Monta a resposta juntando a linha com os nomes resolvidos em runtime.

        Ausência de chave vira `None` — nunca o código repetido como se fosse
        nome, que faria a tela mostrar "1.01.01" na coluna Nome e parecer dado
        bom.
        """
        return cls(
            category_code=row.category_code,
            name=names.categories.get(row.category_code),
            parent_code=row.parent_code,
            dre_code=row.dre_code,
            dre_name=names.dre.get(row.dre_code) if row.dre_code else None,
            dre_level=row.dre_level,
            dre_sign=row.dre_sign,
            conta_contabil_code=row.conta_contabil_code,
            totalizadora=row.totalizadora,
            transferencia=row.transferencia,
            nao_exibir=row.nao_exibir,
            status=ChartOfAccountsStatus(row.status),
            synced_at=row.synced_at,
        )


class ChartOfAccountsListResponse(BaseModel):
    """Body de `GET /clients/{client_id}/chart-of-accounts`."""

    data: list[ChartOfAccountEntryResponse]
    pagination: PaginationMeta


class ChartOfAccountsCoverageResponse(BaseModel):
    """As cinco contagens da cobertura, sobre o CONJUNTO INTEIRO do cliente.

    Rota própria, e não um campo da lista: é uma pergunta diferente ("quanto do
    de-para já vem pronto") e a resposta não pode mudar conforme a página.
    `comDestino + semDestino == ativas` — as três parcelas saem da mesma base
    ativa, na mesma query.
    """

    total: int = Field(ge=0, description="Todas as linhas, inclusive inativas e ausentes.")
    ativas: int = Field(ge=0, description="Linhas com situação `ativa`.")
    com_destino: int = Field(
        ge=0,
        alias="comDestino",
        description="Ativas com conta de demonstrativo — o numerador da métrica da sprint.",
    )
    sem_destino: int = Field(
        ge=0,
        alias="semDestino",
        description="Ativas **sem destino declarado**. É informação, não pendência a corrigir.",
    )
    com_conta_contabil: int = Field(
        ge=0, alias="comContaContabil", description="Ativas com conta contábil vinculada."
    )
    synced_at: datetime | None = Field(
        default=None,
        alias="syncedAt",
        description="Última sincronização BEM-SUCEDIDA. `null` = nunca sincronizou (estado vazio).",
    )
    sync_failed_at: datetime | None = Field(
        default=None,
        alias="syncFailedAt",
        description=(
            "Última tentativa que FALHOU, quando a mais recente falhou. Vem "
            "junto com `syncedAt` de propósito: a tela precisa dizer 'falhou "
            "agora, e a última boa foi tal dia'."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_coverage(
        cls,
        coverage: ChartOfAccountsCoverage,
        *,
        synced_at: datetime | None,
        sync_failed_at: datetime | None,
    ) -> ChartOfAccountsCoverageResponse:
        return cls(
            total=coverage.total,
            ativas=coverage.ativas,
            com_destino=coverage.com_destino,
            sem_destino=coverage.sem_destino,
            com_conta_contabil=coverage.com_conta_contabil,
            synced_at=synced_at,
            sync_failed_at=sync_failed_at,
        )


class ChartOfAccountsCoverageEnvelope(BaseModel):
    """Body de `GET .../chart-of-accounts/coverage` e de `POST .../sync`."""

    data: ChartOfAccountsCoverageResponse
