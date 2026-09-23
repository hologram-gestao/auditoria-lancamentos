"""Modelo Client — clientes BPO da Hologram, com credenciais Omie criptografadas.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §clients.

DIVERGÊNCIA INTENCIONAL DO SCHEMA DA DOC:
    A doc lista `encryption_iv` e `encryption_tag` (1 par para todos os campos).
    Nosso `app.core.crypto`:
      - Gera IV NOVO para cada operação (regra: nunca reutilizar IV no AES-GCM).
      - Embute a tag GCM dentro do ciphertext (lib `cryptography` faz isso).
    Por isso temos:
      - omie_app_key_encrypted + omie_app_key_iv  (cada um com IV próprio)
      - omie_app_secret_encrypted + omie_app_secret_iv
    Tag não precisa de coluna: já está nos últimos 16 bytes do ciphertext.
    Esta divergência foi documentada e mantém a INTENÇÃO da doc (AES-256-GCM
    com IVs únicos), seguindo a regra inviolável CLAUDE.md §4.

Credenciais NUNCA são logadas, retornadas em response, nem armazenadas em
claro. Sempre descriptografar em memória, usar e descartar.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.organization import organization_id_server_default

if TYPE_CHECKING:
    from app.db.models.client_assignment import ClientAssignment
    from app.db.models.client_category import ClientCategory
    from app.db.models.client_chart_of_accounts import ClientChartOfAccount
    from app.db.models.client_connection import ClientConnection
    from app.db.models.omie_account_cache import OmieAccountCache
    from app.db.models.organization import Organization
    from app.db.models.reconciliation_session import ReconciliationSession
    from app.db.models.user import User


# Tamanho fixo do IV em hex (12 bytes = 24 chars hex)
IV_HEX_LENGTH = 24


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    # AES-256-GCM: ciphertext (com tag embutida) + IV próprio por campo.
    #
    # NULÁVEIS desde a Sprint 9 (BACK 09.1, migration `a7f2c1d93e84`): o cliente
    # deixou de SER um par de credenciais com nome e passou a existir sem nenhuma
    # origem conectada — a maior parte da carteira de um escritório contábil não
    # usa o Omie. As origens agora moram em `client_connections` (0..N por
    # cliente). Estas 4 colunas seguem sendo a credencial Omie dos clientes já
    # cadastrados: a conversão para a tabela nova é a 09.5 e a remoção delas é
    # `contract` de sprint seguinte — até lá, quem lê precisa tratar o `None`
    # (ver `modules/clients/omie_factory.py`).
    #
    # ⚠️ NULL e `''` são estados DIFERENTES: cliente ENCERRADO grava `''`
    # (crypto-shredding, §4.12) e continua distinguível de cliente que nunca
    # teve origem. O pré-check do downgrade da migration depende disso.
    omie_app_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    omie_app_key_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)
    omie_app_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    omie_app_secret_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    # DEK-por-cliente (Sprint 3, BACK 03.3): a Data Encryption Key deste cliente,
    # embrulhada pela KEK do KMS (envelope encryption). A DEK em claro só existe
    # em memória, pelo tempo da operação — NUNCA persiste em claro nem é logada.
    # Nullable nesta migration: clientes NOVOS já nascem com DEK; os legados
    # ganham a DEK no backfill (BACK 03.4), após o qual vira NOT-NULL efetivo.
    dek_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 86e36pm1z — encerramento com retenção: NULL = cliente aberto. Preenchido,
    # marca o cliente como ENCERRADO (terminal): nome anonimizado, credenciais
    # removidas e `dek_wrapped` destruída (crypto-shredding, §4.1) — o histórico
    # operacional (valores, datas, status) fica. Escrita em cliente encerrado é
    # recusada com 409 (`ClientClosedError`) — inclusive o provisionamento lazy
    # de DEK, que ressuscitaria a cifra de um tenant morto.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Timestamp do último sync das contas Omie (cache L1, S7).
    # NÃO derivar de MAX(omie_accounts_cache.synced_at): se o Omie devolver lista
    # vazia para um cliente, a tabela fica sem linhas e o MAX volta None — o TTL
    # nunca dispara e toda request bate o Omie. Manter o estado do sync separado
    # das linhas resolve isso (cliente sem contas continua respeitando o TTL de 24h).
    omie_accounts_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # Sprint 10 (BACK 10.1) — estado da sincronização do PLANO DE CONTAS.
    # Duas colunas, mesmo precedente de `omie_accounts_synced_at` logo acima e
    # pelo mesmo motivo: NÃO derivar de `MAX(client_chart_of_accounts.synced_at)`,
    # porque um cliente cujo cadastro de categorias está vazio deixaria o MAX em
    # NULL, o TTL de 24h nunca dispararia e toda abertura de tela bateria a
    # origem.
    #
    # São DUAS e não uma com significado duplo: a tela precisa dizer "falhou
    # agora, e a última boa foi tal dia" (R3) — um campo só escolheria entre
    # esquecer a falha ou mentir sobre o sucesso. Fonte ÚNICA do "está dentro da
    # validade": `chart_of_accounts_synced_at`. A falha NUNCA mexe nele, é o que
    # garante "falha preserva a última sincronização bem-sucedida" (R2).
    chart_of_accounts_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    chart_of_accounts_sync_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # Sprint 6 (BACK 06.2) — marcador de versão do GLOSSÁRIO deste tenant.
    # Contador incrementado na MESMA transação de qualquer escrita no glossário
    # (criação, edição E remoção). É o que permite invalidar o bloco de prompt
    # cacheado da qualificação (BACK 06.4) quando o conteúdo muda.
    # Contador, e não `MAX(updated_at)` das entradas: um delete não mexeria no
    # MAX e o cache ficaria servindo conteúdo que já não existe. Fonte ÚNICA —
    # quem incrementa é `ClientGlossaryRepository.bump_version`, mais ninguém.
    glossary_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # 86e34jd8m — categoria (nicho/segmento), UMA por cliente. Nullable: cliente
    # sem categoria é o estado inicial de todos os existentes. RESTRICT: apagar
    # categoria em uso é 409 no catálogo, nunca um "sem categoria" silencioso.
    category_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("client_categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        default=None,
    )

    # Camada de organizações (86e36ec7p): a organização DONA do cliente — é por
    # ela que `resolve_client_access` decide se um admin/manager de org alcança
    # este cliente. NOT NULL, com `server_default` = Hologram: linha gravada sem
    # o campo é a forma ANTIGA da tabela (API antiga na janela de deploy, testes
    # que constroem `Client(...)` sem org). Sem `default` no ORM de propósito:
    # o service passa a org da LINHA do ator (nunca do payload). RESTRICT: uma
    # organização com clientes não some.
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        server_default=text(organization_id_server_default()),
    )

    # Relationships
    creator: Mapped[User] = relationship("User", foreign_keys=[created_by], lazy="raise")
    category: Mapped[ClientCategory | None] = relationship("ClientCategory", lazy="raise")
    organization: Mapped[Organization] = relationship("Organization", lazy="raise")
    assignments: Mapped[list[ClientAssignment]] = relationship(
        "ClientAssignment",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    omie_accounts: Mapped[list[OmieAccountCache]] = relationship(
        "OmieAccountCache",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    # Sprint 9 (BACK 09.1): as ORIGENS de dado do cliente — 0..N, tipadas.
    connections: Mapped[list[ClientConnection]] = relationship(
        "ClientConnection",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    # Sprint 10 (BACK 10.1): o plano de contas do cliente, só códigos e flags.
    chart_of_accounts: Mapped[list[ClientChartOfAccount]] = relationship(
        "ClientChartOfAccount",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    reconciliations: Mapped[list[ReconciliationSession]] = relationship(
        "ReconciliationSession",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Client id={self.id} name={self.name!r}>"
