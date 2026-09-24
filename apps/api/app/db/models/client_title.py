"""Modelo ClientTitle — a CARTEIRA de títulos em aberto do cliente (Sprint 11, BACK 11.1).

**Por que a estrutura existente não serve.** Hoje o único registro de título é
`reconciliation_omie_entries`: ele nasce pendurado numa `session_id` com
`ondelete="CASCADE"`, guarda **só** os títulos sem correspondente no arquivo, e a
leitura que o alimenta (`modules/reconciliations/processing/omie_fetch.py`) pede
`conta_corrente_id=<a conta conciliada>` entre `reference_month` e o último dia
do mês. Ou seja: **uma conta, um mês, e só o que divergiu**. Um título vencido há
quatro meses nunca volta, e o que volta morre junto com a sessão. Verificado em
21/09/2026.

A carteira é o oposto: **todos** os títulos em aberto, de **todas** as contas,
sem recorte de competência, com **linha própria** que sobrevive a qualquer
sessão. É a fundação do contexto do título (Sprint 15) — e é por isso que aqui
**nada se apaga**.

**Espelho do "em aberto", não histórico.** Título que sai do aberto não vira
linha deletada: vira `liquidado` (a origem disse que foi pago/recebido) ou
`ausente_na_origem` (sumiu do cadastro sem explicação). A linha FICA porque pode
haver contexto humano apontando para ela — apagá-la transformaria um acordo
registrado num órfão silencioso. Mesma lei de `ClientChartOfAccount` (S10).

**Só CÓDIGO, nunca nome (§4.5).** `category_code` e `supplier_code` são os
códigos que a origem devolve; razão social do devedor e descrição da categoria
continuam resolvidas em runtime, pelo `clientes_cache`/`ListarCategorias` que a
aba de divergências já usa. Não existe aqui coluna de nome, de descrição nem de
`observacao` — observação é texto livre de terceiro, e é exatamente por onde nome
de pessoa entra no banco sem ninguém decidir isso.

**Os dois enums têm CHECK no banco**, pelo mesmo motivo de
`client_connections.status` e `client_chart_of_accounts.status`: o vocabulário é
fechado por definição e é sobre ele que o aging e a tela decidem. `TitleType`,
`TitleStatus`, `title_type_check()` e `title_status_check()` aqui são a fonte
ÚNICA — a migration `c2d9e7f41ab5` COPIA as strings e
`tests/unit/test_client_titles_schema.py` compara as duas (o autogenerate do
Alembic não enxerga CHECK constraint).

**`BigInteger` nos códigos numéricos da origem não é exagero.** A captura real
(`tests/fixtures/omie/listar_contas_pagar.response.json`) traz
`codigo_cliente_fornecedor = 2624256082` e `id_conta_corrente = 2617722760` —
os dois **acima** do teto de `INTEGER` (2.147.483.647). Um `Integer` aqui
estouraria na primeira sincronização real. É o mesmo tipo que
`reconciliation_omie_entries.supplier_code` já usa.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client


class TitleType(StrEnum):
    """A pagar ou a receber — fonte ÚNICA do CHECK de `title_type`.

    Duas origens diferentes (`ListarContasPagar` / `ListarContasReceber`), um
    vocabulário só. O tipo é da LINHA e não muda: um título a pagar não vira a
    receber, e a chave `(cliente, identificador)` é única por cliente inteiro,
    não por tipo — se a origem um dia repetir o identificador entre os dois
    cadastros, queremos o erro barulhento da UNIQUE, não duas linhas.
    """

    A_PAGAR = "a_pagar"
    A_RECEBER = "a_receber"


class TitleStatus(StrEnum):
    """Situação da linha na carteira — fonte ÚNICA do CHECK de `status`.

    `em_aberto` é o que a carteira existe para enxergar. Os outros dois são
    **saídas**, e são duas porque contam histórias diferentes: `liquidado` é a
    origem dizendo que o título foi pago/recebido; `ausente_na_origem` é o
    título tendo simplesmente sumido do cadastro — inclusive o caso da
    **reemissão**, em que o antigo some e outro identificador entra no lugar.
    Colapsar os dois num só faria a tela chamar de "pago" um título que ninguém
    pagou.

    Nenhum deles apaga a linha (R2).
    """

    EM_ABERTO = "em_aberto"
    LIQUIDADO = "liquidado"
    AUSENTE_NA_ORIGEM = "ausente_na_origem"


#: Os estados que **saíram** do aberto. Um lugar só: o repositório usa para
#: decidir o que marcar no fim do ciclo, e os agregados da 11.4 usam para saber
#: o que NÃO entra na soma.
CLOSED_TITLE_STATUSES = frozenset({TitleStatus.LIQUIDADO, TitleStatus.AUSENTE_NA_ORIGEM})

#: Teto do identificador do título na origem. O Omie devolve inteiro
#: (`codigo_lancamento_omie = 2624256084`), mas a coluna é `str` de propósito —
#: identificador de terceiro não é número nosso, e supor `int` fecharia a porta
#: para um provedor que use UUID ou código alfanumérico (mesma decisão de
#: `ProviderAccount.external_id`). 60 dá folga de 3x sobre um UUID.
MAX_TITLE_EXTERNAL_ID_CHARS = 60

#: Teto do código de categoria. `client_chart_of_accounts` usa 50 para o mesmo
#: vocabulário; aqui vale o mesmo número de propósito — é o mesmo código.
MAX_TITLE_CODE_CHARS = 50

#: Teto do número do documento (`numero_documento`, ex. `'00123/A'`). É
#: identificação de documento, não nome: entra.
MAX_TITLE_DOCUMENT_CHARS = 60

#: Nome da UNIQUE `(client_id, external_id)` — é ela que torna o upsert do ciclo
#: idempotente (`ON CONFLICT`), e não uma leitura anterior.
UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID = "uq_client_titles_client_id_external_id"

#: Índice que sustenta o aging (11.4) e a ordenação por vencimento (11.5). Serve
#: também toda query por `client_id` sozinho, pelo prefixo.
IX_CLIENT_TITLE_CLIENT_DUE_DATE = "ix_client_titles_client_id_due_date"

#: Rótulos (não os nomes finais) dos CHECKs — a `NAMING_CONVENTION` do `Base`
#: prefixa `ck_client_titles_`.
TITLE_TYPE_CK_LABEL = "title_type"
TITLE_STATUS_CK_LABEL = "status"
TITLE_TYPE_CONSTRAINT = f"ck_client_titles_{TITLE_TYPE_CK_LABEL}"
TITLE_STATUS_CONSTRAINT = f"ck_client_titles_{TITLE_STATUS_CK_LABEL}"


def title_type_check() -> str:
    """Predicado SQL do CHECK de `title_type` — a MESMA string vai na migration."""
    valores = ", ".join(f"'{t.value}'" for t in TitleType)
    return f"title_type IN ({valores})"


def title_status_check() -> str:
    """Predicado SQL do CHECK de `status` — a MESMA string vai na migration.

    Função (e não constante solta) pelo mesmo motivo de
    `chart_of_accounts_status_check()`: o teste unitário compara as duas fontes,
    porque o autogenerate do Alembic não compara CHECK constraint.
    """
    valores = ", ".join(f"'{s.value}'" for s in TitleStatus)
    return f"status IN ({valores})"


class ClientTitle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_titles"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "external_id",
            name=UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID,
        ),
        Index(IX_CLIENT_TITLE_CLIENT_DUE_DATE, "client_id", "due_date"),
        CheckConstraint(text(title_type_check()), name=TITLE_TYPE_CK_LABEL),
        CheckConstraint(text(title_status_check()), name=TITLE_STATUS_CK_LABEL),
    )

    #: CASCADE porque a exclusão DEFINITIVA do cliente apaga tudo que pende dele
    #: (§4.12), e carteira sem cliente não é nada. O ENCERRAMENTO (que não apaga
    #: a linha de `clients`) é tratado à parte, por `close_client_purge` —
    #: cliente encerrado não opera, então não tem carteira a operar.
    #: Sem índice próprio: `ix_client_titles_client_id_due_date` já serve toda
    #: query por `client_id` pelo prefixo.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: O identificador do título NA ORIGEM (`codigo_lancamento_omie`), como
    #: texto. Junto com `client_id` é a identidade da linha — e o alvo do
    #: `ON CONFLICT` do ciclo.
    external_id: Mapped[str] = mapped_column(String(MAX_TITLE_EXTERNAL_ID_CHARS), nullable=False)

    #: Valor de `TitleType`, travado por CHECK no banco.
    title_type: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Base do aging (R3). `Date` e não `DateTime`: vencimento é dia, e um
    #: fuso a mais aqui moveria títulos de balde.
    due_date: Mapped[date] = mapped_column(SQLDate, nullable=False)

    #: `valor_documento`. `Numeric(14,2)` como todo dinheiro do sistema (§3.4) —
    #: nunca `float`, em ponto nenhum do caminho.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    #: Valor de `TitleStatus`, travado por CHECK no banco.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TitleStatus.EM_ABERTO.value,
        server_default=TitleStatus.EM_ABERTO.value,
    )

    #: `codigo_categoria` — só o CÓDIGO (ex.: `'2.04.94'`). A descrição é
    #: resolvida em runtime pelo cache de categorias (§4.5).
    category_code: Mapped[str | None] = mapped_column(
        String(MAX_TITLE_CODE_CHARS), nullable=True, default=None
    )

    #: `codigo_cliente_fornecedor` — o código do devedor/credor no cadastro da
    #: origem, **nunca** a razão social. `BigInteger`: a captura real traz
    #: `2624256082`, acima do teto de `INTEGER`.
    supplier_code: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)

    #: `id_conta_corrente` — a conta corrente do título, **presente na resposta
    #: real** dos dois endpoints (verificado nas fixtures capturadas). Nulável
    #: porque é dado da origem e nem todo provedor terá o conceito.
    #: `BigInteger` pelo mesmo motivo de `supplier_code`.
    omie_conta_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)

    #: `numero_documento` (ex.: `'00123/A'`). Identificação do documento, não
    #: nome de pessoa — é o que permite rastrear o título na origem.
    document_number: Mapped[str | None] = mapped_column(
        String(MAX_TITLE_DOCUMENT_CHARS), nullable=True, default=None
    )

    #: Quando ESTA linha foi vista pela última vez numa sincronização íntegra.
    #: Serve à auditoria da linha; o estado da carteira INTEIRA é decidido por
    #: `clients.titles_synced_at` (ver a nota lá): um cliente sem título nenhum
    #: deixaria o `MAX` em NULL e o estado "nunca sincronizou" nunca sairia.
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    client: Mapped[Client] = relationship("Client", back_populates="titles", lazy="raise")

    def __repr__(self) -> str:
        return (
            f"<ClientTitle client={self.client_id} external={self.external_id!r} "
            f"type={self.title_type!r} due={self.due_date} status={self.status!r}>"
        )
