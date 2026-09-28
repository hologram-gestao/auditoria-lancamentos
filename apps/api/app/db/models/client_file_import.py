"""Modelo ClientFileImport — o REGISTRO de cada arquivo processado (Sprint 14, BACK 14.3 — R3).

Uma linha por arquivo que ENTROU na base de movimentos: cliente, competência, hash
SHA-256 do conteúdo, o mapeamento aplicado, quantas linhas viraram movimento, quem
enviou e quando. É a trilha do "segundo mês em rotina" (S-1) e a lista que a tela
mostra.

**`UNIQUE(client_id, competence, file_hash)` é o 409 do reenvio, no BANCO** — não
numa leitura anterior: duas abas enviando o mesmo arquivo batem na constraint, e a
segunda recebe `ARQUIVO_JA_PROCESSADO` sem duplicar lançamento nenhum. Arquivo
CORRIGIDO (hash diferente) na mesma competência é permitido: pelo ciclo R0 as linhas
do arquivo anterior viram `ausente_na_origem` e as novas ficam `presente` — o "nunca
apaga" da S12.

**Nenhum nome de arquivo, nenhum conteúdo.** O hash identifica o conteúdo sem
guardá-lo; o nome do arquivo é texto do usuário e não é necessário para nada.

`mapping_id` é FK `SET NULL`: o mapeamento é substituído no lugar (mesma pk, upsert)
e só some no encerramento/exclusão do cliente — que leva esta tabela junto de
qualquer forma. `created_by` é RESTRICT (autoria é trilha; a exclusão definitiva
apaga o registro antes dos usuários, precedente ADR-074-BE).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

#: Tamanho do hex do SHA-256.
FILE_HASH_HEX_LENGTH = 64

#: A UNIQUE que dá o 409 do reenvio.
UQ_CLIENT_FILE_IMPORT_CLIENT_COMPETENCE_HASH = (
    "uq_client_file_imports_client_id_competence_file_hash"
)

#: Rótulo do CHECK de competência (dia 1), o MESMO predicado da base de movimentos.
FILE_IMPORT_COMPETENCE_CK_LABEL = "competence_first_day"
FILE_IMPORT_COMPETENCE_CONSTRAINT = f"ck_client_file_imports_{FILE_IMPORT_COMPETENCE_CK_LABEL}"
FILE_IMPORT_COMPETENCE_CHECK = "EXTRACT(DAY FROM competence) = 1"


class ClientFileImport(UUIDPrimaryKeyMixin, Base):
    """Um arquivo processado — append-only (sem `updated_at`)."""

    __tablename__ = "client_file_imports"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "competence",
            "file_hash",
            name=UQ_CLIENT_FILE_IMPORT_CLIENT_COMPETENCE_HASH,
        ),
        CheckConstraint(text(FILE_IMPORT_COMPETENCE_CHECK), name=FILE_IMPORT_COMPETENCE_CK_LABEL),
    )

    #: CASCADE: a exclusão DEFINITIVA leva os registros. O ENCERRAMENTO os purga
    #: explicitamente (`close_client_purge`).
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: A competência ESCOLHIDA no envio (dia 1 do mês).
    competence: Mapped[date] = mapped_column(SQLDate, nullable=False)
    #: SHA-256 (hex) do conteúdo do arquivo — identidade do conteúdo, sem guardá-lo.
    file_hash: Mapped[str] = mapped_column(String(FILE_HASH_HEX_LENGTH), nullable=False)
    #: O mapeamento aplicado. `SET NULL` quando ele deixar de existir.
    mapping_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("client_input_mappings.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    #: Quantas linhas do arquivo viraram movimento.
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Quem enviou. RESTRICT: a exclusão definitiva apaga o registro antes dos usuários.
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"<ClientFileImport id={self.id} client={self.client_id} "
            f"competence={self.competence} rows={self.rows}>"
        )
