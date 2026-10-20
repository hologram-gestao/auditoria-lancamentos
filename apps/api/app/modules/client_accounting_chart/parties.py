"""Leitura das relações de CLIENTES e de FORNECEDORES do Domínio (86e3qhhrn).

O escritório parceiro exporta do Domínio dois relatórios, "RELAÇÃO DE CLIENTES" e
"RELAÇÃO DE FORNECEDORES", e cada linha deles liga uma CONTA do plano contábil (o
código reduzido) à PESSOA a quem a conta pertence (CNPJ ou CPF). Este módulo só LÊ:
reconhece o layout, devolve as linhas `(conta, documento)` e o que não pôde ler com
segurança. Nada aqui grava, e quem decide se a conta existe no plano é o serviço.

**O layout** (duas amostras reais de 14/10/2026; as cópias anonimizadas são a fixture
`tests/fixtures/accounting_chart_dominio_parties/`), em `.xls` com o MESMO defeito dos
BLANKs órfãos do plano, que o leitor compartilhado já repara:

- um banner nas primeiras linhas: `Empresa:` e `C.N.P.J.:` com os valores ao lado,
  `Folha:`, `Emissão:` e `Hora:`; depois o TÍTULO do relatório numa linha própria
  ("RELAÇÃO DE CLIENTES" ou "RELAÇÃO DE FORNECEDORES"), que é de onde o TIPO do
  vínculo sai; depois o CABEÇALHO: `Código`, `Conta`, `Nome`, `Razão Social` e a
  coluna do documento (`C.N.P.J./C.P.F./C.E.I./…`). Os dois relatórios TROCAM
  `Código` e `Conta` de lugar, então as colunas casam pelo NOME, nunca pela posição;
- uma linha por pessoa: o `Código` (o cadastro no Domínio, ignorado), a `Conta` (o
  código reduzido do plano, inteiro), nome e razão social (ignorados: nome não
  persiste, §4.5) e o documento, 14 dígitos (CNPJ) ou 11 (CPF), como texto;
- no fim, uma linha de total ("Total de …"), que fecha o bloco.

**Duas camadas de problema, decididas pelas amostras reais** (14/10/2026):

- **recusa o arquivo inteiro** (422, nenhum vínculo gravado) o que diz que o ARQUIVO
  está errado ou editado: linha do bloco com documento mas sem conta, linha com cara de
  pessoa DEPOIS do fim do bloco (seria descartada calada, lei da Sprint 2), e, no
  serviço, conta que não existe no plano do cliente ou é sintética (o pedido da task:
  "tudo ou nada por arquivo, como o plano");
- **não vincula, mas não recusa** o que é estado LEGÍTIMO do Domínio e a pessoa não
  conserta reexportando: a conta `0` (o Domínio a usa para "sem conta"; 3 fornecedores
  na amostra), o documento ausente, o documento com dígito verificador errado (erro de
  digitação no Domínio, que se corrige lá; nenhum nas amostras) e a conta
  COMPARTILHADA por mais de uma pessoa (uma conta "diversos" com dois fornecedores:
  um vínculo um-para-um é impossível, então nenhuma das linhas dela vincula). Essas
  linhas voltam na resposta de sucesso, com motivo fechado e o código da conta.

Toda recusa e toda linha não vinculada nomeiam só o número da linha, o motivo e o
CÓDIGO da conta (código não é nome, §4.5: é o que a pessoa procura no plano para
corrigir). O documento, o nome e a razão social nunca saem daqui a não ser dentro de
`PartyLine`, em memória.

O relatório que não é nenhum dos dois (sem o título) e o arquivo sem o CNPJ da empresa
no banner são recusados: o tipo do vínculo nunca é adivinhado, e a empresa é o que
impede importar o relatório de OUTRO cliente por engano.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from app.core.cnpj import normalize_cnpj
from app.core.cpf import normalize_cpf
from app.core.exceptions import (
    FileFormatNotSupportedError,
    FileHeaderMismatchError,
    FileInvalidError,
    FileLinesInvalidError,
)
from app.db.models.client_accounting_account import AccountingPartyType
from app.db.models.client_input_mapping import InputFileFormat
from app.modules.client_accounting_chart.sheet import fold_text
from app.modules.client_file_ingestion.reader import (
    MAX_INVALID_LINES_REPORTED,
    detect_format,
    read_sheet_raw_rows,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Até onde o título, o CNPJ da empresa e o cabeçalho são procurados. Nas amostras
#: o cabeçalho é a linha 7; 15 dá folga sem varrer o arquivo.
PARTIES_HEADER_SEARCH_ROWS = 15

#: Os títulos que dão o TIPO do vínculo, depois de `_fold`.
PARTY_REPORT_TITLES: dict[str, AccountingPartyType] = {
    "relacao de clientes": AccountingPartyType.CLIENTE,
    "relacao de fornecedores": AccountingPartyType.FORNECEDOR,
}
#: O rótulo do CNPJ da empresa no banner, depois de `_fold`.
_COMPANY_LABEL = "c.n.p.j.:"

#: Os papéis das colunas do cabeçalho — vocabulário NOSSO (é o que a recusa nomeia).
type PartyColumn = Literal["codigo", "conta", "nome", "razao_social", "documento"]
#: Nome normalizado do cabeçalho → papel. A coluna do documento é reconhecida pelo
#: PREFIXO (`c.n.p.j./c.p.f./…` varia no fim entre os dois relatórios).
_HEADER_ROLES: dict[str, PartyColumn] = {
    "codigo": "codigo",
    "conta": "conta",
    "nome": "nome",
    "razao social": "razao_social",
}
_DOCUMENT_HEADER_PREFIX = "c.n.p.j."
EXPECTED_PARTY_COLUMNS: tuple[str, ...] = (
    "Código",
    "Conta",
    "Nome",
    "Razão Social",
    "C.N.P.J./C.P.F.",
)

#: Motivos que RECUSAM o arquivo inteiro — vocabulário FECHADO (a resposta nunca traz a célula).
type PartyLineReason = Literal[
    # A linha do bloco tem documento mas não tem a conta (código reduzido) na coluna `Conta`.
    "conta_ausente",
    # A coluna `Conta` tem algo que não é um número inteiro (`685A`).
    "conta_invalida",
    # Linha com cara de pessoa DEPOIS do fim do bloco: seria descartada calada.
    "linha_fora_do_bloco",
    # Decididos pelo SERVIÇO, contra o plano do cliente (pedido da task: tudo ou nada):
    "conta_nao_encontrada",
    "conta_sintetica",
]

#: Motivos pelos quais uma linha NÃO vincula sem recusar o arquivo — vocabulário FECHADO.
type PartySkipReason = Literal[
    # `Conta` igual a 0: o Domínio usa para a pessoa sem conta contábil.
    "sem_conta",
    # A coluna do documento está vazia.
    "documento_ausente",
    # Nem CNPJ (14 dígitos) nem CPF (11), ou dígito verificador errado no Domínio.
    "documento_invalido",
    # A mesma conta em linhas de pessoas DIFERENTES: vínculo um-para-um impossível.
    "conta_compartilhada",
]

type DocumentKind = Literal["cnpj", "cpf"]

_INVALID_FILE_MESSAGE = (
    "Não foi possível ler o relatório. Envie a RELAÇÃO DE CLIENTES ou a RELAÇÃO DE "
    "FORNECEDORES exportada do Domínio, em XLS ou XLSX."
)
_FORMAT_MESSAGE = (
    "O relatório de clientes e fornecedores precisa ser a planilha exportada do Domínio "
    "(XLS ou XLSX). CSV e PDF não são aceitos aqui."
)
_HEADER_MESSAGE = (
    "O arquivo não foi reconhecido como a RELAÇÃO DE CLIENTES nem como a RELAÇÃO DE "
    "FORNECEDORES do Domínio: o cabeçalho com Código, Conta, Nome, Razão Social e "
    "C.N.P.J./C.P.F. não foi encontrado nas primeiras linhas."
)
_TITLE_MESSAGE = (
    "O arquivo tem o cabeçalho da relação, mas não diz se é a RELAÇÃO DE CLIENTES ou a "
    "RELAÇÃO DE FORNECEDORES. Exporte o relatório de novo, sem editar as primeiras linhas."
)
_COMPANY_MESSAGE = (
    "O relatório não traz o CNPJ da empresa no cabeçalho (a linha C.N.P.J.:). Exporte o "
    "relatório de novo, sem editar as primeiras linhas."
)
_LINES_MESSAGE = (
    "O relatório tem linhas que não puderam ser lidas com segurança. Confira as linhas "
    "apontadas (ou exporte o relatório de novo) — nenhum vínculo foi gravado."
)
_EMPTY_MESSAGE = "O relatório não tem nenhuma pessoa abaixo do cabeçalho. Confira o arquivo."


@dataclass(frozen=True, slots=True)
class PartyLine:
    """Uma linha VÁLIDA do relatório: a conta e o documento, só em memória."""

    line: int
    account_code: str
    document: str
    kind: DocumentKind

    def __repr__(self) -> str:
        # O documento é dado do cliente final: nunca num repr que acabe em log.
        return f"<PartyLine line={self.line} account_code={self.account_code!r} kind={self.kind}>"


@dataclass(frozen=True, slots=True)
class PartyLineProblem:
    """Uma linha que não vira vínculo: motivo fechado (recusa ou pulo) e o código da conta."""

    line: int
    reason: PartyLineReason | PartySkipReason
    #: O código reduzido da conta, quando a linha o tem: é o que a pessoa procura no
    #: plano para corrigir. Código não é nome (§4.5).
    account_code: str | None = None


@dataclass(frozen=True, slots=True)
class PartiesReport:
    """O relatório lido: o tipo do vínculo, o CNPJ da empresa (14 dígitos), as linhas
    vinculáveis e as que ficam sem vínculo (`skipped`, motivo em `PartySkipReason`)."""

    party_type: AccountingPartyType
    company_document: str
    lines: list[PartyLine]
    skipped: list[PartyLineProblem]
    #: Linhas de pessoa no relatório (vinculáveis + sem vínculo + repetidas).
    persons: int

    def __repr__(self) -> str:
        return (
            f"<PartiesReport type={self.party_type} lines={len(self.lines)} "
            f"skipped={len(self.skipped)}>"
        )


def _fold(text: str) -> str:
    """A dobra do `sheet.py` mais o colapso de espaços (os títulos vêm com dois)."""
    return re.sub(r"\s+", " ", fold_text(text))


def _filled(cells: Sequence[Any]) -> list[tuple[int, Any]]:
    return [(col, cell) for col, cell in enumerate(cells) if cell is not None]


def _as_int(value: Any) -> int | None:
    """Inteiro de célula NUMÉRICA (`685` ou `685.0`); texto de dígitos também vale."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        # `isdigit()` aceita `²` e dígitos de outros alfabetos, que `int()` recusa com
        # ValueError (seria 500): só dígitos ASCII contam.
        if text.isascii() and text.isdigit():
            return int(text)
    return None


def find_parties_header(
    rows: Sequence[tuple[int, Sequence[Any]]],
) -> tuple[int, dict[PartyColumn, int]] | None:
    """A linha do cabeçalho e a COLUNA de cada papel, ou `None`.

    O cabeçalho é a linha, nas primeiras `PARTIES_HEADER_SEARCH_ROWS`, cujas células
    preenchidas são todas texto e cobrem os cinco papéis (as posições não importam:
    os dois relatórios trocam `Código` e `Conta` de lugar).
    """
    for line, cells in rows[:PARTIES_HEADER_SEARCH_ROWS]:
        filled = _filled(cells)
        if not filled or not all(isinstance(cell, str) for _, cell in filled):
            continue
        roles: dict[PartyColumn, int] = {}
        for col, cell in filled:
            folded = _fold(cell)
            role = _HEADER_ROLES.get(folded)
            if role is None and folded.startswith(_DOCUMENT_HEADER_PREFIX):
                role = "documento"
            if role is None or role in roles:
                roles = {}
                break
            roles[role] = col
        if len(roles) == 5:
            return line, roles
    return None


def _find_title(
    rows: Sequence[tuple[int, Sequence[Any]]], header_line: int
) -> AccountingPartyType | None:
    for line, cells in rows:
        if line >= header_line:
            break
        for _, cell in _filled(cells):
            if isinstance(cell, str) and (kind := PARTY_REPORT_TITLES.get(_fold(cell))) is not None:
                return kind
    return None


def _find_company(rows: Sequence[tuple[int, Sequence[Any]]], header_line: int) -> str | None:
    """Os 14 dígitos do CNPJ ao lado de `C.N.P.J.:` no banner, ou `None`."""
    for line, cells in rows:
        if line >= header_line:
            break
        filled = _filled(cells)
        for position, (_, cell) in enumerate(filled):
            if (
                isinstance(cell, str)
                and _fold(cell) == _COMPANY_LABEL
                and position + 1 < len(filled)
            ):
                normalized = normalize_document(filled[position + 1][1])
                if normalized is None or normalized[1] != "cnpj":
                    return None
                return normalized[0]
    return None


def normalize_document(raw: Any) -> tuple[str, DocumentKind] | None:
    """Os dígitos de um CNPJ ou CPF válido e qual dos dois é, ou `None`.

    Texto é lido como texto (com ou sem máscara), nunca convertido em número: um
    CNPJ que começa com zeros perderia os zeros. Célula NUMÉRICA (um documento sem
    máscara pode chegar como número) vira texto, e os zeros à esquerda que o Excel
    perdeu são repostos pelo tamanho que sobrou (9 a 11 dígitos é CPF, 12 a 14 é CNPJ),
    com o verificador confirmando; número mais curto não se recupera.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        # Texto fica texto: `00123456000190` passa por `int` e perde os dois zeros.
        digits = re.sub(r"\D", "", raw)
    else:
        number = _as_int(raw)
        if number is None:
            return None
        digits = str(number)
        # Célula NUMÉRICA perde os zeros à esquerda no Excel. O tamanho que sobrou diz
        # o formato (9 a 11 dígitos é CPF, 12 a 14 é CNPJ); os zeros são repostos e o
        # DÍGITO VERIFICADOR confirma. Menos que isso não se recupera: um número curto
        # completado caberia nos dois formatos, e o verificador aceitaria um deles por
        # acaso.
        if 9 <= len(digits) <= 11:
            return _validated_document(digits.zfill(11))
        if 12 <= len(digits) <= 14:
            return _validated_document(digits.zfill(14))
        return None
    return _validated_document(digits)


def parse_typed_document(raw: str) -> tuple[str, DocumentKind] | None:
    """O documento DIGITADO na tela (vínculo manual, 86e3tttaf), ou `None`.

    Mais estrito que a célula do relatório: só dígitos e a máscara usual (`.`, `/`,
    `-`, espaço), conferidos por `normalize_cnpj`/`normalize_cpf`; 14 dígitos é CNPJ,
    11 é CPF, outro tamanho é inválido. Nunca passa por `int` (os zeros à esquerda).
    """
    digits = re.sub(r"\D", "", raw or "")
    try:
        if len(digits) == 14:
            return normalize_cnpj(raw), "cnpj"
        if len(digits) == 11:
            return normalize_cpf(raw), "cpf"
    except ValueError:
        return None
    return None


def _validated_document(digits: str) -> tuple[str, DocumentKind] | None:
    try:
        if len(digits) == 14:
            return normalize_cnpj(digits), "cnpj"
        if len(digits) == 11:
            return normalize_cpf(digits), "cpf"
    except ValueError:
        return None
    return None


def _looks_like_party(cells: Sequence[Any], columns: dict[PartyColumn, int]) -> bool:
    """A linha tem a conta OU o documento nas colunas do cabeçalho: é uma pessoa."""
    conta = columns["conta"]
    documento = columns["documento"]
    return (conta < len(cells) and _as_int(cells[conta]) is not None) or (
        documento < len(cells) and normalize_document(cells[documento]) is not None
    )


def _cell(cells: Sequence[Any], col: int) -> Any:
    return cells[col] if col < len(cells) else None


@dataclass(frozen=True, slots=True)
class ConvertedParties:
    """O bloco convertido: vinculáveis, pulos (sem vínculo), recusas e as linhas que
    repetiam uma pessoa já lida (contam como pessoa, não viram vínculo duplicado)."""

    lines: list[PartyLine]
    skipped: list[PartyLineProblem]
    problems: list[PartyLineProblem]
    repeated: int

    @property
    def persons(self) -> int:
        """Quantas linhas de pessoa o relatório tem, como a pessoa as conta no Excel."""
        return len(self.lines) + len(self.skipped) + self.repeated


def convert_parties(
    rows: Sequence[tuple[int, Sequence[Any]]],
    header_line: int,
    columns: dict[PartyColumn, int],
) -> ConvertedParties:
    """Converte o bloco de pessoas abaixo do cabeçalho em `ConvertedParties`.

    Função PURA. O bloco termina na primeira linha preenchida sem cara de pessoa (a
    linha de total); tudo depois só é conferido, e linha com cara de pessoa ali vira
    `linha_fora_do_bloco` (recusa). Dentro do bloco, a conta `0`, o documento ausente
    ou inválido e a conta compartilhada viram PULOS (`PartySkipReason`): a linha fica
    sem vínculo e volta na resposta. A mesma conta repetida com o MESMO documento é
    uma linha só (o Domínio lista a pessoa de novo); com documentos diferentes, TODAS
    as linhas daquela conta viram `conta_compartilhada`.
    """
    after_header = [(line, cells) for line, cells in rows if line > header_line]
    position = 0
    while position < len(after_header) and not _filled(after_header[position][1]):
        position += 1

    candidates: list[PartyLine] = []
    skipped: list[PartyLineProblem] = []
    problems: list[PartyLineProblem] = []
    while position < len(after_header):
        line, cells = after_header[position]
        if not _filled(cells):
            position += 1
            continue
        if not _looks_like_party(cells, columns):
            break
        position += 1
        raw_account = _cell(cells, columns["conta"])
        account = _as_int(raw_account)
        if account is None:
            problems.append(
                PartyLineProblem(line, "conta_ausente" if raw_account is None else "conta_invalida")
            )
            continue
        code = str(account)
        if account == 0:
            skipped.append(PartyLineProblem(line, "sem_conta", code))
            continue
        raw_document = _cell(cells, columns["documento"])
        if raw_document is None:
            skipped.append(PartyLineProblem(line, "documento_ausente", code))
            continue
        normalized = normalize_document(raw_document)
        if normalized is None:
            skipped.append(PartyLineProblem(line, "documento_invalido", code))
            continue
        document, kind = normalized
        candidates.append(PartyLine(line=line, account_code=code, document=document, kind=kind))

    problems.extend(
        PartyLineProblem(line, "linha_fora_do_bloco")
        for line, cells in after_header[position:]
        if _looks_like_party(cells, columns)
    )

    documents_by_code: dict[str, set[str]] = {}
    for candidate in candidates:
        documents_by_code.setdefault(candidate.account_code, set()).add(candidate.document)
    lines: list[PartyLine] = []
    seen: set[str] = set()
    repeated = 0
    for candidate in candidates:
        if len(documents_by_code[candidate.account_code]) > 1:
            skipped.append(
                PartyLineProblem(candidate.line, "conta_compartilhada", candidate.account_code)
            )
            continue
        if candidate.account_code in seen:
            repeated += 1
            continue
        seen.add(candidate.account_code)
        lines.append(candidate)
    skipped.sort(key=lambda p: p.line)
    return ConvertedParties(lines=lines, skipped=skipped, problems=problems, repeated=repeated)


def _raise_invalid_file(reason: str | None = None) -> NoReturn:
    details = {"reason": reason} if reason else None
    raise FileInvalidError(
        f"relatório de terceiros ilegível: {reason or 'leitura'}",
        user_message={
            "relatorio_nao_reconhecido": _TITLE_MESSAGE,
            "empresa_nao_identificada": _COMPANY_MESSAGE,
            "sem_pessoas": _EMPTY_MESSAGE,
        }.get(reason or "", _INVALID_FILE_MESSAGE),
        details=details,
    ) from None


def raise_party_lines(problems: Sequence[PartyLineProblem]) -> NoReturn:
    """A recusa 422 do arquivo inteiro: linha, motivo e código da conta, limitada."""
    ordered = sorted(problems, key=lambda p: (p.line, p.reason))
    raise FileLinesInvalidError(
        f"relatório de terceiros: {len(ordered)} linha(s) inválida(s)",
        user_message=_LINES_MESSAGE,
        details={
            "lines": [
                {"line": p.line, "reason": p.reason, "accountCode": p.account_code}
                for p in ordered[:MAX_INVALID_LINES_REPORTED]
            ],
            "total": len(ordered),
        },
    )


def parse_parties_report(content: bytes) -> PartiesReport:
    """Lê e valida o relatório INTEIRO; devolve as linhas ou levanta a recusa tipada.

    Ordem das recusas (todas 422, nenhuma grava nada): formato pelo conteúdo
    (`FORMATO_NAO_SUPORTADO`: só planilha) → arquivo que não abre (`ARQUIVO_INVALIDO`)
    → cabeçalho da relação (`CABECALHO_DIVERGENTE`) → título e CNPJ da empresa
    (`ARQUIVO_INVALIDO` com `details.reason`) → linhas (`LINHAS_INVALIDAS`) →
    relatório sem nenhuma pessoa (`ARQUIVO_INVALIDO`, `reason=sem_pessoas`).
    """
    if detect_format(content) is not InputFileFormat.XLSX:
        raise FileFormatNotSupportedError(
            "relatório de terceiros não é planilha", user_message=_FORMAT_MESSAGE
        )
    try:
        raw = read_sheet_raw_rows(content)
    except FileInvalidError:
        _raise_invalid_file()

    header = find_parties_header(raw)
    if header is None:
        raise FileHeaderMismatchError(
            "relatório de terceiros sem o cabeçalho da relação",
            user_message=_HEADER_MESSAGE,
            details={
                "expectedColumns": list(EXPECTED_PARTY_COLUMNS),
                "missingColumns": list(EXPECTED_PARTY_COLUMNS),
                "repeatedColumns": [],
                "unexpectedColumnCount": 0,
                "foundColumnCount": 0,
            },
        )
    header_line, columns = header
    party_type = _find_title(raw, header_line)
    if party_type is None:
        _raise_invalid_file("relatorio_nao_reconhecido")
    company = _find_company(raw, header_line)
    if company is None:
        _raise_invalid_file("empresa_nao_identificada")

    converted = convert_parties(raw, header_line, columns)
    if converted.problems:
        raise_party_lines(converted.problems)
    if converted.persons == 0:
        _raise_invalid_file("sem_pessoas")
    return PartiesReport(
        party_type=party_type,
        company_document=company,
        lines=converted.lines,
        skipped=converted.skipped,
        persons=converted.persons,
    )
