"""Modelo ClientConnection — as ORIGENS de dado de um cliente (Sprint 9, BACK 09.1).

Até aqui um cliente **era** um par de credenciais Omie com nome: as 4 colunas de
credencial em `clients` eram `NOT NULL` e não havia como cadastrar quem não usa
o Omie. Esta tabela desfaz esse acoplamento — o cliente passa a ser entidade
plena com **0..N** conexões tipadas, e o Omie vira a primeira implementação de
um contrato, não o único mundo possível.

**Por que uma tabela, e não mais colunas em `clients`.** Uma origem tem ciclo de
vida próprio (conecta, falha, é desligada, é removida e reconectada), estado
próprio (`status`, `last_checked_at`) e carimbo de sincronização próprio
(`accounts_synced_at`, que na 09.6 substitui `clients.omie_accounts_synced_at`).
E são N: um cliente pode ter duas contas no mesmo ERP, ou duas planilhas de
origens diferentes.

**`provider_type` é `String` + `StrEnum` no servidor, não `ENUM` do Postgres** —
mesmo padrão de `usage_events.event` e de `users.role`. Provedor novo é linha de
código, não migration; um `ENUM` de banco cobraria uma migration por provedor, e
o valor do enum é validado na borda (schemas) de qualquer forma.

**`status` TEM CHECK no banco** (`ativa` · `inativa` · `erro`), ao contrário do
tipo: o vocabulário de estado é fechado por definição e é sobre ele que a UI e a
lógica de elegibilidade decidem. `ProviderType` e `ConnectionStatus` aqui são a
fonte ÚNICA — a migration `a7f2c1d93e84` COPIA a string do CHECK e
`tests/unit/test_client_connection_schema.py` compara as duas (o autogenerate do
Alembic **não** enxerga CHECK constraint).

**Unicidade é `(client_id, provider_type, label)`** — e não `(client_id,
provider_type)`: duas conexões do mesmo tipo são legítimas, o que as distingue é
o rótulo. Remover conexão é **exclusão definitiva da linha** (a trilha vive na
auditoria, não na linha), justamente para que reconectar o mesmo tipo com o
mesmo rótulo volte a ser possível sem esbarrar na UNIQUE. Por isso esta tabela
**não** tem `deleted_at` — é a exceção consciente ao soft delete padrão do repo.

**Credenciais CIFRADAS num par só** (`credentials_encrypted`/`credentials_iv`),
guardando um JSON com as chaves do provedor. Envelope AES-256-GCM com a DEK do
cliente e AAD por linha (`field_locator(AAD_CONNECTION_CREDENTIALS, <pk>)`), IV
novo a cada operação — CLAUDE.md §4.1. Um par (e não duas colunas por provedor)
porque provedor futuro tem outro shape de credencial: `{app_key, app_secret}`
hoje, `{token}` ou `{url, user, password}` amanhã, sem coluna nova nem AAD novo.
Nulável: origem baseada em arquivo não tem segredo para guardar.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.client import Client


class ProviderType(StrEnum):
    """Tipos de origem suportados — fonte ÚNICA (proibida string mágica).

    Sem CHECK no banco de propósito (ver docstring do módulo): provedor novo
    entra aqui e nos schemas da borda, sem migration.
    """

    OMIE = "omie"


class ConnectionStatus(StrEnum):
    """Estado da conexão — fonte ÚNICA do CHECK `ck_client_connections_status`.

    `ativa` é o estado de quem opera; `inativa` é desligamento deliberado (a
    conexão fica, deixa de ser usada); `erro` é o que a verificação registrou —
    credencial recusada, provedor fora do ar — e é diferente de `inativa`
    porque exige ação, não é uma escolha do usuário.
    """

    ATIVA = "ativa"
    INATIVA = "inativa"
    ERRO = "erro"


#: Teto do rótulo, em caracteres. A validação de entrada (BACK 09.3) usa ESTE
#: número — não uma segunda cópia.
MAX_CONNECTION_LABEL_CHARS = 100

#: Nome da UNIQUE `(client_id, provider_type, label)`. A rota de criação (09.3)
#: converte a violação dela em 409 apontando a conexão existente.
UQ_CLIENT_CONNECTION_CLIENT_PROVIDER_LABEL = "uq_client_connections_client_provider_label"

#: Rótulo (não o nome final) do CHECK de status — a `NAMING_CONVENTION` do
#: `Base` prefixa `ck_client_connections_`.
CONNECTION_STATUS_CK_LABEL = "status"
CONNECTION_STATUS_CONSTRAINT = f"ck_client_connections_{CONNECTION_STATUS_CK_LABEL}"

#: Rótulo do CHECK que mantém o envelope inteiro: ciphertext e IV vivem e morrem
#: juntos. IV sem ciphertext (ou o contrário) é dado indecifrável gravado em
#: silêncio — o modo de falha que a §4.1 existe para impedir.
CONNECTION_CREDENTIALS_PAIR_CK_LABEL = "credentials_pair"
CONNECTION_CREDENTIALS_PAIR_CONSTRAINT = (
    f"ck_client_connections_{CONNECTION_CREDENTIALS_PAIR_CK_LABEL}"
)


def connection_status_check() -> str:
    """Predicado SQL do CHECK de status — a MESMA string vai na migration.

    Função (e não constante solta) pelo mesmo motivo de
    `primary_assignment_index_predicate()`: o teste unitário compara as duas
    fontes, porque o autogenerate do Alembic não compara CHECK constraint.
    """
    valores = ", ".join(f"'{s.value}'" for s in ConnectionStatus)
    return f"status IN ({valores})"


def connection_credentials_pair_check() -> str:
    """Predicado SQL do CHECK do par credencial — copiado na migration."""
    return "(credentials_encrypted IS NULL) = (credentials_iv IS NULL)"


class ClientConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_connections"

    __table_args__ = (
        # N conexões do mesmo tipo são legítimas — o rótulo é que distingue.
        UniqueConstraint(
            "client_id",
            "provider_type",
            "label",
            name=UQ_CLIENT_CONNECTION_CLIENT_PROVIDER_LABEL,
        ),
        CheckConstraint(text(connection_status_check()), name=CONNECTION_STATUS_CK_LABEL),
        CheckConstraint(
            text(connection_credentials_pair_check()),
            name=CONNECTION_CREDENTIALS_PAIR_CK_LABEL,
        ),
    )

    #: Sem índice próprio: a UNIQUE `(client_id, provider_type, label)` já serve
    #: toda busca por `client_id` pelo prefixo — mesmo raciocínio de
    #: `client_assignments.client_id`. CASCADE porque a exclusão DEFINITIVA do
    #: cliente apaga tudo que pende dele (§4.12) e uma origem não faz sentido
    #: sem o cliente.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Valor de `ProviderType`. Sem CHECK no banco (ver docstring do módulo).
    provider_type: Mapped[str] = mapped_column(String(30), nullable=False)

    #: Como o humano chama esta origem ("Omie — matriz"). Obrigatório e não
    #: vazio; o "não vazio" é 422 na borda (09.3), não CHECK — é validação de
    #: entrada, não integridade referencial.
    label: Mapped[str] = mapped_column(String(MAX_CONNECTION_LABEL_CHARS), nullable=False)

    #: Valor de `ConnectionStatus`, travado por CHECK. Nasce `ativa`: conexão só
    #: é criada com credencial aceita (09.3).
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ConnectionStatus.ATIVA.value,
        server_default=ConnectionStatus.ATIVA.value,
    )

    #: Quando a credencial foi verificada contra o provedor pela última vez.
    #: NULL = nunca verificada.
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    #: Carimbo do último sync de contas DESTA conexão (R6). Substitui, na 09.6,
    #: o `clients.omie_accounts_synced_at` — que só sabe falar de um cliente com
    #: uma origem só. Estado do sync separado das linhas do cache de propósito:
    #: provedor que devolve lista vazia deixaria um `MAX(synced_at)` em NULL e o
    #: TTL nunca dispararia (ver `clients.omie_accounts_synced_at`).
    accounts_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # ---- credencial: SEMPRE cifrada, envelope com DEK do cliente + AAD ----
    #: JSON com as chaves do provedor, cifrado inteiro. Nulável: origem por
    #: arquivo não tem segredo. O CHECK do par garante que os dois campos
    #: estejam ambos preenchidos ou ambos vazios.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    client: Mapped[Client] = relationship("Client", back_populates="connections", lazy="raise")

    def __repr__(self) -> str:
        return (
            f"<ClientConnection id={self.id} client={self.client_id} "
            f"provider={self.provider_type!r} label={self.label!r} status={self.status!r}>"
        )
