"""Contrato HTTP do arquivo contábil (Sprint 13, BACK 13.4 — R3).

A FORMA (competência fora de `YYYY-MM`, UUID inválido, chave desconhecida, `pageSize`
acima do teto) é o 400 `VALIDATION_ERROR` genérico. As recusas que ORIENTAM são 409
tipadas do gerador (13.3) e da entrega (sem materialização, arquivo divergente).

A resposta é SÓ METADADOS: o conteúdo do arquivo nunca persiste nem volta aqui — sai
apenas pelo download, regenerado e conferido pelo SHA-256.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.client_movements.competence import (
    COMPETENCE_PATTERN,
    format_competence,
    parse_competence,
)
from app.modules.reconciliations.schemas import SessionAuthor
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.modules.accounting_files.repository import GenerationRow


def accounting_file_name(competence: date, materialization_version: int) -> str:
    """`lancamentos_<AAAA-MM>_v<versão da MATERIALIZAÇÃO>.csv` — sem nome de cliente.

    `<versão>` é a versão da materialização do de-para (v1, v2… da competência), não a do
    layout: é ela que diz QUAL fechamento do mês o arquivo carrega (ADR-093-BE).
    """
    return f"lancamentos_{format_competence(competence)}_v{materialization_version}.csv"


class GenerateAccountingFileRequest(BaseModel):
    """Corpo de `POST /clients/{id}/accounting-files`."""

    layout_id: UUID = Field(alias="layoutId", description="Layout da organização do cliente.")
    competence: str = Field(pattern=COMPETENCE_PATTERN, description="`YYYY-MM`.")
    materialization_id: UUID | None = Field(
        default=None,
        alias="materializationId",
        description=(
            "Gera uma versão ANTERIOR explicitamente. Omitido = a ÚLTIMA materialização do "
            "destino Conta contábil na competência."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @property
    def competence_date(self) -> date:
        return parse_competence(self.competence)


class AccountingFileGenerationItem(BaseModel):
    """Uma geração registrada — só metadados."""

    id: UUID
    competence: str = Field(description="`YYYY-MM`.")
    materialization_id: UUID = Field(alias="materializationId")
    materialization_version: int = Field(alias="materializationVersion", ge=1)
    layout_id: UUID = Field(alias="layoutId")
    layout_name: str = Field(alias="layoutName")
    layout_version: int = Field(alias="layoutVersion", ge=1)
    lines: int = Field(ge=0, description="Linhas do arquivo.")
    total_amount: Decimal = Field(
        alias="totalAmount", description="Σ|valor| das linhas do arquivo (em reais)."
    )
    sha256: str = Field(description="SHA-256 do conteúdo (o download confere contra ele).")
    file_name: str = Field(alias="fileName", description="Nome do arquivo no download.")
    author: SessionAuthor
    created_at: datetime = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, row: GenerationRow, author: SessionAuthor) -> AccountingFileGenerationItem:
        g = row.generation
        return cls(
            id=g.id,
            competence=format_competence(g.competence),
            materialization_id=g.materialization_id,
            materialization_version=row.materialization_version,
            layout_id=g.layout_id,
            layout_name=row.layout_name,
            layout_version=g.layout_version,
            lines=g.line_count,
            total_amount=g.total_amount,
            sha256=g.sha256,
            file_name=accounting_file_name(g.competence, row.materialization_version),
            author=author,
            created_at=g.created_at,
        )


class AccountingFileGenerationEnvelope(BaseModel):
    data: AccountingFileGenerationItem


class AccountingFileGenerationListResponse(BaseModel):
    data: list[AccountingFileGenerationItem]
    pagination: PaginationMeta
