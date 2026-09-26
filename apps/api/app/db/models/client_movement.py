"""Modelo ClientMovement — a BASE DE MOVIMENTOS REALIZADOS do cliente (Sprint 12, BACK 12.1 — R0).

**Por que uma entidade nova.** O de-para (Sprint 12) precisa de uma entrada que
ainda não existia, e as três tabelas candidatas erram por construção (lido em
25/09/2026): `reconciliation_file_entries` não tem categoria;
`reconciliation_omie_entries` guarda **só** o que DIVERGIU numa conciliação (o
movimento que casou com o extrato, que é a maioria, nunca é gravado); e
`client_titles` (S11) só tem títulos EM ABERTO. Medir a cobertura do de-para
sobre qualquer uma delas seria medir sobre a coisa errada.

**Agnóstica de origem.** A chave é `(cliente, tipo de origem, identificador do
movimento na origem)`, e `source_type` é o TIPO do provedor (`omie` hoje, `arquivo`
na Sprint 14) — **nunca** uma FK para `client_connections`. Conexão é removida e
recriada ao trocar credencial; a base de movimentos (e o de-para que se apoia nela)
é do CLIENTE e não pode ir junto. Pelo mesmo motivo `source_type` não tem CHECK:
é o precedente de `ProviderType` (provedor novo entra sem migration). As colunas
que só o arquivo tem (descrição cifrada, documento) são da Sprint 14, na migration
dela — aqui não entram.

**Só CÓDIGO, nunca nome nem texto livre (§4.5).** `category_code`,
`supplier_code` e `source_account_id` são identificadores da origem. A descrição
do lançamento (`cObservacoes` no Omie) fica FORA: é texto livre de terceiro, e é
exatamente por onde nome de fornecedor entra no banco sem ninguém decidir isso. Por
consequência nada aqui é cifrado.

**Movimento não se apaga.** Sumiu da origem numa nova sincronização da mesma
competência, vira `ausente_na_origem` — a linha FICA, no mesmo padrão da carteira
(S11) e do plano de contas (S10). Reapareceu, volta a `presente`. O único `DELETE`
é o do cliente inteiro (encerramento/exclusão).

**Identificadores de terceiro são TEXTO**, inclusive `supplier_code` e
`source_account_id`, ao contrário de `client_titles` (que os guarda em
`BigInteger`). A diferença é deliberada: aquela tabela é do Omie por natureza; esta
nasce para receber também a origem por ARQUIVO, e supor `int` fecharia a porta para
um provedor que identifique fornecedor ou conta com código alfanumérico (mesma
decisão de `ProviderAccount.external_id`, S9).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MovementStatus(StrEnum):
    """Situação da linha na base — fonte ÚNICA do CHECK de `status`.

    `presente` é o que a última sincronização íntegra da competência devolveu.
    `ausente_na_origem` é o que saiu dela (apagado, estornado ou movido de mês na
    origem — a plataforma não sabe qual, e não finge saber). Só `presente` entra
    na aplicação do de-para (BACK 12.6).
    """

    PRESENTE = "presente"
    AUSENTE_NA_ORIGEM = "ausente_na_origem"


#: Teto do identificador do movimento na origem. O Omie devolve inteiro
#: (`nCodLancamento`), mas a coluna é texto (ver o docstring do módulo). 60 é o
#: mesmo teto de `client_titles.external_id` — é o mesmo tipo de identificador.
MAX_MOVEMENT_SOURCE_ID_CHARS = 60

#: Teto do tipo de origem (`omie`, `arquivo`…). Folga larga para um slug.
MAX_MOVEMENT_SOURCE_TYPE_CHARS = 30

#: Teto do código de categoria. `client_chart_of_accounts` e `client_titles` usam
#: 50 para o mesmo vocabulário; aqui vale o mesmo número de propósito — é o mesmo
#: código, e é por ele que o de-para casa.
MAX_MOVEMENT_CATEGORY_CODE_CHARS = 50

#: Teto dos identificadores de fornecedor e de conta na origem, como texto.
MAX_MOVEMENT_REF_CHARS = 60

#: A UNIQUE que torna o ciclo de sincronização idempotente (`ON CONFLICT`) — e não
#: uma leitura anterior.
UQ_CLIENT_MOVEMENT_SOURCE = "uq_client_movements_client_id_source_type_source_movement_id"

#: O índice da aplicação do de-para (12.6) e do ciclo de sincronização: ambos
#: leem "os movimentos DESTE cliente NESTA competência".
IX_CLIENT_MOVEMENT_CLIENT_COMPETENCE = "ix_client_movements_client_id_competence"

#: Rótulos (não os nomes finais) dos CHECKs — a `NAMING_CONVENTION` do `Base`
#: prefixa `ck_client_movements_`.
MOVEMENT_STATUS_CK_LABEL = "status"
MOVEMENT_COMPETENCE_CK_LABEL = "competence_first_day"
MOVEMENT_STATUS_CONSTRAINT = f"ck_client_movements_{MOVEMENT_STATUS_CK_LABEL}"
MOVEMENT_COMPETENCE_CONSTRAINT = f"ck_client_movements_{MOVEMENT_COMPETENCE_CK_LABEL}"

#: Predicado do CHECK de competência. Competência é MÊS; guardada como o dia 1
#: (`DATE`, como `reconciliation_sessions.reference_month`). O CHECK impede que um
#: `2026-06-15` gravado por engano vire uma competência que nenhuma consulta
#: `= '2026-06-01'` enxerga. A MESMA string vai na migration.
MOVEMENT_COMPETENCE_CHECK = "EXTRACT(DAY FROM competence) = 1"


def movement_status_check() -> str:
    """Predicado SQL do CHECK de `status` — a MESMA string vai na migration.

    Função (e não constante solta) pelo mesmo motivo de `title_status_check()`:
    o teste unitário compara as duas fontes, porque o autogenerate do Alembic não
    compara CHECK constraint.
    """
    valores = ", ".join(f"'{s.value}'" for s in MovementStatus)
    return f"status IN ({valores})"


class ClientMovement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_movements"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "source_type",
            "source_movement_id",
            name=UQ_CLIENT_MOVEMENT_SOURCE,
        ),
        Index(IX_CLIENT_MOVEMENT_CLIENT_COMPETENCE, "client_id", "competence"),
        CheckConstraint(text(movement_status_check()), name=MOVEMENT_STATUS_CK_LABEL),
        CheckConstraint(text(MOVEMENT_COMPETENCE_CHECK), name=MOVEMENT_COMPETENCE_CK_LABEL),
    )

    #: CASCADE porque a exclusão DEFINITIVA do cliente apaga tudo que pende dele
    #: (§4.12). O ENCERRAMENTO (que não apaga a linha de `clients`) é tratado por
    #: `close_client_purge`. Sem índice próprio: o `(client_id, competence)` serve
    #: toda query por `client_id` pelo prefixo.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: O TIPO do provedor (`ProviderType`: `omie`; `arquivo` na S14). Nunca FK de
    #: conexão — ver o docstring do módulo.
    source_type: Mapped[str] = mapped_column(String(MAX_MOVEMENT_SOURCE_TYPE_CHARS), nullable=False)

    #: O identificador do movimento NA ORIGEM (`nCodLancamento`), como texto.
    source_movement_id: Mapped[str] = mapped_column(
        String(MAX_MOVEMENT_SOURCE_ID_CHARS), nullable=False
    )

    #: A competência (mês) a que o movimento pertence, como o dia 1 do mês. É a
    #: competência DA DATA do movimento — não a pedida na sincronização: um
    #: movimento que a origem mudou de mês muda de competência no upsert.
    competence: Mapped[date] = mapped_column(SQLDate, nullable=False)

    #: A data do movimento. `Date`, não `DateTime`: é dia.
    movement_date: Mapped[date] = mapped_column(SQLDate, nullable=False)

    #: Valor COM SINAL (débito negativo, crédito positivo) — a convenção de
    #: natureza do provedor morre no adaptador. `Numeric(14,2)` como todo dinheiro
    #: do sistema (§3.4), nunca `float`.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    #: Código da categoria na origem. NULO é estado legítimo e contado: é o "sem
    #: categoria de origem" do R3 (buraco de ingestão, distinto de "sem decisão").
    category_code: Mapped[str | None] = mapped_column(
        String(MAX_MOVEMENT_CATEGORY_CODE_CHARS), nullable=True, default=None
    )

    #: Código do fornecedor/cliente no cadastro da origem — nunca a razão social.
    supplier_code: Mapped[str | None] = mapped_column(
        String(MAX_MOVEMENT_REF_CHARS), nullable=True, default=None
    )

    #: Conta da origem (`nCodCC` no Omie). Nulável: a origem por arquivo da
    #: Sprint 14 não tem conta.
    source_account_id: Mapped[str | None] = mapped_column(
        String(MAX_MOVEMENT_REF_CHARS), nullable=True, default=None
    )

    #: Valor de `MovementStatus`, travado por CHECK no banco.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MovementStatus.PRESENTE.value,
        server_default=MovementStatus.PRESENTE.value,
    )

    #: Quando ESTA linha foi vista pela última vez numa sincronização íntegra.
    #: Auditoria da linha; o estado da COMPETÊNCIA mora em
    #: `client_movement_syncs` (uma competência sem movimento nenhum deixaria o
    #: `MAX` em NULL e "vazia" viraria indistinguível de "nunca sincronizada").
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<ClientMovement client={self.client_id} source={self.source_type!r} "
            f"id={self.source_movement_id!r} competence={self.competence} "
            f"status={self.status!r}>"
        )
