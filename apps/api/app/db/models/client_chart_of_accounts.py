"""Modelo ClientChartOfAccount — o PLANO DE CONTAS do cliente (Sprint 10, BACK 10.1).

Até aqui a plataforma **lia** as categorias do cliente (`listar_categorias` +
`OmieCategoriasCache`, TTL de 6h em memória) só para resolver código → descrição
na tela de revisão. O vínculo que cada categoria já carrega com a **conta de
demonstrativo** não era sequer declarado no DTO — e é justamente ele o insumo do
de-para da Sprint 12. Na amostra real, **37 de 50 categorias** chegam com esse
vínculo preenchido pela origem: trabalho já feito, que estávamos jogando fora.

**Por que o nome não é `client_categories*`.** `client_categories` já existe e é
outra coisa: o catálogo de RÓTULOS de clientes por organização ("Fintech",
"Varejo"), governado por `MANAGE_CLIENT_CATEGORIES` e pela tela "Categorias de
Cliente". Nenhum repositório, serviço ou permissão daquele módulo é reusado aqui.

**Só CÓDIGO, situação e flags — nunca nome.** O primer §4.5 mantém
nomes/descrições de categorias e de contas fora do disco em claro: eles seguem
resolvidos em runtime pelo cache de 6h que já existe, e a tela do R3 exibe o nome
vindo de lá. Por isso **não** existem aqui colunas para `descricao`,
`descricaoDRE` nem `tag_conta_contabil` — este último é o rótulo livre da conta
contábil, e rótulo é nome. O DTO (`CategoriaOmie`) carrega os três porque o
contrato precisa declará-los; a persistência para no código.

Consequência desejada: **nada aqui morre no crypto-shredding** (§4.12) porque
nada aqui é cifrado — não há PII. Ainda assim a tabela entra em
`close_client_purge`: é configuração do cliente final, e cliente encerrado não
tem plano de contas a operar.

**Estado, não deleção.** Categoria que some da origem vira
`ausente_na_origem`, nunca uma linha apagada: pode existir de-para (Sprint 12)
apontando para ela, e apagar a linha transformaria um vínculo humano em órfão
silencioso. É a invariante "categoria não se apaga" do PRD.

**`status` TEM CHECK no banco**, pelo mesmo motivo de `client_connections.status`:
o vocabulário de estado é fechado por definição e é sobre ele que a cobertura e a
tela decidem. `ChartOfAccountsStatus` e `chart_of_accounts_status_check()` aqui
são a fonte ÚNICA — a migration `f1b7a52c8e60` COPIA a string e
`tests/unit/test_chart_of_accounts_schema.py` compara as duas (o autogenerate do
Alembic não enxerga CHECK constraint).

**`inativa` não é coluna booleana** (ao contrário do que a sugestão de desenho da
task listava): ela JÁ é um valor de `status`. Ter as duas seria o mesmo fato em
dois lugares, com um `UPDATE` capaz de deixá-las discordando — e a §7 do primer
exige um único lugar por valor derivado. Ver `.claude/memory/decisions.md`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client


class ChartOfAccountsStatus(StrEnum):
    """Situação da linha do plano de contas — fonte ÚNICA do CHECK.

    `ativa`/`inativa` espelham o `conta_inativa` da origem no momento da última
    sincronização. `ausente_na_origem` é o terceiro estado que só esta camada
    conhece: a categoria existia, sumiu do cadastro do cliente, e a linha FICA —
    marcada, nunca apagada.
    """

    ATIVA = "ativa"
    INATIVA = "inativa"
    AUSENTE_NA_ORIGEM = "ausente_na_origem"


#: Teto dos códigos. A origem devolve códigos de plano de contas como
#: `'1.01.01'` e conta contábil como `'3.1.3.01.00003'`; 50 dá folga de uma
#: ordem de grandeza sobre o maior visto na fixture real (14 caracteres).
MAX_ACCOUNT_CODE_CHARS = 50

#: Nome da UNIQUE `(client_id, category_code)` — é ela que torna o upsert da
#: 10.2 idempotente (`ON CONFLICT`), e não uma leitura anterior.
UQ_CHART_OF_ACCOUNTS_CLIENT_CODE = "uq_client_chart_of_accounts_client_code"

#: Rótulo (não o nome final) do CHECK de status — a `NAMING_CONVENTION` do
#: `Base` prefixa `ck_client_chart_of_accounts_`.
CHART_OF_ACCOUNTS_STATUS_CK_LABEL = "status"
CHART_OF_ACCOUNTS_STATUS_CONSTRAINT = (
    f"ck_client_chart_of_accounts_{CHART_OF_ACCOUNTS_STATUS_CK_LABEL}"
)


def chart_of_accounts_status_check() -> str:
    """Predicado SQL do CHECK de status — a MESMA string vai na migration.

    Função (e não constante solta) pelo mesmo motivo de
    `connection_status_check()`: o teste unitário compara as duas fontes,
    porque o autogenerate do Alembic não compara CHECK constraint.
    """
    valores = ", ".join(f"'{s.value}'" for s in ChartOfAccountsStatus)
    return f"status IN ({valores})"


class ClientChartOfAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_chart_of_accounts"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "category_code",
            name=UQ_CHART_OF_ACCOUNTS_CLIENT_CODE,
        ),
        CheckConstraint(
            text(chart_of_accounts_status_check()),
            name=CHART_OF_ACCOUNTS_STATUS_CK_LABEL,
        ),
    )

    #: Sem índice próprio: a UNIQUE `(client_id, category_code)` já serve toda
    #: query por `client_id` pelo prefixo — mesmo raciocínio de
    #: `client_connections.client_id`. CASCADE porque a exclusão DEFINITIVA do
    #: cliente apaga tudo que pende dele (§4.12), e plano de contas sem cliente
    #: não é nada.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: `codigo` da categoria na origem — a identidade da linha dentro do tenant.
    category_code: Mapped[str] = mapped_column(String(MAX_ACCOUNT_CODE_CHARS), nullable=False)

    #: `categoria_superior`, já normalizado: a raiz (`'0'` na origem) vira NULL,
    #: para que todo valor não-nulo seja um `category_code` de verdade.
    parent_code: Mapped[str | None] = mapped_column(
        String(MAX_ACCOUNT_CODE_CHARS), nullable=True, default=None
    )

    #: `dadosDRE.codigoDRE` — a CONTA DE DEMONSTRATIVO, o insumo do de-para.
    #: NULL = **"sem destino declarado"**, que é informação (transferências e
    #: totalizadoras não têm conta de demonstrativo própria), não buraco a
    #: preencher: destino NUNCA é inferido.
    dre_code: Mapped[str | None] = mapped_column(
        String(MAX_ACCOUNT_CODE_CHARS), nullable=True, default=None
    )
    #: `dadosDRE.nivelDRE` — profundidade na árvore do demonstrativo. Número
    #: JSON na resposta real, não string.
    dre_level: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    #: `dadosDRE.sinalDRE` — `'+'` ou `'-'`. Um caractere; sem CHECK, porque a
    #: origem pode inventar um terceiro valor e recusar a sincronização inteira
    #: por causa disso seria pior do que guardá-lo.
    dre_sign: Mapped[str | None] = mapped_column(String(1), nullable=True, default=None)

    #: `id_conta_contabil` — só o CÓDIGO. A `tag_conta_contabil` (rótulo livre)
    #: fica de fora: é nome de conta, §4.5.
    conta_contabil_code: Mapped[str | None] = mapped_column(
        String(MAX_ACCOUNT_CODE_CHARS), nullable=True, default=None
    )

    #: Flags da origem, já traduzidas de `'S'`/`'N'`. `NOT NULL` com default
    #: `false`: ausente na origem significa "não é", e o DTO já resolve isso.
    totalizadora: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    transferencia: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    nao_exibir: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Valor de `ChartOfAccountsStatus`, travado por CHECK no banco.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ChartOfAccountsStatus.ATIVA.value,
        server_default=ChartOfAccountsStatus.ATIVA.value,
    )

    #: Quando ESTA linha foi vista pela última vez numa sincronização. Serve à
    #: auditoria da linha; o TTL de 24h da sincronização é decidido por
    #: `clients.chart_of_accounts_synced_at`, que continua existindo mesmo
    #: quando o cliente não tem nenhuma categoria (ver a nota lá).
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    client: Mapped[Client] = relationship(
        "Client", back_populates="chart_of_accounts", lazy="raise"
    )

    def __repr__(self) -> str:
        return (
            f"<ClientChartOfAccount client={self.client_id} "
            f"code={self.category_code!r} dre={self.dre_code!r} status={self.status!r}>"
        )
