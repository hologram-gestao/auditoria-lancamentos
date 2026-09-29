"""A definição de um layout de exportação: vocabulário FECHADO, validação e o modelo Domínio.

PURO (sem I/O, sem banco, sem relógio): consumido pela validação das rotas (13.2) E pelo
gerador do arquivo (13.3). É o ÚNICO lugar que sabe o que uma definição pode conter —
o gerador não revalida nem reinterpreta nada por conta própria.

**Vocabulário fechado de campos** (`LayoutField`): `data`, `conta_debito`,
`conta_credito`, `valor`, `historico`, `competencia`, `codigo_categoria_origem`. Uma
coluna é um campo de origem (e, com cabeçalho, o rótulo dela); o FORMATO de cada coluna
vem do tipo do campo — `data` pelo `dateFormat`, `valor` pelo `amountFormat` (sempre
absoluto), os demais como texto. Não há formato por coluna à parte: dois lugares para o
formato da data seriam duas respostas para a mesma pergunta.

**Duas camadas de erro, como a §4.8 manda.** A FORMA do JSON (tipo errado, chave
desconhecida, obrigatório ausente, lista acima do teto) é do Pydantic na borda e vira o
400 `VALIDATION_ERROR` genérico. O que precisa ORIENTAR — campo fora do vocabulário,
parâmetro que não se sustenta, codificação que o Python não conhece — é
`ExportLayoutDefinitionError` (422) com `details.field` no caminho do JSON. A validação
acontece ANTES de qualquer escrita: nada é gravado.

**A definição persiste em JSONB no formato CANÔNICO de `to_json()`** (camelCase, o mesmo
do contrato HTTP); ler de volta passa por `parse_definition` — a mesma validação da
escrita, então uma linha adulterada no banco não gera arquivo.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.exceptions import ExportLayoutDefinitionError

#: Tetos de TAMANHO da definição (a forma é recusada com 400 pelo Pydantic da borda).
MAX_COLUMNS = 20
MAX_HEADER_CHARS = 60
MAX_SEPARATOR_CHARS = 3
MAX_PREFIX_CHARS = 10
MAX_ENCODING_CHARS = 40
MAX_DATE_FORMAT_CHARS = 20
MAX_FIELD_CHARS = 40
MAX_LINE_ENDING_CHARS = 10
#: Casas decimais aceitas no valor. Fora da faixa é 422 nomeando o campo (orientar). O
#: mínimo é 2 de propósito: o dinheiro do sistema tem 2 casas (`Numeric(14,2)`), e menos
#: casas exigiria ARREDONDAR o valor que entra na contabilidade do cliente — alteração
#: silenciosa, que o gerador nunca faz. Mais casas só completam com zeros (exato).
MIN_DECIMAL_PLACES = 2
MAX_DECIMAL_PLACES = 4
#: Caracteres que, no INÍCIO de uma célula, planilhas interpretam como fórmula. Fonte
#: ÚNICA: o gerador (13.3) recusa o texto do DADO que começa com um deles, e a definição
#: recusa o texto do LAYOUT que abre célula (prefixo do valor, cabeçalho de coluna). Com
#: prefixo `=`, TODA célula de valor do arquivo viraria fórmula no Excel; com `-`, o valor
#: absoluto pareceria negativo.
FORMULA_PREFIXES = ("=", "+", "-", "@")


class LayoutField(StrEnum):
    """O vocabulário FECHADO de campos de origem de coluna — fonte ÚNICA.

    A validação da definição e o gerador (13.3) consomem ESTA enum. Um campo novo é
    código novo (e o gerador precisa saber produzi-lo), nunca cadastro.
    """

    DATA = "data"
    CONTA_DEBITO = "conta_debito"
    CONTA_CREDITO = "conta_credito"
    VALOR = "valor"
    HISTORICO = "historico"
    COMPETENCIA = "competencia"
    CODIGO_CATEGORIA_ORIGEM = "codigo_categoria_origem"


class LineEnding(StrEnum):
    """Quebra de linha do arquivo — escrita também depois da ÚLTIMA linha."""

    CRLF = "crlf"
    LF = "lf"

    @property
    def chars(self) -> str:
        return "\r\n" if self is LineEnding.CRLF else "\n"


#: Tokens do formato de data (em português, como a tela e o PRD escrevem) → strftime.
_DATE_TOKENS = {"dd": "%d", "mm": "%m", "aaaa": "%Y", "aa": "%y"}
_DATE_FORMAT_RE = re.compile(
    r"^(dd|mm|aaaa|aa)([/.\-]?)(dd|mm|aaaa|aa)\2(dd|mm|aaaa|aa)$",
)
#: Caracteres que nunca podem ser separador/prefixo: quebram a linha ou o número.
_LINE_BREAKS = ("\r", "\n")


def date_format_to_strftime(date_format: str) -> str | None:
    """`dd/mm/aaaa` → `%d/%m/%Y`. `None` se o formato não usar dia, mês e ano uma vez cada.

    Separador ÚNICO entre as três partes (`/`, `.`, `-` ou nenhum) — `dd/mm-aaaa` não é
    formato de ninguém. Ano com 4 (`aaaa`) ou 2 (`aa`) dígitos.
    """
    match = _DATE_FORMAT_RE.match(date_format)
    if match is None:
        return None
    parts = (match.group(1), match.group(3), match.group(4))
    kinds = sorted("a" if part.startswith("a") else part for part in parts)
    if kinds != ["a", "dd", "mm"]:
        return None
    sep = match.group(2)
    return sep.join(_DATE_TOKENS[part] for part in parts)


@dataclass(frozen=True, slots=True)
class AmountFormat:
    """Formato do valor: SEMPRE absoluto (o sinal é a partida, débito/crédito)."""

    prefix: str
    thousands_separator: str
    decimal_separator: str
    decimal_places: int


@dataclass(frozen=True, slots=True)
class LayoutColumn:
    field: LayoutField
    #: Rótulo da coluna no cabeçalho (só com `has_header`); `None` → o nome do campo.
    header: str | None = None

    @property
    def header_text(self) -> str:
        return self.header if self.header is not None else self.field.value


@dataclass(frozen=True, slots=True)
class LayoutDefinition:
    """Uma definição VALIDADA — só `parse_definition` a constrói a partir de JSON."""

    columns: tuple[LayoutColumn, ...]
    separator: str
    has_header: bool
    encoding: str
    line_ending: LineEnding
    date_format: str
    amount_format: AmountFormat

    @property
    def strftime(self) -> str:
        fmt = date_format_to_strftime(self.date_format)
        if fmt is None:  # pragma: no cover — parse_definition garante
            msg = f"formato de data não validado: {self.date_format!r}"
            raise ValueError(msg)
        return fmt

    def to_json(self) -> dict[str, Any]:
        """A forma CANÔNICA (camelCase, a do contrato HTTP) — o que vai para o JSONB."""
        return {
            "columns": [{"field": c.field.value, "header": c.header} for c in self.columns],
            "separator": self.separator,
            "hasHeader": self.has_header,
            "encoding": self.encoding,
            "lineEnding": self.line_ending.value,
            "dateFormat": self.date_format,
            "amountFormat": {
                "prefix": self.amount_format.prefix,
                "thousandsSeparator": self.amount_format.thousands_separator,
                "decimalSeparator": self.amount_format.decimal_separator,
                "decimalPlaces": self.amount_format.decimal_places,
            },
        }


def _invalid(field: str, user_message: str) -> ExportLayoutDefinitionError:
    return ExportLayoutDefinitionError(
        f"Definição de layout inválida em {field}.",
        user_message=user_message,
        details={"field": field},
    )


def _encodable(text: str, encoding: str) -> bool:
    try:
        text.encode(encoding, errors="strict")
    except UnicodeEncodeError:
        return False
    return True


def _check_encoding(raw: str) -> str:
    """A codificação tem de existir no Python E ser de TEXTO.

    `codecs.lookup` conhece `base64`/`rot13`/`zlib`, que não codificam `str` — por isso
    o teste de fato é codificar um texto. A grafia é normalizada (sem espaço, minúsculas)
    e persistida como foi escrita, não pelo nome interno do Python.
    """
    encoding = raw.strip().lower()
    names = "Use, por exemplo, latin-1 ou utf-8."
    if not encoding:
        raise _invalid("encoding", f"Informe a codificação do arquivo. {names}")
    try:
        codecs.lookup(encoding)
        "a".encode(encoding, errors="strict")
    except (LookupError, UnicodeError, TypeError):
        raise _invalid("encoding", f"A codificação '{encoding}' não é conhecida. {names}") from None
    return encoding


def _check_separator(separator: str, encoding: str) -> str:
    if not separator:
        raise _invalid("separator", "Informe o separador de colunas (por exemplo, ';').")
    if any(ch in separator for ch in _LINE_BREAKS):
        raise _invalid("separator", "O separador de colunas não pode ser quebra de linha.")
    if any(ch.isalnum() for ch in separator):
        raise _invalid("separator", "O separador de colunas não pode conter letra nem dígito.")
    if not _encodable(separator, encoding):
        raise _invalid("separator", "O separador de colunas não existe na codificação do layout.")
    return separator


def _check_amount_format(raw: dict[str, Any], separator: str, encoding: str) -> AmountFormat:
    prefix = str(raw.get("prefix", ""))
    thousands = str(raw.get("thousandsSeparator", ""))
    decimal = str(raw.get("decimalSeparator", ""))
    places = raw.get("decimalPlaces")

    if not isinstance(places, int) or isinstance(places, bool):
        raise _invalid("amountFormat.decimalPlaces", "Informe as casas decimais do valor.")
    if not MIN_DECIMAL_PLACES <= places <= MAX_DECIMAL_PLACES:
        raise _invalid(
            "amountFormat.decimalPlaces",
            f"As casas decimais do valor vão de {MIN_DECIMAL_PLACES} a {MAX_DECIMAL_PLACES}.",
        )
    if len(decimal) != 1 or decimal.isdigit() or decimal.isspace() or decimal in "+-":
        raise _invalid(
            "amountFormat.decimalSeparator",
            "O separador decimal é UM caractere que não seja dígito, espaço ou sinal.",
        )
    if decimal in separator:
        raise _invalid(
            "separator", "O separador de colunas não pode ser igual ao separador decimal."
        )
    if len(thousands) > 1 or thousands.isdigit() or thousands in ("+", "-", *_LINE_BREAKS):
        raise _invalid(
            "amountFormat.thousandsSeparator",
            "O separador de milhar é vazio ou UM caractere que não seja dígito nem sinal.",
        )
    if thousands and thousands == decimal:
        raise _invalid(
            "amountFormat.thousandsSeparator",
            "O separador de milhar não pode ser igual ao separador decimal.",
        )
    if thousands and thousands in separator:
        raise _invalid(
            "amountFormat.thousandsSeparator",
            "O separador de milhar não pode aparecer no separador de colunas.",
        )
    if any(ch in prefix for ch in _LINE_BREAKS) or any(ch.isdigit() for ch in prefix):
        raise _invalid(
            "amountFormat.prefix", "O prefixo do valor não pode ter dígito nem quebra de linha."
        )
    if separator in prefix:
        raise _invalid(
            "amountFormat.prefix", "O prefixo do valor não pode conter o separador de colunas."
        )
    if prefix.startswith(FORMULA_PREFIXES):
        raise _invalid(
            "amountFormat.prefix",
            "O prefixo do valor não pode começar com =, +, - ou @: planilhas leem a célula "
            "como fórmula.",
        )
    for name, text in (
        ("amountFormat.prefix", prefix),
        ("amountFormat.thousandsSeparator", thousands),
        ("amountFormat.decimalSeparator", decimal),
    ):
        if not _encodable(text, encoding):
            raise _invalid(name, "Este caractere não existe na codificação do layout.")
    return AmountFormat(
        prefix=prefix,
        thousands_separator=thousands,
        decimal_separator=decimal,
        decimal_places=places,
    )


def _check_columns(
    raw_columns: list[Any], separator: str, encoding: str
) -> tuple[LayoutColumn, ...]:
    vocabulary = ", ".join(field.value for field in LayoutField)
    columns: list[LayoutColumn] = []
    for index, raw in enumerate(raw_columns):
        raw_field = str(raw.get("field", ""))
        try:
            field = LayoutField(raw_field)
        except ValueError:
            raise _invalid(
                f"columns[{index}].field",
                f"A coluna {index + 1} usa o campo '{raw_field}', que não existe. "
                f"Campos aceitos: {vocabulary}.",
            ) from None
        header = raw.get("header")
        if header is not None:
            header = str(header)
            if (
                any(ch in header for ch in _LINE_BREAKS)
                or separator in header
                or not _encodable(header, encoding)
            ):
                raise _invalid(
                    f"columns[{index}].header",
                    f"O cabeçalho da coluna {index + 1} tem quebra de linha, o separador ou "
                    "caractere fora da codificação do layout.",
                )
            if header.startswith(FORMULA_PREFIXES):
                raise _invalid(
                    f"columns[{index}].header",
                    f"O cabeçalho da coluna {index + 1} não pode começar com =, +, - ou @: "
                    "planilhas leem a célula como fórmula.",
                )
        columns.append(LayoutColumn(field=field, header=header))
    if not columns:
        raise _invalid("columns", "O layout precisa de pelo menos uma coluna.")
    return tuple(columns)


def parse_definition(raw: dict[str, Any]) -> LayoutDefinition:
    """JSON canônico (camelCase) → definição VALIDADA, ou 422 nomeando o campo.

    Ordem das checagens (a primeira falha nomeia o erro): codificação (as outras
    checagens de caractere dependem dela) → separador → quebra de linha → formato de
    data → formato de valor → colunas. A forma (tipos, chaves) já passou pelo Pydantic
    na borda; ler do banco passa por aqui de novo.
    """
    encoding = _check_encoding(str(raw.get("encoding", "")))
    separator = _check_separator(str(raw.get("separator", "")), encoding)
    try:
        line_ending = LineEnding(str(raw.get("lineEnding", "")).strip().lower())
    except ValueError:
        raise _invalid("lineEnding", "A quebra de linha é 'crlf' ou 'lf'.") from None
    date_format = str(raw.get("dateFormat", "")).strip().lower()
    if date_format_to_strftime(date_format) is None:
        raise _invalid(
            "dateFormat",
            "O formato de data usa dd, mm e aaaa (ou aa) uma vez cada, com o mesmo "
            "separador: por exemplo, dd/mm/aaaa.",
        )
    date_match = _DATE_FORMAT_RE.match(date_format)
    date_separator = date_match.group(2) if date_match else ""
    if date_separator and date_separator in separator:
        raise _invalid(
            "dateFormat", "O separador da data não pode aparecer no separador de colunas."
        )
    amount_raw = raw.get("amountFormat")
    if not isinstance(amount_raw, dict):
        raise _invalid("amountFormat", "Informe o formato do valor.")
    amount_format = _check_amount_format(amount_raw, separator, encoding)
    raw_columns = raw.get("columns")
    if not isinstance(raw_columns, list) or not all(isinstance(c, dict) for c in raw_columns):
        raise _invalid("columns", "Informe as colunas do layout.")
    columns = _check_columns(raw_columns, separator, encoding)
    if "-" in separator and any(c.field is LayoutField.COMPETENCIA for c in columns):
        # A competência sai `AAAA-MM`: com `-` no separador, a coluna quebraria a linha.
        raise _invalid(
            "separator", "Com a coluna competencia (AAAA-MM), o separador não pode conter '-'."
        )
    return LayoutDefinition(
        columns=columns,
        separator=separator,
        has_header=bool(raw.get("hasHeader", False)),
        encoding=encoding,
        line_ending=line_ending,
        date_format=date_format,
        amount_format=amount_format,
    )


# ---------------------------------------------------------------------------
# Modelos declarados no CÓDIGO (não são linha de banco)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayoutTemplate:
    key: str
    name: str
    target_system: str
    description: str
    definition: LayoutDefinition


#: **Domínio: lançamentos contábeis (CSV)** — a tabela do layout do PRD, lida da amostra
#: real anonimizada (`tests/fixtures/accounting_sample/cliente_exemplo_2026_08/
#: lancamentos_esperados.csv`): `data;conta_debito;conta_credito;valor;historico`, `;`,
#: sem cabeçalho, Latin-1, CRLF inclusive depois da última linha, `dd/mm/aaaa`, valor
#: `R$ 5.466,87` (prefixo "R$" + espaço, milhar `.`, decimal `,`, 2 casas, absoluto).
#: Mudou no Domínio? É VERSÃO NOVA de configuração (S-1), não código.
DOMINIO_TEMPLATE = LayoutTemplate(
    key="dominio_lancamentos_csv",
    name="Domínio: lançamentos contábeis (CSV)",
    target_system="Domínio",
    description=(
        "Importador de lançamentos contábeis (partidas simples) do Domínio: data, conta "
        "débito, conta crédito, valor com R$ e histórico, separados por ponto e vírgula, "
        "sem cabeçalho, em Latin-1."
    ),
    definition=LayoutDefinition(
        columns=(
            LayoutColumn(LayoutField.DATA),
            LayoutColumn(LayoutField.CONTA_DEBITO),
            LayoutColumn(LayoutField.CONTA_CREDITO),
            LayoutColumn(LayoutField.VALOR),
            LayoutColumn(LayoutField.HISTORICO),
        ),
        separator=";",
        has_header=False,
        encoding="latin-1",
        line_ending=LineEnding.CRLF,
        date_format="dd/mm/aaaa",
        amount_format=AmountFormat(
            prefix="R$ ", thousands_separator=".", decimal_separator=",", decimal_places=2
        ),
    ),
)

#: Os modelos oferecidos (hoje, um). Segundo sistema contábil está fora do escopo da S13.
TEMPLATES: dict[str, LayoutTemplate] = {DOMINIO_TEMPLATE.key: DOMINIO_TEMPLATE}
