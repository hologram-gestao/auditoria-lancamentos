"""Contrato HTTP do mapeamento de entrada (Sprint 14, BACK 14.1).

Convenção de erro (23/09/2026, §4.8): forma inválida — enum fora do vocabulário,
coluna vazia, convenção de sinal ausente ou incoerente com os campos — é **400
`VALIDATION_ERROR`** genérico do handler global, nunca 422. Os validadores abaixo
levantam `ValueError` de propósito: é o que o handler traduz para o 400 genérico.
As MESMAS regras vivem como CHECK no banco (`client_input_mappings`): a borda
recusa cedo com a mensagem genérica, o banco recusa o que escapar.

**Nomes de coluna são estrutura, em claro.** Nada aqui carrega conteúdo de célula.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models.client_input_mapping import (
    MAX_INPUT_COLUMN_NAME_CHARS,
    MAX_NATURE_LITERAL_CHARS,
    CategoryMode,
    CsvDelimiter,
    DecimalSeparator,
    InputDateFormat,
    InputEncoding,
    InputFileFormat,
    SignConvention,
)

if TYPE_CHECKING:
    from app.db.models.client_input_mapping import ClientInputMapping


def _clean_column(value: str | None) -> str | None:
    """Nome de coluna sem espaço nas pontas; só-espaços é forma inválida."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("nome de coluna vazio")
    return cleaned


class InputMappingFields(BaseModel):
    """Os campos do mapeamento — compartilhados entre o PUT e a resposta."""

    file_format: InputFileFormat = Field(
        alias="fileFormat", description="`csv` ou `xlsx`. PDF não tem coluna para mapear."
    )
    csv_delimiter: CsvDelimiter | None = Field(
        default=None,
        alias="csvDelimiter",
        description="Só CSV — DECLARADO, nunca farejado. Obrigatório em `csv`, proibido em `xlsx`.",
    )
    encoding: InputEncoding | None = Field(
        default=None,
        description="Só CSV — codificação DECLARADA. Obrigatória em `csv`, proibida em `xlsx`.",
    )
    date_column: str = Field(
        alias="dateColumn", min_length=1, max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )
    description_column: str = Field(
        alias="descriptionColumn", min_length=1, max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )
    amount_column: str | None = Field(
        default=None,
        alias="amountColumn",
        max_length=MAX_INPUT_COLUMN_NAME_CHARS,
        description=(
            "Coluna do valor. Obrigatória em `valor_com_sinal` e `coluna_natureza`; "
            "AUSENTE em `colunas_separadas` (o valor é o par débito/crédito)."
        ),
    )
    category_column: str | None = Field(
        default=None,
        alias="categoryColumn",
        max_length=MAX_INPUT_COLUMN_NAME_CHARS,
        description=(
            "Coluna da categoria de origem. Ausente = arquivo sem categoria: toda linha "
            "nasce «sem categoria de origem» e o de-para conta o buraco."
        ),
    )
    category_mode: CategoryMode = Field(
        default=CategoryMode.COLUNA_CATEGORIA,
        alias="categoryMode",
        description=(
            "`coluna_categoria` (a coluna JÁ é a categoria) ou `classificacao_livre` "
            "(cada valor distinto da coluna vira uma categoria de origem — R4)."
        ),
    )
    account_column: str | None = Field(
        default=None, alias="accountColumn", max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )
    document_column: str | None = Field(
        default=None, alias="documentColumn", max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )
    date_format: InputDateFormat = Field(alias="dateFormat")
    decimal_separator: DecimalSeparator = Field(alias="decimalSeparator")
    sign_convention: SignConvention = Field(
        alias="signConvention",
        description=(
            "Como o arquivo diz débito e crédito. NUNCA inferida: sem ela o mapeamento "
            "é recusado (400)."
        ),
    )
    nature_column: str | None = Field(
        default=None, alias="natureColumn", max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )
    debit_value: str | None = Field(
        default=None,
        alias="debitValue",
        max_length=MAX_NATURE_LITERAL_CHARS,
        description="`coluna_natureza`: o literal que significa débito (ex.: `D`).",
    )
    credit_value: str | None = Field(
        default=None,
        alias="creditValue",
        max_length=MAX_NATURE_LITERAL_CHARS,
        description="`coluna_natureza`: o literal que significa crédito (ex.: `C`).",
    )
    debit_column: str | None = Field(
        default=None, alias="debitColumn", max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )
    credit_column: str | None = Field(
        default=None, alias="creditColumn", max_length=MAX_INPUT_COLUMN_NAME_CHARS
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator(
        "date_column",
        "description_column",
        "amount_column",
        "category_column",
        "account_column",
        "document_column",
        "nature_column",
        "debit_column",
        "credit_column",
    )
    @classmethod
    def _clean_columns(cls, value: str | None) -> str | None:
        return _clean_column(value)

    @field_validator("debit_value", "credit_value")
    @classmethod
    def _clean_literals(cls, value: str | None) -> str | None:
        """Os literais de natureza são comparados como vieram na célula, sem espaço nas pontas."""
        return _clean_column(value)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        """Espelho dos CHECKs `csv_coherent`, `category_coherent` e `sign_coherent`."""
        is_csv = self.file_format is InputFileFormat.CSV
        if is_csv and (self.csv_delimiter is None or self.encoding is None):
            raise ValueError("csv exige csvDelimiter e encoding")
        if not is_csv and (self.csv_delimiter is not None or self.encoding is not None):
            raise ValueError("xlsx não aceita csvDelimiter nem encoding")

        if self.category_mode is CategoryMode.CLASSIFICACAO_LIVRE and self.category_column is None:
            raise ValueError("classificacao_livre exige categoryColumn")

        nature = (self.nature_column, self.debit_value, self.credit_value)
        separate = (self.debit_column, self.credit_column)
        match self.sign_convention:
            case SignConvention.VALOR_COM_SINAL:
                if self.amount_column is None or any(nature) or any(separate):
                    raise ValueError("valor_com_sinal exige só amountColumn")
            case SignConvention.COLUNA_NATUREZA:
                if self.amount_column is None or not all(nature) or any(separate):
                    raise ValueError(
                        "coluna_natureza exige amountColumn, natureColumn, debitValue e creditValue"
                    )
                if self.debit_value == self.credit_value:
                    raise ValueError("debitValue e creditValue precisam ser diferentes")
            case SignConvention.COLUNAS_SEPARADAS:
                if self.amount_column is not None or any(nature) or not all(separate):
                    raise ValueError("colunas_separadas exige só debitColumn e creditColumn")
                if self.debit_column == self.credit_column:
                    raise ValueError("debitColumn e creditColumn precisam ser diferentes")
        return self


class InputMappingRequest(InputMappingFields):
    """Corpo de `PUT /api/v1/clients/{client_id}/input-mapping`.

    `extra="forbid"`: campo desconhecido é erro — um `client_id` no body nunca
    decide tenant (quem decide é a rota).
    """


class InputMappingResponse(InputMappingFields):
    """O mapeamento salvo, como a API o devolve."""

    id: UUID
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @classmethod
    def from_row(cls, row: ClientInputMapping) -> InputMappingResponse:
        return cls(
            id=row.id,
            file_format=InputFileFormat(row.file_format),
            csv_delimiter=CsvDelimiter(row.csv_delimiter) if row.csv_delimiter else None,
            encoding=InputEncoding(row.encoding) if row.encoding else None,
            date_column=row.date_column,
            description_column=row.description_column,
            amount_column=row.amount_column,
            category_column=row.category_column,
            category_mode=CategoryMode(row.category_mode),
            account_column=row.account_column,
            document_column=row.document_column,
            date_format=InputDateFormat(row.date_format),
            decimal_separator=DecimalSeparator(row.decimal_separator),
            sign_convention=SignConvention(row.sign_convention),
            nature_column=row.nature_column,
            debit_value=row.debit_value,
            credit_value=row.credit_value,
            debit_column=row.debit_column,
            credit_column=row.credit_column,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class InputMappingPayload(BaseModel):
    """`{mapping: <mapeamento> | null}` — ausente é `null` com 200, NUNCA 404.

    404 é o código anti-enumeração do tenant (§3.15); "este cliente ainda não tem
    mapeamento" é estado normal e a tela conduz a criação a partir dele.
    """

    mapping: InputMappingResponse | None = None


class InputMappingEnvelope(BaseModel):
    """Body de `GET …/input-mapping`."""

    data: InputMappingPayload


class InputMappingWritePayload(BaseModel):
    mapping: InputMappingResponse
    created: bool = Field(
        description="`true` = o cliente não tinha mapeamento; `false` = o anterior foi SUBSTITUÍDO."
    )


class InputMappingWriteEnvelope(BaseModel):
    """Body de `PUT …/input-mapping`."""

    data: InputMappingWritePayload
