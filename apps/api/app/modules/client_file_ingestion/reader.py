"""Leitor DETERMINÍSTICO do arquivo do cliente — CSV e XLSX, pelo mapeamento (Sprint 14, BACK 14.3).

⚠️ **Não é a ingestão em blocos da conciliação.** Aquela existe para fatiar texto em
chamadas à IA; aqui o caminho precisa ser auditável e reproduzível: a mesma planilha
com o mesmo mapeamento produz sempre as mesmas linhas. Nada é inferido — o formato de
data, o separador decimal, o delimitador, a codificação e a convenção de sinal vêm
DECLARADOS no mapeamento (14.1).

**Custo limitado ANTES de iterar** (lição da S12, ADR-078-BE): bytes pelo
`Settings.max_upload_bytes` (na rota, em streaming), orçamento do zip (tamanho
descomprimido e razão de compressão) antes do openpyxl, colunas por linha, linhas
PERCORRIDAS (vazias inclusive — o openpyxl `read_only` emite uma linha vazia por
buraco até a próxima do XML) e linhas de dados. Tudo constante declarada aqui.

**Qualquer falha de abertura OU de iteração é a MESMA recusa tipada
`ARQUIVO_INVALIDO`, com mensagem fixa e `raise … from None`**: a exceção original
carrega o texto da célula, e o handler de 500 logaria `exc_info`. Só `AppError`
nossa atravessa (o cabeçalho divergente, por exemplo).

**O único pré-processamento de célula é `strip()` de texto** (e `None` para célula
vazia). Está documentado aqui porque é o que o registry de categorias (14.4) assume
ao casar rótulos byte a byte: `" Aluguel "` e `"Aluguel"` são a MESMA categoria por
causa desta linha, e `"Aluguel"` e `"aluguel"` continuam sendo duas.

**Parse é CPU síncrona**: quem está num handler `async` chama via `run_in_threadpool`.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

from openpyxl import load_workbook

from app.core.exceptions import AppError, FileFormatNotSupportedError, FileInvalidError
from app.db.models.client_input_mapping import (
    CategoryMode,
    DecimalSeparator,
    InputDateFormat,
    InputFileFormat,
    SignConvention,
)
from app.db.models.client_movement import (
    MAX_MOVEMENT_DOCUMENT_CHARS,
    MAX_MOVEMENT_REF_CHARS,
    MOVEMENT_AMOUNT_PRECISION,
    MOVEMENT_AMOUNT_SCALE,
)
from app.utils.magic_bytes import FileType, detect_file_type

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Sequence

    from app.db.models.client_input_mapping import ClientInputMapping

# --- Limites DECLARADOS (o teto de bytes é `Settings.max_upload_bytes`, na rota) ----
#: Linhas de DADOS. Uma planilha mensal de cliente pequeno tem dezenas/centenas de
#: lançamentos; 10.000 cobre um extrato movimentado com folga de uma ordem.
MAX_FILE_ROWS = 10_000
#: Linhas PERCORRIDAS, vazias inclusive (célula perdida na linha 300.000 custava
#: minutos de CPU síncrona, S12). O dobro deixa folga para linhas em branco.
MAX_FILE_SCANNED_ROWS = 2 * MAX_FILE_ROWS
#: Colunas lidas por linha. Um extrato tem meia dúzia; 64 é folga sem montar 16.384
#: posições por linha por causa de uma célula na coluna XFD.
MAX_FILE_COLUMNS = 64
#: Teto do conteúdo DESCOMPRIMIDO do zip do XLSX (soma de `file_size`).
MAX_FILE_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
#: Razão máxima de compressão por entrada do zip. XML comprime ~10-20x; acima de
#: 100x é arquivo fabricado.
MAX_FILE_COMPRESSION_RATIO = 100
#: Linhas da AMOSTRA da inspeção (R5: "amostra das primeiras linhas").
SAMPLE_ROWS = 5
#: Teto de linhas inválidas RELATADAS em `details.lines` (o total vai à parte).
MAX_INVALID_LINES_REPORTED = 50
#: Teto da descrição (texto cifrado; sem coluna limitada no banco).
MAX_DESCRIPTION_CHARS = 500
#: Teto do rótulo de categoria (texto cifrado no registry).
MAX_CATEGORY_LABEL_CHARS = 200
#: Assinatura de todo `.xlsx` (é um ZIP).
XLSX_MAGIC = b"PK\x03\x04"

#: Motivos de linha inválida — vocabulário FECHADO (nunca texto da célula).
type LineReason = Literal[
    "valor_nao_numerico",
    "data_invalida",
    "campo_obrigatorio_vazio",
    "natureza_desconhecida",
    "data_fora_da_competencia",
    "campo_longo_demais",
]

#: `InputDateFormat` → padrão `strptime`. Um lugar só (o mapeamento declara o
#: membro do enum; o leitor traduz aqui).
_STRPTIME: dict[InputDateFormat, str] = {
    InputDateFormat.DD_MM_YYYY_SLASH: "%d/%m/%Y",
    InputDateFormat.DD_MM_YYYY_DASH: "%d-%m-%Y",
    InputDateFormat.YYYY_MM_DD_DASH: "%Y-%m-%d",
    InputDateFormat.DD_MM_YY_SLASH: "%d/%m/%y",
}

_CENT = Decimal("0.01")

#: Teto de |valor|, DERIVADO da coluna (`client_movements.amount`, `Numeric(14,2)`):
#: 10^(14-2). Derivado e não digitado — mudar a precisão da coluna muda o teto aqui.
MAX_AMOUNT_ABS = Decimal(10) ** (MOVEMENT_AMOUNT_PRECISION - MOVEMENT_AMOUNT_SCALE)

#: Formato ESTRITO do valor em texto por separador declarado (depois de tirar `R$`,
#: espaços e parênteses): inteiro puro OU milhar em grupos de 3 no separador oposto,
#: e até 2 casas no declarado. Tudo o mais é linha inválida — nunca "consertado".
_AMOUNT_TEXT_PATTERNS: dict[DecimalSeparator, re.Pattern[str]] = {
    DecimalSeparator.COMMA: re.compile(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?"),
    DecimalSeparator.DOT: re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"),
}


@dataclass(frozen=True, slots=True)
class MappingSpec:
    """O mapeamento como o leitor o consome — congelado, sem ORM (testável puro)."""

    file_format: InputFileFormat
    csv_delimiter: str | None
    encoding: str | None
    date_column: str
    description_column: str
    amount_column: str | None
    category_column: str | None
    category_mode: CategoryMode
    account_column: str | None
    document_column: str | None
    date_format: InputDateFormat
    decimal_separator: DecimalSeparator
    sign_convention: SignConvention | None
    nature_column: str | None
    debit_value: str | None
    credit_value: str | None
    debit_column: str | None
    credit_column: str | None

    @classmethod
    def from_row(cls, row: ClientInputMapping) -> MappingSpec:
        return cls(
            file_format=InputFileFormat(row.file_format),
            csv_delimiter=row.csv_delimiter,
            encoding=row.encoding,
            date_column=row.date_column,
            description_column=row.description_column,
            amount_column=row.amount_column,
            category_column=row.category_column,
            category_mode=CategoryMode(row.category_mode),
            account_column=row.account_column,
            document_column=row.document_column,
            date_format=InputDateFormat(row.date_format),
            decimal_separator=DecimalSeparator(row.decimal_separator),
            sign_convention=SignConvention(row.sign_convention) if row.sign_convention else None,
            nature_column=row.nature_column,
            debit_value=row.debit_value,
            credit_value=row.credit_value,
            debit_column=row.debit_column,
            credit_column=row.credit_column,
        )

    @property
    def mapped_columns(self) -> list[str]:
        """Toda coluna que o mapeamento aponta — TODAS precisam existir no cabeçalho."""
        names = [
            self.date_column,
            self.description_column,
            self.amount_column,
            self.category_column,
            self.account_column,
            self.document_column,
            self.nature_column,
            self.debit_column,
            self.credit_column,
        ]
        seen: list[str] = []
        for name in names:
            if name is not None and name not in seen:
                seen.append(name)
        return seen


@dataclass(frozen=True, slots=True)
class ReadOptions:
    file_format: InputFileFormat
    csv_delimiter: str
    encoding: str


@dataclass(frozen=True, slots=True)
class ParsedTable:
    """Cabeçalho + linhas `(número da linha no arquivo, células)`; `scanned` percorridas."""

    columns: list[str]
    rows: list[tuple[int, list[Any]]]
    scanned: int


@dataclass(frozen=True, slots=True)
class LineProblem:
    line: int
    reason: LineReason


@dataclass(frozen=True, slots=True)
class ParsedLine:
    """Uma linha VÁLIDA, já convertida pelo mapeamento. Texto em memória, nunca em log."""

    line: int
    entry_date: date
    amount: Decimal
    description: str
    category_label: str | None
    account: str | None
    document: str | None


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------


def detect_format(content: bytes) -> InputFileFormat:
    """CSV ou XLSX pelos magic bytes; PDF, XLS e desconhecido são recusados COM motivo.

    Detecção é do CONTÊINER (zip x texto), nunca do conteúdo: delimitador e
    codificação do CSV continuam declarados no mapeamento.
    """
    kind = detect_file_type(content)
    if kind is FileType.XLSX:
        return InputFileFormat.XLSX
    if kind is FileType.CSV:
        return InputFileFormat.CSV
    if kind is FileType.PDF:
        raise FileFormatNotSupportedError(
            "arquivo PDF",
            user_message="PDF não tem coluna para mapear. Envie o arquivo em CSV ou XLSX.",
        )
    if kind is FileType.XLS:
        raise FileFormatNotSupportedError(
            "arquivo XLS (formato antigo)",
            user_message=(
                "O formato XLS (Excel antigo) não é aceito. Salve a planilha como XLSX ou CSV."
            ),
        )
    raise FileFormatNotSupportedError("formato desconhecido")


def assert_format_matches(detected: InputFileFormat, declared: InputFileFormat) -> None:
    """O arquivo precisa ser do formato que o mapeamento declara — nunca adivinhar."""
    if detected is not declared:
        raise FileFormatNotSupportedError(
            f"arquivo {detected.value}, mapeamento {declared.value}",
            user_message=(
                f"O mapeamento deste cliente é para {declared.value.upper()}, e o arquivo "
                f"enviado é {detected.value.upper()}. Envie no formato do mapeamento ou "
                "altere o mapeamento."
            ),
        )


# ---------------------------------------------------------------------------
# Leitura (CPU síncrona)
# ---------------------------------------------------------------------------


def _invalid(message: str) -> FileInvalidError:
    return FileInvalidError(f"arquivo do cliente inválido: {message}")


def _header_text(value: Any) -> str:
    """Nome de coluna: texto aparado. Número no cabeçalho vira texto."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _cell(value: Any) -> Any:
    """O ÚNICO pré-processamento de célula: texto aparado; vazio vira `None`.

    Datas e números do XLSX chegam como objetos e seguem como estão — a conversão
    é do mapeamento (`convert_lines`).
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _check_zip_budget(content: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        total = 0
        for info in archive.infolist():
            total += info.file_size
            if total > MAX_FILE_UNCOMPRESSED_BYTES:
                raise _invalid("descomprimido grande demais")
            if info.file_size > MAX_FILE_COMPRESSION_RATIO * max(info.compress_size, 1):
                raise _invalid("razão de compressão")


def _iter_csv(content: bytes, options: ReadOptions) -> Iterator[list[Any]]:
    # `newline=""` é o que o módulo csv exige para tratar quebras dentro de aspas.
    text = io.TextIOWrapper(io.BytesIO(content), encoding=options.encoding, newline="")
    yield from csv.reader(text, delimiter=options.csv_delimiter)


def _iter_xlsx(content: bytes) -> Generator[list[Any]]:
    if not content.startswith(XLSX_MAGIC):
        raise _invalid("magic bytes")
    _check_zip_budget(content)
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise _invalid("sem planilha")
        # A dimensão (`<dimension ref=…>`) é declarada pelo arquivo — não confiar.
        ws.reset_dimensions()
        for values in ws.iter_rows(max_col=MAX_FILE_COLUMNS, values_only=True):
            yield list(values)
    finally:
        wb.close()


def read_table(
    content: bytes,
    options: ReadOptions,
    *,
    on_header: Callable[[list[str]], None] | None = None,
    limit: int | None = None,
) -> ParsedTable:
    """Lê cabeçalho e linhas. `on_header` roda ANTES da primeira linha de dados.

    É por `on_header` que a conferência do cabeçalho (R2) acontece antes de
    qualquer linha ser lida — o teste dirigido conta as linhas iteradas. `limit`
    recorta as linhas de dados (a amostra da inspeção). Linha totalmente vazia é
    pulada (e contada como percorrida).
    """
    try:
        rows_iter = (
            _iter_csv(content, options)
            if options.file_format is InputFileFormat.CSV
            else _iter_xlsx(content)
        )
        header_values = next(rows_iter, None)
        if header_values is None:
            raise _invalid("vazio")
        if len(header_values) > MAX_FILE_COLUMNS:
            raise _invalid("colunas demais")
        columns = [_header_text(v) for v in header_values]
        while columns and columns[-1] == "":
            columns.pop()
        if not columns:
            raise _invalid("cabeçalho vazio")
        if on_header is not None:
            on_header(list(columns))

        width = len(columns)
        rows: list[tuple[int, list[Any]]] = []
        scanned = 0
        for offset, values in enumerate(rows_iter, start=2):
            scanned += 1
            if scanned > MAX_FILE_SCANNED_ROWS:
                raise _invalid("linhas demais")
            cells = [_cell(v) for v in values[:width]]
            if all(c is None for c in cells):
                continue
            # ANTES de guardar: `limit=0` é "só o cabeçalho" (o caminho sem
            # mapeamento) e não pode reter linha nenhuma em memória.
            if limit is not None and len(rows) >= limit:
                break
            if len(rows) >= MAX_FILE_ROWS:
                raise _invalid("linhas demais")
            cells.extend([None] * (width - len(cells)))
            rows.append((offset, cells))
        return ParsedTable(columns=columns, rows=rows, scanned=scanned)
    except AppError:
        raise
    except Exception:
        # A exceção original pode carregar o texto da célula: `from None` e
        # mensagem fixa — nada do arquivo vai para a resposta nem para o log.
        raise _invalid("leitura") from None


def read_xlsx_raw_rows(content: bytes, *, limit: int | None = None) -> list[tuple[int, list[Any]]]:
    """Linhas CRUAS da primeira aba do XLSX, `(número da linha, células)`, SEM cabeçalho.

    Para layout de terceiro cujo cabeçalho NÃO está na linha 1 (o export nativo do
    plano de contas do Domínio tem um banner antes dele): quem chama decide onde o
    cabeçalho está e o que é dado. Os guardas são os MESMOS de `read_table`:
    orçamento do zip antes do openpyxl, colunas por linha, linhas PERCORRIDAS
    (vazias inclusive) e linhas com dado; qualquer falha de abertura ou iteração é
    o mesmo `ARQUIVO_INVALIDO` com `from None`.

    Linha vazia VOLTA como `[]` (não é pulada): para quem chama, a linha em branco
    é estrutura (onde um bloco termina). Células passam pelo mesmo `_cell` (texto
    aparado, vazio vira `None`) e os `None` do fim da linha são cortados. `limit`
    recorta as linhas PERCORRIDAS (a espiada da detecção de layout).

    ⚠️ Célula mesclada: no modo `read_only` o valor vive só na célula-âncora, e as
    demais posições da mescla chegam `None`.
    """
    try:
        rows: list[tuple[int, list[Any]]] = []
        with_data = 0
        # `closing`: a espiada (`limit`) sai no meio, e o `finally` do gerador é
        # o que fecha o workbook.
        with closing(_iter_xlsx(content)) as values_iter:
            for line, values in enumerate(values_iter, start=1):
                if limit is not None and line > limit:
                    break
                if line > MAX_FILE_SCANNED_ROWS:
                    raise _invalid("linhas demais")
                cells = [_cell(v) for v in values]
                while cells and cells[-1] is None:
                    cells.pop()
                if cells:
                    with_data += 1
                    if with_data > MAX_FILE_ROWS:
                        raise _invalid("linhas demais")
                rows.append((line, cells))
        return rows
    except AppError:
        raise
    except Exception:
        # Mesmo contrato de `read_table`: a exceção original pode carregar a célula.
        raise _invalid("leitura") from None


def sample_text(values: Sequence[Any]) -> list[str]:
    """A amostra da inspeção como TEXTO (data em ISO, número como veio)."""
    out: list[str] = []
    for value in values:
        if value is None:
            out.append("")
        elif isinstance(value, datetime):
            out.append(value.date().isoformat())
        elif isinstance(value, date):
            out.append(value.isoformat())
        else:
            out.append(str(value))
    return out


# ---------------------------------------------------------------------------
# Conversão pelo mapeamento (puro)
# ---------------------------------------------------------------------------


def parse_date(value: Any, fmt: InputDateFormat) -> date:
    """Data pelo FORMATO DECLARADO. Objeto de data do XLSX passa direto."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("data não é texto")
    # Data civil (sem fuso): o `.date()` descarta a meia-noite ingênua do strptime.
    return datetime.strptime(value, _STRPTIME[fmt]).date()


def parse_amount(value: Any, sep: DecimalSeparator) -> Decimal:
    """Valor pelo SEPARADOR DECLARADO. Nunca `float` no caminho de dinheiro (§3.4).

    Texto: aceita `R$`, espaços, sinal `-` à esquerda e parênteses como negativo;
    o resto tem de casar com o formato ESTRITO da convenção (`_AMOUNT_TEXT_PATTERNS`):
    milhar só em grupos de 3 no separador oposto, no máximo 2 casas. Fora disso é
    `ValueError` — `1500.50` com vírgula declarada NÃO vira `150050` (retrabalho da
    14.3: aceitava e gravava R$ 150.050,00 calado), `1E+30` e `1.5` também não.
    Número (célula numérica do XLSX): mais de duas casas é recusado (arredondar em
    silêncio mudaria o valor). Os dois caminhos passam pelo teto da coluna
    (`MAX_AMOUNT_ABS`): o que não cabe em `Numeric(14,2)` estouraria no upsert
    como `DataError` → 500, com os parâmetros do INSERT (células) no log.

    Só levanta `ValueError` — quem chama converte em `valor_nao_numerico`.
    """
    if isinstance(value, bool):
        raise ValueError("booleano")
    if isinstance(value, int | float | Decimal):
        text = str(value)
    elif isinstance(value, str):
        text = value.replace("R$", "").replace("\u00a0", "").replace(" ", "")
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        if not _AMOUNT_TEXT_PATTERNS[sep].fullmatch(text):
            raise ValueError("fora do formato do separador declarado")
        if sep is DecimalSeparator.COMMA:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
        if negative:
            text = "-" + text
    else:
        raise ValueError("valor não é texto nem número")
    try:
        amount = Decimal(text)
        if not amount.is_finite():
            raise ValueError("não finito")
        if abs(amount) >= MAX_AMOUNT_ABS:
            raise ValueError("acima do teto da coluna")
        quantized = amount.quantize(_CENT)
    except InvalidOperation:
        # `from None`: a exceção do decimal não carrega a célula hoje, mas o
        # contrato do leitor é que NENHUMA falha de célula leve contexto adiante.
        raise ValueError("não numérico") from None
    if quantized != amount:
        raise ValueError("mais de duas casas")
    return quantized


def _text(value: Any, *, max_chars: int) -> str | None:
    """Célula como texto (número vira texto), aparada; acima do teto é `ValueError`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        text = value.date().isoformat()
    elif isinstance(value, date):
        text = value.isoformat()
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    if not text:
        return None
    if len(text) > max_chars:
        raise ValueError("longo demais")
    return text


def _signed_amount(
    cells: dict[str, Any], spec: MappingSpec
) -> tuple[Decimal | None, LineReason | None]:
    """O valor COM SINAL pela convenção DECLARADA — nunca inferido."""
    match spec.sign_convention:
        case SignConvention.VALOR_COM_SINAL:
            raw = cells.get(spec.amount_column or "")
            if raw is None:
                return None, "campo_obrigatorio_vazio"
            try:
                return parse_amount(raw, spec.decimal_separator), None
            except ValueError:
                return None, "valor_nao_numerico"
        case SignConvention.COLUNA_NATUREZA:
            raw = cells.get(spec.amount_column or "")
            nature = cells.get(spec.nature_column or "")
            if raw is None or nature is None:
                return None, "campo_obrigatorio_vazio"
            try:
                amount = abs(parse_amount(raw, spec.decimal_separator))
            except ValueError:
                return None, "valor_nao_numerico"
            nature_text = str(nature).strip()
            if nature_text == spec.debit_value:
                return -amount, None
            if nature_text == spec.credit_value:
                return amount, None
            return None, "natureza_desconhecida"
        case SignConvention.COLUNAS_SEPARADAS:
            debit_raw = cells.get(spec.debit_column or "")
            credit_raw = cells.get(spec.credit_column or "")
            if debit_raw is None and credit_raw is None:
                return None, "campo_obrigatorio_vazio"
            try:
                debit = abs(parse_amount(debit_raw, spec.decimal_separator)) if debit_raw else 0
                credit = abs(parse_amount(credit_raw, spec.decimal_separator)) if credit_raw else 0
            except ValueError:
                return None, "valor_nao_numerico"
            return Decimal(credit) - Decimal(debit), None
        case _:
            return None, "campo_obrigatorio_vazio"


def convert_lines(
    table: ParsedTable, spec: MappingSpec, *, start: date, end: date
) -> tuple[list[ParsedLine], list[LineProblem]]:
    """Converte TODAS as linhas pelo mapeamento, acumulando problemas (R2).

    Nunca para na primeira falha: o arquivo é recusado inteiro, e a pessoa precisa
    da lista completa (limitada na resposta) para corrigir de uma vez. Texto da
    célula nunca sai daqui a não ser dentro de `ParsedLine` (memória).
    """
    index = {name: pos for pos, name in enumerate(table.columns)}
    parsed: list[ParsedLine] = []
    problems: list[LineProblem] = []

    for line, values in table.rows:
        cells = {name: values[pos] for name, pos in index.items()}

        raw_date = cells.get(spec.date_column)
        if raw_date is None:
            problems.append(LineProblem(line, "campo_obrigatorio_vazio"))
            continue
        try:
            entry_date = parse_date(raw_date, spec.date_format)
        except ValueError:
            problems.append(LineProblem(line, "data_invalida"))
            continue
        if not start <= entry_date <= end:
            problems.append(LineProblem(line, "data_fora_da_competencia"))
            continue

        amount, reason = _signed_amount(cells, spec)
        if reason is not None or amount is None:
            problems.append(LineProblem(line, reason or "valor_nao_numerico"))
            continue

        try:
            description = _text(cells.get(spec.description_column), max_chars=MAX_DESCRIPTION_CHARS)
            category = (
                _text(cells.get(spec.category_column), max_chars=MAX_CATEGORY_LABEL_CHARS)
                if spec.category_column
                else None
            )
            account = (
                _text(cells.get(spec.account_column), max_chars=MAX_MOVEMENT_REF_CHARS)
                if spec.account_column
                else None
            )
            document = (
                _text(cells.get(spec.document_column), max_chars=MAX_MOVEMENT_DOCUMENT_CHARS)
                if spec.document_column
                else None
            )
        except ValueError:
            problems.append(LineProblem(line, "campo_longo_demais"))
            continue

        parsed.append(
            ParsedLine(
                line=line,
                entry_date=entry_date,
                amount=amount,
                description=description or "",
                category_label=category,
                account=account,
                document=document,
            )
        )
    return parsed, problems
