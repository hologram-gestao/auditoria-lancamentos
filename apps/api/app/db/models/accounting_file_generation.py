"""Registro de cada GERAÇÃO do arquivo contábil — só metadados (Sprint 13, BACK 13.4 — R3).

**O conteúdo do arquivo NÃO persiste.** Ele traz o histórico do de-para (dado do cliente
final) e é regenerável por construção: a materialização é imutável e a versão do layout
também. O que fica é o que explica e confere o arquivo: cliente, materialização, layout e
versão, autor, data, linhas, total e o SHA-256 do conteúdo. O download REGENERA e confere
o SHA-256 com o registrado — divergência é 409 + alerta, nunca um arquivo diferente.
Não há coluna de conteúdo, de histórico, nem nome de arquivo (o nome é derivado e não leva
nome de cliente); um teste lista as colunas.

**`client_id` desnormalizado** (§4.11): toda query filtra por ele no próprio `SELECT`, sem
depender de um JOIN com a materialização que alguém pode esquecer.

**FKs:** cliente CASCADE (a linha de `clients` só some na exclusão definitiva);
materialização RESTRICT (a exclusão definitiva apaga as gerações ANTES das
materializações e dos usuários); a VERSÃO do layout por FK COMPOSTA `(layout_id,
layout_version) → export_layout_versions(layout_id, version)` RESTRICT — a geração aponta
uma versão que existe e que não pode sumir; autor RESTRICT. No encerramento as gerações
FICAM (são "o que aconteceu", só metadados), como as materializações.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

SHA256_HEX_LENGTH = 64

IX_ACCOUNTING_FILE_GENERATION_CLIENT_MATERIALIZATION = (
    "ix_accounting_file_generations_client_id_materialization_id"
)
FK_ACCOUNTING_FILE_GENERATION_MATERIALIZATION = "fk_accounting_file_generations_materialization"
FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION = "fk_accounting_file_generations_layout_version"

#: CHECKs (rótulo → SQL). O rótulo vira `ck_accounting_file_generations_<rótulo>` pela
#: naming convention; a migration copia o par e o teste de drift compara.
ACCOUNTING_FILE_GENERATION_CHECKS: tuple[tuple[str, str], ...] = (
    ("layout_version_positive", "layout_version >= 1"),
    ("line_count_nonnegative", "line_count >= 0"),
    ("total_amount_nonnegative", "total_amount >= 0"),
    ("sha256_hex", "sha256 ~ '^[0-9a-f]{64}$'"),
    ("competence_first_day", "EXTRACT(DAY FROM competence) = 1"),
)


class AccountingFileGeneration(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "accounting_file_generations"

    __table_args__ = (
        ForeignKeyConstraint(
            ["layout_id", "layout_version"],
            ["export_layout_versions.layout_id", "export_layout_versions.version"],
            name=FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION,
            ondelete="RESTRICT",
        ),
        Index(
            IX_ACCOUNTING_FILE_GENERATION_CLIENT_MATERIALIZATION, "client_id", "materialization_id"
        ),
        *(
            CheckConstraint(text(sql), name=label)
            for label, sql in ACCOUNTING_FILE_GENERATION_CHECKS
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    materialization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "client_mapping_materializations.id",
            ondelete="RESTRICT",
            name=FK_ACCOUNTING_FILE_GENERATION_MATERIALIZATION,
        ),
        nullable=False,
    )
    layout_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    layout_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: A competência da materialização (1º dia do mês), copiada para o filtro da lista.
    competence: Mapped[date] = mapped_column(SQLDate, nullable=False)
    #: Linhas do arquivo e Σ|valor| delas (`DECIMAL(14,2)`, §3.4).
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    #: SHA-256 do CONTEÚDO (64 hex minúsculos) — a prova de qual arquivo foi gerado.
    sha256: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AccountingFileGeneration id={self.id} client={self.client_id} "
            f"materialization={self.materialization_id}>"
        )
