"""Modelo Organization — a empresa (BPO/escritório) dona de clientes e de staff.

Camada de organizações (épico 86e36ec0q, task 86e36ec7p): Plataforma →
Organizações → Clientes finais. A tabela é mínima de propósito: o nome do BPO
(dado de negócio da plataforma, em claro — a §4.5 do primer cobre dado do
cliente final vindo do Omie, não a razão social do escritório contratante),
`active` e timestamps.

A primeira organização é a **Hologram**, com id FIXO (`HOLOGRAM_ORGANIZATION_ID`):
    - a migration `3e8f1a6c9d24` a insere e faz todo cliente, usuário e
      categoria já existentes apontarem para ela — backfill "tudo é Hologram",
      comportamento idêntico ao anterior até a primeira organização nova;
    - é o `server_default` de `clients.organization_id`,
      `users.organization_id` e `client_categories.organization_id`: linha
      gravada sem o campo é a forma ANTIGA da tabela (a API antiga na janela de
      deploy, e os testes que constroem `User(...)`/`Client(...)` sem org).
      O default do banco só fala por quem não fala — o código de criação passa
      `organization_id` explicitamente, a partir da LINHA do ator (§3.15),
      nunca do payload;
    - `tests/conftest.py` a insere logo depois do `create_all`, pelo mesmo id.

Sem `closed_at`: desativar (`active=false`) derruba os usuários da organização
no request seguinte; não há encerramento com retenção por organização.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

#: Id fixo da primeira organização. A migration o insere e as colunas de org o
#: usam como `server_default`; o conftest o usa para o bootstrap do schema de
#: teste. Fixo de propósito: um default de banco precisa de um valor conhecido
#: ANTES de qualquer linha existir.
HOLOGRAM_ORGANIZATION_ID = UUID("0706eeb5-9718-4d03-bcda-ef615789e6ac")
#: Nome curto por decisão do Pedro (16/09/2026): é o que a máscara de autoria
#: "Equipe {org}" mostra ao usuário de cliente — "Equipe Hologram", como hoje.
HOLOGRAM_ORGANIZATION_NAME = "Hologram"

#: Nome do BPO — cabe numa linha de tabela e num seletor.
MAX_ORGANIZATION_NAME_CHARS = 120

#: Nome da UNIQUE de `name` (case-sensitive no banco; o service compara sem caixa,
#: como em `client_categories`).
UQ_ORGANIZATION_NAME = "uq_organizations_name"


def organization_id_server_default() -> str:
    """Literal SQL do `server_default` das colunas `organization_id`.

    Função (e não constante solta) pelo mesmo motivo de
    `primary_assignment_index_predicate()`: a migration COPIA a string e um
    teste unitário compara as duas fontes — o autogenerate compara
    `server_default`, mas só contra um banco já migrado.
    """
    return f"'{HOLOGRAM_ORGANIZATION_ID}'::uuid"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("name", name=UQ_ORGANIZATION_NAME),)

    name: Mapped[str] = mapped_column(String(MAX_ORGANIZATION_NAME_CHARS), nullable=False)
    #: `active=false` = organização suspensa: ninguém dela entra (decidido em
    #: `get_current_user`), os dados ficam. A plataforma continua enxergando.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} name={self.name!r} active={self.active}>"
