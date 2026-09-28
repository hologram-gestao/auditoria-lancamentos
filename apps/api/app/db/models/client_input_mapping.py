"""Modelo ClientInputMapping — o MAPEAMENTO DE ENTRADA do arquivo do cliente (Sprint 14, BACK 14.1 — R1).

A Sprint 9 deixou o cliente existir sem ERP; a Sprint 14 dá a ele uma origem de
primeira classe: a planilha ou o extrato que ele manda todo mês. Cada cliente manda
do seu jeito, e é este mapeamento — **declarado por uma pessoa, uma vez** — que diz
ao leitor (BACK 14.3) qual coluna é a data, qual é o valor, como a data está
escrita, qual é o separador decimal e, principalmente, **qual é a convenção de
sinal**. Nada aqui é inferido: o PRD decidiu mapeamento declarativo, sem IA e sem
heurística de coluna, porque o resultado vira lançamento contábil e precisa ser
auditável e reproduzível.

**UM mapeamento por cliente** (`UNIQUE(client_id)`). É configuração, não vigência:
alterar SUBSTITUI (upsert), e o histórico do que foi lido com qual mapeamento fica
em `client_file_imports` (14.3), não aqui.

**Nomes de coluna são ESTRUTURA, não PII — ficam em claro.** "Histórico", "Valor",
"Data" descrevem o layout de um arquivo, não uma pessoa nem um fornecedor. O
conteúdo das células, esse sim, nunca chega a esta tabela (a descrição do
lançamento é cifrada na base de movimentos, 14.3).

**Todo vocabulário é `StrEnum` + CHECK copiado na migration**, no precedente de
`client_connections.status`: o autogenerate do Alembic não enxerga CHECK, então
`tests/unit/test_client_input_mapping_schema.py` compara as duas fontes.

**A coerência convenção x campos é do BANCO**, no molde de
`decision_target_coherent` (S12): `valor_com_sinal` e `coluna_natureza` exigem a
coluna de valor; `coluna_natureza` exige ainda a coluna de natureza e os dois
literais (débito/crédito); `colunas_separadas` exige as DUAS colunas de valor e
proíbe a coluna única. Decisão registrada (ADR-079-BE): a coluna de valor é
NULÁVEL porque, com débito e crédito em colunas separadas, "a coluna de valor" não
existe — o PRD lista "valor" como obrigatório e o par de colunas É o valor nessa
convenção. Mapeamento com a forma errada é 400 na borda E é recusado pelo CHECK.

**Sinal NÃO se infere** (invariante do PRD): `sign_convention` é `NOT NULL`. O
14.3 ainda checa `SINAL_NAO_DECLARADO` por defesa em profundidade, mas o banco não
aceita mapeamento sem convenção.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client


class InputFileFormat(StrEnum):
    """Formatos aceitos — fonte ÚNICA do CHECK `file_format`.

    PDF fica fora de propósito: não tem coluna para mapear (R1). O 14.3 recusa
    PDF com motivo acionável (`FORMATO_NAO_SUPORTADO`), não com este CHECK.
    """

    CSV = "csv"
    XLSX = "xlsx"


class CategoryMode(StrEnum):
    """De onde sai a categoria de origem — fonte ÚNICA do CHECK `category_mode`.

    `coluna_categoria`: a coluna apontada JÁ é a categoria do cliente.
    `classificacao_livre`: a coluna apontada é texto de classificação livre (R4 —
    "arquivo sem coluna de categoria não fica sem saída"): cada valor distinto vira
    uma categoria de origem. O registry (14.4) é agnóstico a este modo; quem
    escolhe a coluna é este mapeamento, quem lê é o 14.3.
    """

    COLUNA_CATEGORIA = "coluna_categoria"
    CLASSIFICACAO_LIVRE = "classificacao_livre"


class InputDateFormat(StrEnum):
    """Formatos de data DECLARADOS — fonte ÚNICA do CHECK `date_format`.

    Vocabulário fechado, não `strftime` livre: um padrão arbitrário vindo do
    cliente da API é superfície para erro silencioso (um `%m/%d` onde se queria
    `%d/%m` troca dia e mês sem falhar em 12 de cada 31 dias). O leitor (14.3)
    traduz cada membro para o padrão `strptime` correspondente num lugar só.
    """

    DD_MM_YYYY_SLASH = "dd/mm/yyyy"
    DD_MM_YYYY_DASH = "dd-mm-yyyy"
    YYYY_MM_DD_DASH = "yyyy-mm-dd"
    DD_MM_YY_SLASH = "dd/mm/yy"


class DecimalSeparator(StrEnum):
    """Separador decimal DECLARADO — fonte ÚNICA do CHECK `decimal_separator`."""

    COMMA = ","
    DOT = "."


class SignConvention(StrEnum):
    """Como o arquivo diz o que é débito e o que é crédito — fonte ÚNICA do CHECK.

    `valor_com_sinal`: a coluna de valor já vem negativa para saída.
    `coluna_natureza`: uma coluna carrega um literal (`D`/`C`, `Débito`/`Crédito`…)
    e o mapeamento declara QUAIS literais são débito e crédito.
    `colunas_separadas`: débito e crédito vêm em colunas distintas, sem sinal.

    **Nunca inferido** (invariante do PRD): sem convenção declarada, o arquivo não
    processa.
    """

    VALOR_COM_SINAL = "valor_com_sinal"
    COLUNA_NATUREZA = "coluna_natureza"
    COLUNAS_SEPARADAS = "colunas_separadas"


class CsvDelimiter(StrEnum):
    """Delimitador do CSV, DECLARADO (nunca farejado) — fonte ÚNICA do CHECK.

    Sem tabulação de propósito: o caractere literal dentro do predicado do CHECK
    é invisível na revisão e frágil na cópia para a migration.
    """

    SEMICOLON = ";"
    COMMA = ","
    PIPE = "|"


class InputEncoding(StrEnum):
    """Codificação do CSV, DECLARADA (nunca farejada) — fonte ÚNICA do CHECK.

    Os nomes são os codecs do Python (`codecs.lookup`), para o leitor passar o
    valor direto ao `TextIOWrapper` sem tabela de tradução.
    """

    UTF_8 = "utf-8"
    UTF_8_SIG = "utf-8-sig"
    LATIN_1 = "latin-1"
    CP1252 = "cp1252"


#: Teto do nome de uma coluna do arquivo. Nome de coluna é estrutura (em claro); o
#: teto existe para a borda recusar lixo, não por PII.
MAX_INPUT_COLUMN_NAME_CHARS = 100

#: Teto dos literais de natureza (`D`, `C`, `Débito`, `Crédito`, `Saída`…).
MAX_NATURE_LITERAL_CHARS = 30

#: A UNIQUE que faz "um mapeamento por cliente" — e o alvo do `ON CONFLICT` do
#: upsert (configuração, não vigência).
UQ_CLIENT_INPUT_MAPPING_CLIENT = "uq_client_input_mappings_client_id"

#: Rótulos (não os nomes finais) dos CHECKs — a `NAMING_CONVENTION` do `Base`
#: prefixa `ck_client_input_mappings_`.
INPUT_MAPPING_FILE_FORMAT_CK_LABEL = "file_format"
INPUT_MAPPING_CATEGORY_MODE_CK_LABEL = "category_mode"
INPUT_MAPPING_DATE_FORMAT_CK_LABEL = "date_format"
INPUT_MAPPING_DECIMAL_SEPARATOR_CK_LABEL = "decimal_separator"
INPUT_MAPPING_SIGN_CONVENTION_CK_LABEL = "sign_convention"
INPUT_MAPPING_CSV_DELIMITER_CK_LABEL = "csv_delimiter"
INPUT_MAPPING_ENCODING_CK_LABEL = "encoding"
INPUT_MAPPING_CSV_COHERENT_CK_LABEL = "csv_coherent"
INPUT_MAPPING_CATEGORY_COHERENT_CK_LABEL = "category_coherent"
INPUT_MAPPING_SIGN_COHERENT_CK_LABEL = "sign_coherent"

_TABLE = "client_input_mappings"
INPUT_MAPPING_SIGN_COHERENT_CONSTRAINT = f"ck_{_TABLE}_{INPUT_MAPPING_SIGN_COHERENT_CK_LABEL}"
INPUT_MAPPING_CSV_COHERENT_CONSTRAINT = f"ck_{_TABLE}_{INPUT_MAPPING_CSV_COHERENT_CK_LABEL}"
INPUT_MAPPING_CATEGORY_COHERENT_CONSTRAINT = (
    f"ck_{_TABLE}_{INPUT_MAPPING_CATEGORY_COHERENT_CK_LABEL}"
)


def _in_check(column: str, enum: type[StrEnum]) -> str:
    valores = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({valores})"


def _in_or_null_check(column: str, enum: type[StrEnum]) -> str:
    return f"{column} IS NULL OR {_in_check(column, enum)}"


def input_file_format_check() -> str:
    """Predicado do CHECK de `file_format` — a MESMA string vai na migration."""
    return _in_check("file_format", InputFileFormat)


def input_category_mode_check() -> str:
    """Predicado do CHECK de `category_mode` — a MESMA string vai na migration."""
    return _in_check("category_mode", CategoryMode)


def input_date_format_check() -> str:
    """Predicado do CHECK de `date_format` — a MESMA string vai na migration."""
    return _in_check("date_format", InputDateFormat)


def input_decimal_separator_check() -> str:
    """Predicado do CHECK de `decimal_separator` — a MESMA string vai na migration."""
    return _in_check("decimal_separator", DecimalSeparator)


def input_sign_convention_check() -> str:
    """Predicado do CHECK de `sign_convention` — a MESMA string vai na migration."""
    return _in_check("sign_convention", SignConvention)


def input_csv_delimiter_check() -> str:
    """Predicado do CHECK de `csv_delimiter` (nulável) — a MESMA string vai na migration."""
    return _in_or_null_check("csv_delimiter", CsvDelimiter)


def input_encoding_check() -> str:
    """Predicado do CHECK de `encoding` (nulável) — a MESMA string vai na migration."""
    return _in_or_null_check("encoding", InputEncoding)


#: CSV exige delimitador e codificação DECLARADOS; XLSX não tem os dois (o
#: container decide). Nunca farejado: um `csv.Sniffer` que acerta 95% das vezes
#: erra em silêncio nas outras 5%.
INPUT_MAPPING_CSV_COHERENT_CHECK = (
    "(file_format = 'csv' AND csv_delimiter IS NOT NULL AND encoding IS NOT NULL) "
    "OR (file_format = 'xlsx' AND csv_delimiter IS NULL AND encoding IS NULL)"
)

#: `classificacao_livre` só faz sentido apontando uma coluna. Sem coluna de
#: categoria (nula), o modo é irrelevante e toda linha nasce "sem categoria de
#: origem" — buraco de ingestão, contado pela prévia do de-para (S12 R3).
INPUT_MAPPING_CATEGORY_COHERENT_CHECK = (
    "NOT (category_mode = 'classificacao_livre' AND category_column IS NULL)"
)

#: Convenção x campos exigidos, no molde de `decision_target_coherent` (S12).
INPUT_MAPPING_SIGN_COHERENT_CHECK = (
    "(sign_convention = 'valor_com_sinal' AND amount_column IS NOT NULL "
    "AND nature_column IS NULL AND debit_value IS NULL AND credit_value IS NULL "
    "AND debit_column IS NULL AND credit_column IS NULL) "
    "OR (sign_convention = 'coluna_natureza' AND amount_column IS NOT NULL "
    "AND nature_column IS NOT NULL AND debit_value IS NOT NULL AND credit_value IS NOT NULL "
    "AND debit_column IS NULL AND credit_column IS NULL) "
    "OR (sign_convention = 'colunas_separadas' AND amount_column IS NULL "
    "AND nature_column IS NULL AND debit_value IS NULL AND credit_value IS NULL "
    "AND debit_column IS NOT NULL AND credit_column IS NOT NULL)"
)


class ClientInputMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = _TABLE

    __table_args__ = (
        UniqueConstraint("client_id", name=UQ_CLIENT_INPUT_MAPPING_CLIENT),
        CheckConstraint(text(input_file_format_check()), name=INPUT_MAPPING_FILE_FORMAT_CK_LABEL),
        CheckConstraint(
            text(input_category_mode_check()), name=INPUT_MAPPING_CATEGORY_MODE_CK_LABEL
        ),
        CheckConstraint(text(input_date_format_check()), name=INPUT_MAPPING_DATE_FORMAT_CK_LABEL),
        CheckConstraint(
            text(input_decimal_separator_check()),
            name=INPUT_MAPPING_DECIMAL_SEPARATOR_CK_LABEL,
        ),
        CheckConstraint(
            text(input_sign_convention_check()), name=INPUT_MAPPING_SIGN_CONVENTION_CK_LABEL
        ),
        CheckConstraint(
            text(input_csv_delimiter_check()), name=INPUT_MAPPING_CSV_DELIMITER_CK_LABEL
        ),
        CheckConstraint(text(input_encoding_check()), name=INPUT_MAPPING_ENCODING_CK_LABEL),
        CheckConstraint(
            text(INPUT_MAPPING_CSV_COHERENT_CHECK), name=INPUT_MAPPING_CSV_COHERENT_CK_LABEL
        ),
        CheckConstraint(
            text(INPUT_MAPPING_CATEGORY_COHERENT_CHECK),
            name=INPUT_MAPPING_CATEGORY_COHERENT_CK_LABEL,
        ),
        CheckConstraint(
            text(INPUT_MAPPING_SIGN_COHERENT_CHECK), name=INPUT_MAPPING_SIGN_COHERENT_CK_LABEL
        ),
    )

    #: CASCADE: a exclusão DEFINITIVA do cliente leva o mapeamento. O ENCERRAMENTO
    #: o remove explicitamente (`close_client_purge`): é configuração, como as
    #: decisões do de-para. Sem índice próprio — a UNIQUE já serve a busca.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ---- formato do arquivo ------------------------------------------------
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Só CSV. Declarado, nunca farejado (CHECK `csv_coherent`).
    csv_delimiter: Mapped[str | None] = mapped_column(String(1), nullable=True, default=None)
    #: Só CSV. Nome de codec do Python.
    encoding: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)

    # ---- colunas do arquivo (nomes do cabeçalho — estrutura, em claro) -------
    date_column: Mapped[str] = mapped_column(String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=False)
    description_column: Mapped[str] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=False
    )
    #: Nulável de propósito: em `colunas_separadas` o valor é o PAR
    #: débito/crédito (CHECK `sign_coherent`).
    amount_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )
    #: Nulável: arquivo sem categoria alguma. Toda linha nasce "sem categoria de
    #: origem" e o de-para conta o buraco.
    category_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )
    category_mode: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CategoryMode.COLUNA_CATEGORIA.value,
        server_default=CategoryMode.COLUNA_CATEGORIA.value,
    )
    account_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )
    document_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )

    # ---- convenções de leitura ----------------------------------------------
    date_format: Mapped[str] = mapped_column(String(20), nullable=False)
    decimal_separator: Mapped[str] = mapped_column(String(1), nullable=False)
    sign_convention: Mapped[str] = mapped_column(String(30), nullable=False)
    #: `coluna_natureza`: a coluna e os DOIS literais que ela carrega.
    nature_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )
    debit_value: Mapped[str | None] = mapped_column(
        String(MAX_NATURE_LITERAL_CHARS), nullable=True, default=None
    )
    credit_value: Mapped[str | None] = mapped_column(
        String(MAX_NATURE_LITERAL_CHARS), nullable=True, default=None
    )
    #: `colunas_separadas`: as duas colunas de valor, sem sinal.
    debit_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )
    credit_column: Mapped[str | None] = mapped_column(
        String(MAX_INPUT_COLUMN_NAME_CHARS), nullable=True, default=None
    )

    # ---- autoria -------------------------------------------------------------
    #: RESTRICT, no precedente das decisões do de-para (ADR-074-BE): quem
    #: declarou o layout é trilha. A exclusão definitiva do cliente apaga o
    #: mapeamento ANTES dos usuários do tenant (`delete_client_cascade`).
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    client: Mapped[Client] = relationship("Client", lazy="raise")

    def __repr__(self) -> str:
        return (
            f"<ClientInputMapping id={self.id} client={self.client_id} "
            f"format={self.file_format!r} sign={self.sign_convention!r}>"
        )
