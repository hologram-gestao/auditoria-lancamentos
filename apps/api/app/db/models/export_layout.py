"""Layouts de exportação do arquivo contábil, POR ORGANIZAÇÃO e VERSIONADOS (Sprint 13, BACK 13.2 — R1).

**Por organização, não por cliente.** O layout é a forma do arquivo que o sistema
contábil de destino importa (Domínio, por exemplo) — configuração do ESCRITÓRIO, igual
para todos os clientes dele. Nada aqui é dado do cliente final: nenhum campo cifrado
(nenhum par de AAD novo em `crypto_service`).

**Versão imutável.** `export_layouts` guarda a identidade (organização, nome, sistema
alvo); a DEFINIÇÃO mora em `export_layout_versions`, uma linha por versão, nunca
alterada: mudar o layout grava a versão N+1 e preserva as anteriores — um arquivo já
gerado continua explicável pela versão que o gerou (a geração da 13.4 registra
`layout_id` + `layout_version`). Não há `UPDATE` de definição nem rota que apague.
`UNIQUE(layout_id, version)` é a rede da corrida pela mesma versão.

**`UNIQUE(organization_id, name)`**: dois layouts com o mesmo nome na organização
seriam indistinguíveis na tela de gerar; e o "criar a partir do modelo" clicado duas
vezes vira 409, não um segundo layout igual.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

MAX_EXPORT_LAYOUT_NAME_CHARS = 120
MAX_EXPORT_LAYOUT_TARGET_SYSTEM_CHARS = 60

UQ_EXPORT_LAYOUT_ORG_NAME = "uq_export_layouts_organization_id_name"
UQ_EXPORT_LAYOUT_VERSION = "uq_export_layout_versions_layout_id_version"
EXPORT_LAYOUT_VERSION_CK_LABEL = "version_positive"
EXPORT_LAYOUT_VERSION_CHECK = "version >= 1"


class ExportLayout(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "export_layouts"

    __table_args__ = (UniqueConstraint("organization_id", "name", name=UQ_EXPORT_LAYOUT_ORG_NAME),)

    #: RESTRICT, como `mapping_destinations`: organização não se apaga (suspende). A
    #: UNIQUE `(organization_id, name)` serve toda busca por organização pelo prefixo.
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(MAX_EXPORT_LAYOUT_NAME_CHARS), nullable=False)
    #: Sistema contábil alvo, em TEXTO ("Domínio") — segundo sistema é cadastro.
    target_system: Mapped[str] = mapped_column(
        String(MAX_EXPORT_LAYOUT_TARGET_SYSTEM_CHARS), nullable=False
    )
    #: Autoria RESTRICT (padrão da casa): quem criou não some da trilha.
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ExportLayout id={self.id} org={self.organization_id}>"


class ExportLayoutVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "export_layout_versions"

    __table_args__ = (
        UniqueConstraint("layout_id", "version", name=UQ_EXPORT_LAYOUT_VERSION),
        CheckConstraint(text(EXPORT_LAYOUT_VERSION_CHECK), name=EXPORT_LAYOUT_VERSION_CK_LABEL),
    )

    #: RESTRICT: versão é histórico — o layout não se apaga com versões (não há rota
    #: de exclusão, e a geração registrada aponta a versão).
    layout_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("export_layouts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: A definição CANÔNICA (`LayoutDefinition.to_json()`), já validada. Imutável.
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ExportLayoutVersion layout={self.layout_id} v{self.version}>"
