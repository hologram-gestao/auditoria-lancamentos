"""Contrato HTTP dos layouts de exportação (Sprint 13, BACK 13.2 — R1).

**Duas camadas de erro (§4.8, convenção de erro do PRD).** Aqui só a FORMA: tipos,
chaves conhecidas (`extra="forbid"`), obrigatórios e TETOS de tamanho (string e
quantidade de colunas). Forma inválida → 400 `VALIDATION_ERROR` genérico do handler
global, nunca 422. Os campos semânticos (`field`, `encoding`, `separator`,
`lineEnding`, `dateFormat`, `amountFormat.*`) são `str`/`int` LIVRES de propósito: quem
decide se o valor se sustenta é `definition.parse_definition`, com 422 tipado NOMEANDO o
campo — um `Literal` aqui transformaria "campo fora do vocabulário" num 400 mudo.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.export_layout import (
    MAX_EXPORT_LAYOUT_NAME_CHARS,
    MAX_EXPORT_LAYOUT_TARGET_SYSTEM_CHARS,
)
from app.modules.export_layouts.definition import (
    MAX_COLUMNS,
    MAX_DATE_FORMAT_CHARS,
    MAX_ENCODING_CHARS,
    MAX_FIELD_CHARS,
    MAX_HEADER_CHARS,
    MAX_LINE_ENDING_CHARS,
    MAX_PREFIX_CHARS,
    MAX_SEPARATOR_CHARS,
    LayoutField,
)
from app.modules.reconciliations.schemas import SessionAuthor

if TYPE_CHECKING:
    from app.db.models import ExportLayout, ExportLayoutVersion
    from app.modules.export_layouts.definition import LayoutTemplate

_FIELDS_DOC = ", ".join(f"`{f.value}`" for f in LayoutField)


def _clean_text(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("vazio")
    return cleaned


class _Strict(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LayoutColumnPayload(_Strict):
    field: str = Field(
        max_length=MAX_FIELD_CHARS,
        description=f"Campo de origem da coluna. Vocabulário fechado: {_FIELDS_DOC}.",
    )
    header: str | None = Field(
        default=None,
        max_length=MAX_HEADER_CHARS,
        description="Rótulo da coluna no cabeçalho (só com `hasHeader`); nulo = nome do campo.",
    )


class AmountFormatPayload(_Strict):
    prefix: str = Field(
        default="", max_length=MAX_PREFIX_CHARS, description="Prefixo do valor (ex.: `R$ `)."
    )
    thousands_separator: str = Field(
        alias="thousandsSeparator",
        max_length=MAX_SEPARATOR_CHARS,
        description="Separador de milhar (vazio = sem).",
    )
    decimal_separator: str = Field(
        alias="decimalSeparator", max_length=MAX_SEPARATOR_CHARS, description="Separador decimal."
    )
    decimal_places: int = Field(alias="decimalPlaces", description="Casas decimais (2 a 4).")


class LayoutDefinitionPayload(_Strict):
    """A definição do arquivo. O valor sai SEMPRE absoluto (o sinal vira a partida)."""

    columns: list[LayoutColumnPayload] = Field(
        min_length=1, max_length=MAX_COLUMNS, description="Colunas, na ordem do arquivo."
    )
    separator: str = Field(max_length=MAX_SEPARATOR_CHARS, description="Separador de colunas.")
    has_header: bool = Field(alias="hasHeader", description="Primeira linha com os rótulos.")
    encoding: str = Field(
        max_length=MAX_ENCODING_CHARS, description="Codificação do arquivo (ex.: `latin-1`)."
    )
    line_ending: str = Field(
        alias="lineEnding",
        max_length=MAX_LINE_ENDING_CHARS,
        description="`crlf` ou `lf` — escrita também depois da última linha.",
    )
    date_format: str = Field(
        alias="dateFormat",
        max_length=MAX_DATE_FORMAT_CHARS,
        description="Formato da data com `dd`, `mm` e `aaaa`/`aa` (ex.: `dd/mm/aaaa`).",
    )
    amount_format: AmountFormatPayload = Field(alias="amountFormat")

    def to_raw(self) -> dict[str, Any]:
        """A forma camelCase que `parse_definition` valida."""
        return self.model_dump(by_alias=True)


class ExportLayoutCreate(_Strict):
    """Cria um layout (versão 1) na organização do ator (a plataforma escolhe)."""

    name: str = Field(min_length=1, max_length=MAX_EXPORT_LAYOUT_NAME_CHARS)
    target_system: str = Field(
        alias="targetSystem",
        min_length=1,
        max_length=MAX_EXPORT_LAYOUT_TARGET_SYSTEM_CHARS,
        description='Sistema contábil alvo, em texto (ex.: "Domínio").',
    )
    organization_id: UUID | None = Field(
        default=None,
        alias="organizationId",
        description=(
            "Organização dona. Obrigatória para a plataforma; o admin omite (usa a "
            "própria) ou repete a própria — outra é 403."
        ),
    )
    definition: LayoutDefinitionPayload

    @field_validator("name", "target_system")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return _clean_text(value)


class ExportLayoutVersionCreate(_Strict):
    """Nova versão (N+1) do layout — a anterior fica intacta e consultável."""

    definition: LayoutDefinitionPayload


class ExportLayoutFromTemplate(_Strict):
    """Cria o layout da organização a partir de um modelo do código, numa ação só."""

    template_key: str = Field(
        alias="templateKey",
        min_length=1,
        max_length=60,
        description="Chave do modelo (`GET /export-layout-templates`).",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_EXPORT_LAYOUT_NAME_CHARS,
        description="Nome do layout; omitido = o nome do modelo.",
    )
    organization_id: UUID | None = Field(
        default=None,
        alias="organizationId",
        description="Obrigatória para a plataforma; o admin omite ou repete a própria.",
    )

    @field_validator("name")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value)


# ------------------------------ RESPOSTAS ------------------------------


class ExportLayoutVersionItem(BaseModel):
    version: int = Field(ge=1)
    definition: dict[str, Any] = Field(
        description="A definição canônica desta versão (mesma forma do pedido)."
    )
    author: SessionAuthor
    created_at: datetime = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, version: ExportLayoutVersion, author: SessionAuthor) -> ExportLayoutVersionItem:
        return cls(
            version=version.version,
            definition=version.definition,
            author=author,
            created_at=version.created_at,
        )


class ExportLayoutItem(BaseModel):
    id: UUID
    name: str
    target_system: str = Field(alias="targetSystem")
    organization_id: UUID = Field(alias="organizationId")
    latest_version: int = Field(ge=1, alias="latestVersion")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, layout: ExportLayout, *, latest_version: int) -> ExportLayoutItem:
        return cls(
            id=layout.id,
            name=layout.name,
            target_system=layout.target_system,
            organization_id=layout.organization_id,
            latest_version=latest_version,
            created_at=layout.created_at,
            updated_at=layout.updated_at,
        )


class ExportLayoutDetail(ExportLayoutItem):
    versions: list[ExportLayoutVersionItem] = Field(
        description="Todas as versões, da mais nova para a mais antiga (só leitura)."
    )


class ExportLayoutListResponse(BaseModel):
    data: list[ExportLayoutItem]


class ExportLayoutEnvelope(BaseModel):
    data: ExportLayoutDetail


class ExportLayoutTemplateItem(BaseModel):
    key: str
    name: str
    target_system: str = Field(alias="targetSystem")
    description: str
    definition: dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, template: LayoutTemplate) -> ExportLayoutTemplateItem:
        return cls(
            key=template.key,
            name=template.name,
            target_system=template.target_system,
            description=template.description,
            definition=template.definition.to_json(),
        )


class ExportLayoutTemplateListResponse(BaseModel):
    data: list[ExportLayoutTemplateItem]
