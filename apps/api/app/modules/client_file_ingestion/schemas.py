"""Contrato HTTP da ingestão por arquivo (Sprint 14, BACK 14.3).

Convenção de erro (§4.8): forma inválida (competência fora de `YYYY-MM`, total fora
do formato) é 400 `VALIDATION_ERROR` genérico; toda recusa do ARQUIVO é exceção
tipada com `code` e `userMessage` (R5: motivo específico e acionável).

**Nada aqui carrega conteúdo de célula além da AMOSTRA da inspeção** — que só
existe na resposta, nunca é persistida nem logada.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.client_input_mapping import InputFileFormat
from app.modules.client_movements.competence import COMPETENCE_PATTERN, format_competence
from app.modules.reconciliations.schemas import SessionAuthor

if TYPE_CHECKING:
    from app.db.models import ClientFileImport

#: Total informado no envio: decimal com até 2 casas, vírgula ou ponto, sinal opcional.
DECLARED_TOTAL_PATTERN = r"^-?\d{1,12}([.,]\d{1,2})?$"

#: A competência como campo de formulário (multipart), o MESMO padrão único.
COMPETENCE_FORM_PATTERN = COMPETENCE_PATTERN


class InspectPayload(BaseModel):
    format: InputFileFormat = Field(description="Formato DETECTADO pelo contêiner (csv/xlsx).")
    columns: list[str] = Field(description="Os nomes das colunas do cabeçalho, na ordem.")
    sample: list[list[str]] = Field(
        description=(
            "As primeiras linhas, como texto — só na resposta, nunca persistidas nem logadas."
        )
    )
    has_mapping: bool = Field(
        alias="hasMapping",
        description="`true` = o cliente já tem mapeamento salvo (a tela mostra o resumo).",
    )

    model_config = ConfigDict(populate_by_name=True)


class InspectEnvelope(BaseModel):
    data: InspectPayload


class ProcessedPayload(BaseModel):
    import_id: UUID = Field(alias="importId")
    competence: str
    rows: int = Field(ge=0, description="Linhas do arquivo que viraram movimento.")
    columns_recognized: int = Field(
        ge=0, alias="columnsRecognized", description="Colunas do mapeamento encontradas."
    )
    categories_created: int = Field(
        ge=0,
        alias="categoriesCreated",
        description="Categorias de origem NOVAS registradas a partir deste arquivo (R4).",
    )
    absent: int = Field(
        ge=0,
        description=(
            "Movimentos de arquivos anteriores da MESMA competência que este arquivo não "
            "trouxe — marcados `ausente_na_origem`, nunca apagados."
        ),
    )
    mapping_id: UUID = Field(alias="mappingId")
    processed_at: datetime = Field(alias="processedAt")

    model_config = ConfigDict(populate_by_name=True)


class ProcessedEnvelope(BaseModel):
    data: ProcessedPayload


class FileImportItem(BaseModel):
    id: UUID
    competence: str
    rows: int = Field(ge=0)
    file_hash: str = Field(alias="fileHash", description="SHA-256 do conteúdo (hex).")
    mapping_id: UUID | None = Field(default=None, alias="mappingId")
    processed_at: datetime = Field(alias="processedAt")
    author: SessionAuthor

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_row(cls, record: ClientFileImport, author: SessionAuthor) -> FileImportItem:
        return cls(
            id=record.id,
            competence=format_competence(record.competence),
            rows=record.rows,
            file_hash=record.file_hash,
            mapping_id=record.mapping_id,
            processed_at=record.processed_at,
            author=author,
        )


class FileImportListResponse(BaseModel):
    data: list[FileImportItem]
