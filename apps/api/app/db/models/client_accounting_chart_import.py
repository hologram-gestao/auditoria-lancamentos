"""Modelo ClientAccountingChartImport — o REGISTRO de cada importação do plano contábil.

Épico 86e3ydjy9 (86e3ydjze, Tract em 03/11/2026). Uma linha por importação que ENTROU:
quem importou, quando, de que layout, as contagens e as linhas que ficaram DE FORA por
escolha confirmada na prévia (linha com problema não derruba mais o arquivo inteiro). A
tela do plano lê a ÚLTIMA: com linha de fora, um selo vermelho diz que o plano importado
não está 100% e quais linhas faltaram; importação seguinte sem linha de fora apaga o selo.

**Nenhum nome, nenhuma célula** (§4.5): `excluded_lines` guarda só `{linha, motivo}`, com
o número da linha física da planilha e o motivo no vocabulário FECHADO do leitor
(`sheet.ChartLineReason` e `dominio.DominioLineReason`). Nada é cifrado porque nada aqui
é dado do cliente final.

`imported_by` é RESTRICT (autoria é trilha; a exclusão definitiva apaga o registro antes
dos usuários do tenant, molde de `client_file_imports`). O encerramento purga a tabela
explicitamente (`close_client_purge`, §4.12).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

#: Largura da coluna `layout` (`modelo`, `dominio`, `reconhecido`, `mapeado`).
MAX_CHART_IMPORT_LAYOUT_CHARS = 20

#: Os CHECKs, copiados na migration (o teste compara as duas fontes).
CHART_IMPORT_COUNTS_CK_LABEL = "counts_non_negative"
CHART_IMPORT_COUNTS_CHECK = (
    "accounts >= 0 AND new_accounts >= 0 AND updated_accounts >= 0 "
    "AND absent_accounts >= 0 AND excluded_count >= 0"
)
CHART_IMPORT_EXCLUDED_CK_LABEL = "excluded_lines_count"
CHART_IMPORT_EXCLUDED_CHECK = (
    "jsonb_typeof(excluded_lines) = 'array' AND excluded_count = jsonb_array_length(excluded_lines)"
)
#: A última importação do cliente é a que a tela lê.
CHART_IMPORT_LATEST_INDEX = "ix_client_accounting_chart_imports_client_latest"


class ClientAccountingChartImport(UUIDPrimaryKeyMixin, Base):
    """Uma importação do plano contábil — append-only (sem `updated_at`)."""

    __tablename__ = "client_accounting_chart_imports"

    __table_args__ = (
        CheckConstraint(text(CHART_IMPORT_COUNTS_CHECK), name=CHART_IMPORT_COUNTS_CK_LABEL),
        CheckConstraint(text(CHART_IMPORT_EXCLUDED_CHECK), name=CHART_IMPORT_EXCLUDED_CK_LABEL),
        Index(CHART_IMPORT_LATEST_INDEX, "client_id", "imported_at"),
    )

    #: CASCADE: a exclusão DEFINITIVA leva os registros. O ENCERRAMENTO os purga
    #: explicitamente (`close_client_purge`).
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    #: Quem importou. RESTRICT: a exclusão definitiva apaga o registro antes dos usuários.
    imported_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    layout: Mapped[str] = mapped_column(String(MAX_CHART_IMPORT_LAYOUT_CHARS), nullable=False)
    accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    new_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    absent_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `[{"linha": int, "motivo": str}]` — só número e vocabulário fechado, nunca célula.
    excluded_lines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    def __repr__(self) -> str:
        return (
            f"<ClientAccountingChartImport id={self.id} client={self.client_id} "
            f"accounts={self.accounts} excluded={self.excluded_count}>"
        )
