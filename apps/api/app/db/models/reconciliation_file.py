"""Modelo ReconciliationFile — um arquivo (parte) de uma conciliação (Sprint 4, BACK 04.2).

**Mudança de nível do hash.** Até a Sprint 3 o hash morava na sessão
(`reconciliation_sessions.file_hash NOT NULL`) e a idempotência era
`UNIQUE(client_id, omie_conta_id, reference_month, file_hash)` — ou seja, dois
arquivos diferentes criavam DUAS sessões e não havia como consolidar uma fatura
quebrada em 3 PDFs num resumo só. A partir daqui:

    - uma conciliação = **uma conta + um mês** → `UNIQUE(client_id,
      omie_conta_id, reference_month)` na sessão (parcial, `deleted_at IS NULL`);
    - duplicata de arquivo = `UNIQUE(session_id, file_hash)` **aqui**.

O `file_hash` é SHA-256 do CONTEÚDO, recalculado no servidor em `POST /parse`
(S0/A10) — nunca o que o cliente afirma ter calculado.

**`filename` é cifrado.** Nome de arquivo é texto livre digitado por gente e
costuma carregar razão social ("Extrato Austral Junho.pdf") — CLAUDE.md §4.5
proíbe dado identificável do cliente final em claro. Mesmo envelope + AAD das
descrições de lançamento. É nullable porque as linhas do backfill (sessões
anteriores à Sprint 4) não têm nome de arquivo guardado em lugar nenhum — a UI
mostra "Arquivo N" nesses casos. Cifrar no backfill exigiria a migration
carregar as chaves do serviço (landmine conhecido), e não há dado para cifrar.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.reconciliation_session import ReconciliationSession


class ReconciliationFileStatus(StrEnum):
    """Estado de uma parte dentro da conciliação.

    `PARSED`: o arquivo foi extraído e suas linhas estão em
    `reconciliation_file_entries` (é o caso normal).

    `ERROR`: o arquivo **não** virou linhas — a extração falhou em `POST /parse`
    e o cliente registrou a parte mesmo assim, para que a tela possa dizer
    **qual** parte falhou (com o código genérico do erro) e oferecer removê-la.
    Sem esta linha, um upload de 3 PDFs em que o 2º falha vira uma conciliação
    silenciosamente incompleta — o pior desfecho possível numa auditoria.
    """

    PARSED = "parsed"
    ERROR = "error"


class ReconciliationFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_files"

    __table_args__ = (
        # Duplicata de arquivo passa a ser POR SESSÃO. Reenviar a mesma parte é
        # rejeitado; uma parte nova nunca é bloqueada por causa das anteriores.
        Index(
            "uq_recon_files_session_hash",
            "session_id",
            "file_hash",
            unique=True,
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex do conteúdo, recalculado no servidor.
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Nome do arquivo — AES-256-GCM (ver docstring do módulo). NULL nas linhas
    # do backfill e quando o cliente não informa.
    filename_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReconciliationFileStatus.PARSED.value,
    )
    # Código canônico do erro (`app.core.exceptions.ErrorCode`) quando
    # `status='error'`. Código, NUNCA a mensagem interna: a tela mostra
    # "não foi possível ler esta parte (cód. X)" — CLAUDE.md §3.7 / S2-R9.
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    session: Mapped[ReconciliationSession] = relationship(
        "ReconciliationSession", back_populates="files", lazy="raise"
    )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationFile id={self.id} session={self.session_id} "
            f"status={self.status} hash={self.file_hash[:8]}>"
        )
