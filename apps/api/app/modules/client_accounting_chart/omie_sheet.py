"""A planilha de importação do plano de contas da Omie (86e3qhhrr).

A API pública da Omie não tem plano de contas: a entrada é a planilha
`Omie_Plano_Contas` v1.1.3 do Painel do Contador, e "só o modelo é aceito" (planilha
protegida). Por isso o gerador NÃO monta um workbook: ele COPIA o modelo versionado
(`app/resources/omie/Omie_Plano_Contas_v1_1_3.xlsx`, o modelo vazio extraído por
`scripts/extract_omie_chart_template.py`) e só preenche células, editando o XML do
pacote. Regravar pelo `openpyxl` descartaria as listas de opções (a validação de dados
do modelo é a extensão `x14:dataValidations`, que ele não suporta), e a Omie pode
recusar a planilha sem elas.

O que muda no pacote, e só isso:

- `xl/worksheets/sheet1.xml`: as linhas 6 em diante, uma por conta, na ordem do plano.
  Cada célula preenchida ganha `t="s"` e o índice da string; o estilo (`s=`) é o do
  modelo, que já é texto (`@`) e desbloqueado. Linha, coluna, validações, proteção,
  filtro e o resto do XML ficam byte a byte;
- `xl/sharedStrings.xml`: as strings do modelo, mais as novas na ordem da primeira
  aparição, com `count` e `uniqueCount` recalculados.

Colunas (B a J): classificação, código reduzido, nome, natureza (`Devedora`/
`Credora`), tipo (`Analítica`/`Totalizadora (Sintética)`), SPED vazio, o CNPJ ou CPF do
vínculo COM máscara (o modelo diz "texto, pontos e traços") só na conta vinculada,
centro de custo vazio e referencial vazio.

**Recusa, nunca altera texto** (molde da S13): plano vazio, plano maior que a área do
modelo, conta sem classificação, sem natureza, nome ou documento indecifrável, texto
que começa com `= + - @`, que passa do teto da coluna ou que tem caractere de
controle. `find_problems` devolve TODOS os problemas, cada um com os CÓDIGOS das
contas (nunca nome, classificação nem documento).

**Mesma entrada, mesmos bytes**: as partes são montadas sem nada que dependa de hora
ou de ordem de dicionário, e o zip sai com data fixa e a mesma ordem e compressão do
modelo. Funções PURAS: recebem as linhas, devolvem os bytes.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from xml.sax.saxutils import escape

from app.core.cnpj import CNPJ_DIGITS, format_cnpj
from app.core.cpf import CPF_DIGITS, format_cpf
from app.db.models.client_accounting_account import AccountingAccountNature, AccountingAccountType
from app.modules.export_layouts.definition import FORMULA_PREFIXES

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

#: O modelo VAZIO versionado: as linhas 1 a 5 e a aba `Config` do modelo da Omie,
#: com as validações. A amostra preenchida que o originou nunca entra no repositório.
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "resources" / "omie" / "Omie_Plano_Contas_v1_1_3.xlsx"
)
#: A versão do modelo, como a aba `Config` a declara (`Config!B3`).
TEMPLATE_VERSION = "1.1.3"
SHEET_PART = "xl/worksheets/sheet1.xml"
SHARED_STRINGS_PART = "xl/sharedStrings.xml"

#: A área de dados do modelo (`Area` = `Omie_Plano_Contas!$B$6:$J$10005`).
FIRST_DATA_ROW = 6
LAST_DATA_ROW = 10_005
MAX_ACCOUNTS = LAST_DATA_ROW - FIRST_DATA_ROW + 1

#: O tipo do arquivo baixado.
OMIE_EXPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Os tetos das colunas, como a aba `Config` do modelo os declara (linhas 7 a 11).
MAX_CLASSIFICATION_CHARS = 60
MAX_CODE_CHARS = 20
MAX_NAME_CHARS = 60
MAX_DOCUMENT_CHARS = 20

#: Os textos das listas de opções do modelo (aba `Config`, colunas E e F).
NATURE_LABELS: dict[AccountingAccountNature, str] = {
    AccountingAccountNature.DEVEDORA: "Devedora",
    AccountingAccountNature.CREDORA: "Credora",
}
TYPE_LABELS: dict[AccountingAccountType, str] = {
    AccountingAccountType.ANALITICA: "Analítica",
    AccountingAccountType.SINTETICA: "Totalizadora (Sintética)",
}

#: Data fixa das entradas do zip (a do próprio modelo): o arquivo não pode depender
#: da hora em que foi gerado.
_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

#: Por que uma conta (ou o plano) impede a planilha — vocabulário FECHADO, vai em
#: `details.problems[].reason`. A ordem desta tupla é a ordem da resposta.
type OmieExportReason = Literal[
    "plano_vazio",
    "plano_grande_demais",
    "conta_sem_classificacao",
    "conta_sem_natureza",
    "nome_indecifravel",
    "documento_indecifravel",
    "texto_formula",
    "texto_longo",
    "texto_invalido",
]
REASON_ORDER: tuple[OmieExportReason, ...] = (
    "plano_vazio",
    "plano_grande_demais",
    "conta_sem_classificacao",
    "conta_sem_natureza",
    "nome_indecifravel",
    "documento_indecifravel",
    "texto_formula",
    "texto_longo",
    "texto_invalido",
)

#: Caractere de controle (inclusive quebra de linha e tabulação): o XML 1.0 não aceita
#: a maior parte deles, e o resto quebraria a célula. Recusa, nunca remove.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class OmieChartLine:
    """Uma conta do plano como a planilha da Omie a pede, já decifrada.

    `name` é `None` quando o nome não decifrou; `document` são os dígitos do vínculo
    (`None` = conta sem vínculo) e `document_failed` marca o vínculo que não decifrou.
    """

    code: str
    classification: str | None
    name: str | None
    nature: AccountingAccountNature | None
    account_type: AccountingAccountType
    document: str | None = None
    document_failed: bool = False


@dataclass(frozen=True, slots=True)
class OmieExportProblem:
    """Um motivo de recusa e os CÓDIGOS das contas que o têm, na ordem do plano."""

    reason: OmieExportReason
    account_codes: tuple[str, ...] = ()


#: Teto de códigos de conta por motivo na resposta (a contagem é sempre a real).
PROBLEM_CODES_LIMIT = 50


def omie_export_filename(client_id: UUID) -> str:
    """O nome do arquivo: o do modelo e só o início do id do cliente, nunca o nome (§4.7)."""
    return f"Omie_Plano_Contas_{client_id.hex[:8]}.xlsx"


def problems_details(problems: Sequence[OmieExportProblem]) -> list[dict[str, object]]:
    """Os problemas como a prévia e a recusa 409 os devolvem: motivo, os CÓDIGOS das
    contas (até `PROBLEM_CODES_LIMIT`) e a contagem real. Nunca nome nem documento."""
    return [
        {
            "reason": problem.reason,
            "accountCodes": list(problem.account_codes[:PROBLEM_CODES_LIMIT]),
            "count": len(problem.account_codes),
        }
        for problem in problems
    ]


def format_document(digits: str) -> str:
    """O documento como vai na coluna `CNPJ do Estabelecimento`: com a máscara.

    11 dígitos é CPF (`000.000.000-00`), 14 é CNPJ (`00.000.000/0001-91`); o que a
    importação das relações grava é sempre um dos dois. Outro tamanho levanta: a
    planilha não pode levar um documento que ninguém conferiu.
    """
    if len(digits) == CNPJ_DIGITS:
        return format_cnpj(digits)
    if len(digits) == CPF_DIGITS:
        return format_cpf(digits)
    raise ValueError("documento do vínculo com tamanho inesperado")


def find_problems(lines: Sequence[OmieChartLine]) -> list[OmieExportProblem]:
    """TODOS os motivos que impedem a planilha, cada um com os códigos das contas.

    Lista vazia = a planilha pode ser gerada. A conta pode aparecer em mais de um
    motivo (sem classificação E sem natureza, por exemplo): a pessoa vê tudo que tem
    de corrigir de uma vez.
    """
    if not lines:
        return [OmieExportProblem("plano_vazio")]
    if len(lines) > MAX_ACCOUNTS:
        return [OmieExportProblem("plano_grande_demais")]
    # `dict` como conjunto ORDENADO: a ordem do plano fica e a busca é O(1) (o plano
    # pode ter 10.000 contas, todas no mesmo motivo).
    found: dict[OmieExportReason, dict[str, None]] = {}

    def add(reason: OmieExportReason, code: str) -> None:
        found.setdefault(reason, {})[code] = None

    for line in lines:
        if not line.classification:
            add("conta_sem_classificacao", line.code)
        if line.nature is None:
            add("conta_sem_natureza", line.code)
        if line.name is None:
            add("nome_indecifravel", line.code)
        if line.document_failed or (
            line.document is not None and len(line.document) not in (CNPJ_DIGITS, CPF_DIGITS)
        ):
            add("documento_indecifravel", line.code)
        for text, limit in (
            (line.classification, MAX_CLASSIFICATION_CHARS),
            (line.code, MAX_CODE_CHARS),
            (line.name, MAX_NAME_CHARS),
        ):
            if not text:
                continue
            if text.startswith(FORMULA_PREFIXES):
                add("texto_formula", line.code)
            if len(text) > limit:
                add("texto_longo", line.code)
            if _CONTROL.search(text):
                add("texto_invalido", line.code)
    return [
        OmieExportProblem(reason, tuple(found[reason]))
        for reason in REASON_ORDER
        if reason in found
    ]


def line_cells(line: OmieChartLine) -> tuple[str, str, str, str, str, str]:
    """As células preenchidas da linha, de B a F e H (G, I e J ficam vazias).

    Só para linha SEM problema (`find_problems` vazio): levanta se faltar campo.
    """
    if line.classification is None or line.name is None or line.nature is None:
        raise ValueError("linha com problema não entra na planilha")
    document = format_document(line.document) if line.document is not None else ""
    return (
        line.classification,
        line.code,
        line.name,
        NATURE_LABELS[line.nature],
        TYPE_LABELS[line.account_type],
        document,
    )


def build_omie_chart_xlsx(lines: Sequence[OmieChartLine]) -> bytes:
    """A planilha preenchida: o modelo com uma linha por conta, na ordem recebida.

    Recusa (ValueError) se `find_problems` não estiver vazio: quem chama decide a
    resposta 409 antes. Mesma entrada, mesmos bytes.
    """
    if find_problems(lines):
        raise ValueError("plano com problema: a planilha não pode ser gerada")
    template = _template()
    strings = _SharedStrings(template.strings)
    filled: dict[int, str] = {}
    for offset, line in enumerate(lines):
        row = FIRST_DATA_ROW + offset
        classification, code, name, nature, account_type, document = line_cells(line)
        values = {
            "B": classification,
            "C": code,
            "D": name,
            "E": nature,
            "F": account_type,
            "H": document,
        }
        filled[row] = _fill_row(
            template.rows[row], row, {col: strings.index(v) for col, v in values.items() if v}
        )
    sheet = template.render_sheet(filled)
    parts = dict(template.parts)
    parts[SHEET_PART] = sheet.encode("utf-8")
    parts[SHARED_STRINGS_PART] = strings.render(
        template.strings_head, extra_count=sum(_count_cells(r) for r in filled.values())
    ).encode("utf-8")
    return _zip(template, parts)


# ------------------------------------------------------------------ o modelo


_ROW = re.compile(r'<row r="(\d+)"[^>]*>.*?</row>', re.S)
_SI = re.compile(r"<si>.*?</si>", re.S)
_SST_OPEN = re.compile(r'<sst [^>]*count="(\d+)" uniqueCount="(\d+)"[^>]*>')
_EMPTY_CELL = re.compile(r'<c r="([A-Z]+)(\d+)"( s="\d+")?/>')
_PLAIN_TEXT = re.compile(r"<si><t>([^<]*)</t></si>")


@dataclass(frozen=True, slots=True)
class _Template:
    """O modelo lido UMA vez: as partes do zip, as linhas da área de dados e as strings."""

    order: tuple[zipfile.ZipInfo, ...]
    parts: dict[str, bytes]
    sheet_head: str
    sheet_tail: str
    #: `row → XML` de TODAS as linhas da aba, na ordem do arquivo.
    rows: dict[int, str]
    #: As strings do modelo, em ordem (o texto puro de cada `<si>` simples).
    strings: tuple[str | None, ...]
    #: O XML de cada `<si>` do modelo (rich text inclusive) e a abertura do `<sst>`.
    strings_head: tuple[str, tuple[str, ...], int]

    def render_sheet(self, filled: dict[int, str]) -> str:
        body = "".join(filled.get(row, xml) for row, xml in self.rows.items())
        return self.sheet_head + body + self.sheet_tail


@lru_cache(maxsize=1)
def _template() -> _Template:
    raw = TEMPLATE_PATH.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        order = tuple(package.infolist())
        parts = {info.filename: package.read(info.filename) for info in order}
    # As duas partes que o gerador reescreve saem do cache: guardar os bytes delas além
    # das linhas já separadas seria uma segunda cópia de ~3 MB para a vida do processo.
    sheet = parts.pop(SHEET_PART).decode("utf-8")
    start = sheet.index("<sheetData>") + len("<sheetData>")
    end = sheet.index("</sheetData>")
    rows = {int(m.group(1)): m.group(0) for m in _ROW.finditer(sheet, start, end)}
    sst = parts.pop(SHARED_STRINGS_PART).decode("utf-8")
    opening = _SST_OPEN.search(sst)
    if opening is None:  # pragma: no cover - o modelo versionado sempre tem
        raise ValueError("sharedStrings do modelo sem contagem")
    items = tuple(m.group(0) for m in _SI.finditer(sst))
    plain = tuple(
        (match.group(1) if (match := _PLAIN_TEXT.fullmatch(item)) else None) for item in items
    )
    return _Template(
        order=order,
        parts=parts,
        sheet_head=sheet[:start],
        sheet_tail=sheet[end:],
        rows=rows,
        strings=tuple(_unescape(text) if text is not None else None for text in plain),
        strings_head=(sst[: opening.start()], items, int(opening.group(1))),
    )


class _SharedStrings:
    """As strings do modelo e as novas, deduplicadas como o Excel faz."""

    def __init__(self, base: tuple[str | None, ...]) -> None:
        self._index: dict[str, int] = {}
        for position, text in enumerate(base):
            if text is not None and text not in self._index:
                self._index[text] = position
        self._next = len(base)
        self._new: list[str] = []

    def index(self, text: str) -> int:
        found = self._index.get(text)
        if found is not None:
            return found
        position = self._next
        self._next += 1
        self._index[text] = position
        self._new.append(text)
        return position

    def render(self, head: tuple[str, tuple[str, ...], int], *, extra_count: int) -> str:
        prefix, items, count = head
        unique = len(items) + len(self._new)
        added = "".join(_si(text) for text in self._new)
        return (
            f'{prefix}<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            f'count="{count + extra_count}" uniqueCount="{unique}">'
            + "".join(items)
            + added
            + "</sst>"
        )


def _si(text: str) -> str:
    preserve = ' xml:space="preserve"' if text != text.strip() else ""
    return f"<si><t{preserve}>{escape(text)}</t></si>"


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _fill_row(xml: str, row: int, values: dict[str, int]) -> str:
    """A linha do modelo com as células de `values` preenchidas, estilo intocado."""

    def replace(match: re.Match[str]) -> str:
        column, number, style = match.group(1), match.group(2), match.group(3) or ""
        if int(number) != row or column not in values:
            return match.group(0)
        return f'<c r="{column}{number}"{style} t="s"><v>{values[column]}</v></c>'

    filled = _EMPTY_CELL.sub(replace, xml)
    if _count_cells(filled) != len(values):  # pragma: no cover - o modelo tem as células
        raise ValueError(f"linha {row} do modelo sem as células esperadas")
    return filled


def _count_cells(xml: str) -> int:
    return xml.count(' t="s"><v>')


def _zip(template: _Template, parts: dict[str, bytes]) -> bytes:
    """O pacote na MESMA ordem e compressão do modelo, com data fixa."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for original in template.order:
            info = zipfile.ZipInfo(original.filename, date_time=_ZIP_DATE_TIME)
            info.compress_type = original.compress_type
            info.external_attr = original.external_attr
            info.create_system = original.create_system
            package.writestr(info, parts[original.filename], compresslevel=9)
    return buffer.getvalue()
