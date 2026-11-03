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

**Desde 03/11/2026 (86e3ydjzy, a aba "Fornecedores Ajustados" da Tract)** a relação não
precisa ser o relatório do Domínio intacto:

- o cabeçalho por NOME exige só as colunas que viram vínculo (`Conta` e a do documento);
  `Código`, `Nome` e `Razão Social` são reconhecidos e ignorados, e coluna a mais não
  recusa. Sem cabeçalho por nome, o CONTEÚDO decide (`recognize_party_columns`): o
  documento é a coluna com cara de CNPJ/CPF e a conta, a de inteiros que EXISTEM como
  código no plano do cliente;
- sem o título, o tipo do vínculo é PERGUNTADO (`tipoVinculo` no pedido; sem ele, 422
  `TIPO_DE_VINCULO_NECESSARIO`), nunca adivinhado; com título, o título manda e um tipo
  diferente é 422 `TIPO_DE_VINCULO_DIVERGENTE`;
- sem o CNPJ da empresa no banner (ou ilegível), a relação entra SEM conferir a empresa e
  sem tocar a impressão digital; a resposta diz `empresaConferida: false`. O CNPJ ao lado
  de `C.N.P.J.:` na MESMA célula também é lido.
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
    PartyTypeMismatchError,
    PartyTypeRequiredError,
)
from app.db.models.client_accounting_account import AccountingPartyType
from app.db.models.client_input_mapping import InputFileFormat
from app.modules.client_accounting_chart.recognizer import (
    MIN_FORM_RATIO,
    as_integer,
    find_block,
    is_document_like,
)
from app.modules.client_accounting_chart.sheet import fold_text
from app.modules.client_file_ingestion.reader import (
    MAX_INVALID_LINES_REPORTED,
    detect_format,
    read_sheet_raw_rows,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

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
    # Só no vínculo pela coluna de documento do PLANO (86e3ydjzy): o mesmo documento em
    # duas contas, com o mesmo tipo, não sabe qual delas é a da pessoa.
    "documento_repetido",
    # Só no vínculo pela coluna do plano: a conta é sintética (só analítica vincula).
    "conta_sintetica",
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
    #: `None` = o relatório não traz o CNPJ da empresa (legível): a importação segue sem
    #: conferir a empresa e sem tocar a impressão digital (86e3ydjzy).
    company_document: str | None
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


#: As colunas sem as quais não há vínculo: a conta do plano e o documento.
REQUIRED_PARTY_COLUMNS: tuple[PartyColumn, ...] = ("conta", "documento")


def find_parties_header(
    rows: Sequence[tuple[int, Sequence[Any]]],
) -> tuple[int, dict[PartyColumn, int]] | None:
    """A linha do cabeçalho e a COLUNA de cada papel, ou `None`.

    O cabeçalho é a linha, nas primeiras `PARTIES_HEADER_SEARCH_ROWS`, cujas células
    preenchidas são todas texto, que nomeia a `Conta` e o documento, e onde nenhum papel
    se repete (as posições não importam: os dois relatórios trocam `Código` e `Conta` de
    lugar). `Código`, `Nome` e `Razão Social` são reconhecidos e ignorados; outra coluna
    não recusa (86e3ydjzy: a relação da Tract não tem `Nome`).
    """
    for line, cells in rows[:PARTIES_HEADER_SEARCH_ROWS]:
        filled = _filled(cells)
        if not filled or not all(isinstance(cell, str) for _, cell in filled):
            continue
        roles: dict[PartyColumn, int] = {}
        repeated = False
        for col, cell in filled:
            folded = _fold(cell)
            role = _HEADER_ROLES.get(folded)
            if role is None and folded.startswith(_DOCUMENT_HEADER_PREFIX):
                role = "documento"
            if role is None:
                continue
            if role in roles:
                repeated = True
                break
            roles[role] = col
        if not repeated and all(r in roles for r in REQUIRED_PARTY_COLUMNS):
            return line, roles
    return None


def recognize_party_columns(
    rows: Sequence[tuple[int, Sequence[Any]]], known_codes: Collection[str]
) -> tuple[int, dict[PartyColumn, int]] | None:
    """O segundo caminho (86e3ydjzy): as colunas pelo CONTEÚDO, quando não há cabeçalho.

    O bloco é o maior trecho com cara de linha de pessoa (o mesmo `find_block` do plano).
    O documento é a coluna com forma de CNPJ/CPF; a conta, a coluna de inteiros cujos
    valores (fora o `0`, a "sem conta" do Domínio) EXISTEM como código no plano do
    cliente. Empate ou ausência de qualquer um dos dois: `None` (a recusa de cabeçalho).
    Devolve a "linha do cabeçalho" como a linha logo acima do bloco, que é onde a
    conversão começa a ler.
    """
    positions = find_block(rows)
    if not positions:
        return None
    block = [rows[p][1] for p in positions]
    width = max(len(cells) for cells in block)
    documents: list[int] = []
    accounts: list[int] = []
    for col in range(width):
        values = [cells[col] for cells in block if col < len(cells) and cells[col] is not None]
        if not values:
            continue
        shaped = [v for v in values if is_document_like(v)]
        if len(shaped) >= MIN_FORM_RATIO * len(values) and any(as_integer(v) != 0 for v in shaped):
            documents.append(col)
            continue
        numbers = [as_integer(v) for v in values]
        non_zero = [str(n) for n in numbers if n is not None and n != 0]
        if (
            non_zero
            and len([n for n in numbers if n is not None]) >= MIN_FORM_RATIO * len(values)
            and sum(code in known_codes for code in non_zero) >= MIN_FORM_RATIO * len(non_zero)
        ):
            accounts.append(col)
    if len(documents) != 1 or len(accounts) != 1:
        return None
    return rows[positions[0]][0] - 1, {"conta": accounts[0], "documento": documents[0]}


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
    """Os 14 dígitos do CNPJ da empresa no banner, ou `None` se o banner não tem o rótulo.

    Ao lado de `C.N.P.J.:` (a célula seguinte, como o Domínio exporta) ou na MESMA célula,
    depois do rótulo (`C.N.P.J.: 05.482.859/0001-06`, como a relação da Tract traz). Rótulo
    presente com valor que não é CNPJ recusa (`empresa_nao_identificada`).
    """
    for line, cells in rows:
        if line >= header_line:
            break
        filled = _filled(cells)
        for position, (_, cell) in enumerate(filled):
            if not isinstance(cell, str):
                continue
            folded = _fold(cell)
            value: Any
            if folded == _COMPANY_LABEL and position + 1 < len(filled):
                value = filled[position + 1][1]
            elif folded.startswith(_COMPANY_LABEL) and len(folded) > len(_COMPANY_LABEL):
                value = cell.strip()[len(_COMPANY_LABEL) :]
            else:
                continue
            normalized = normalize_document(value)
            if normalized is None or normalized[1] != "cnpj":
                # O rótulo existe e o valor não é um CNPJ: é o arquivo editado (ou de outro
                # sistema), e importar sem conferir a empresa abriria a porta para a
                # relação de OUTRO cliente. Só a AUSÊNCIA do rótulo entra sem conferir.
                _raise_invalid_file("empresa_nao_identificada")
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

    Mais estrito que a célula do relatório: o texto vai inteiro para
    `normalize_cnpj`/`normalize_cpf`, que só aceitam dígitos e a máscara usual (`.`,
    `/`, `-`, espaço). O despacho por tamanho é o MESMO da importação
    (`_validated_document`). Nunca passa por `int` (os zeros à esquerda).
    """
    return _validated_document(raw or "")


def _validated_document(value: str) -> tuple[str, DocumentKind] | None:
    """14 dígitos é CNPJ, 11 é CPF, pelo verificador; `value` pode vir com a máscara."""
    digits = re.sub(r"\D", "", value)
    try:
        if len(digits) == 14:
            return normalize_cnpj(value), "cnpj"
        if len(digits) == 11:
            return normalize_cpf(value), "cpf"
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


def parse_parties_report(
    content: bytes,
    *,
    party_type: AccountingPartyType | None = None,
    known_codes: Collection[str] = (),
) -> PartiesReport:
    """Lê e valida o relatório INTEIRO; devolve as linhas ou levanta a recusa tipada.

    Ordem das recusas (todas 422, nenhuma grava nada): formato pelo conteúdo
    (`FORMATO_NAO_SUPORTADO`: só planilha) → arquivo que não abre (`ARQUIVO_INVALIDO`)
    → colunas da relação, pelo nome ou pelo conteúdo (`CABECALHO_DIVERGENTE`) → tipo do
    vínculo (`TIPO_DE_VINCULO_NECESSARIO` sem título e sem `party_type`;
    `TIPO_DE_VINCULO_DIVERGENTE` se o escolhido contradiz o título) → linhas
    (`LINHAS_INVALIDAS`) → relatório sem nenhuma pessoa (`ARQUIVO_INVALIDO`,
    `reason=sem_pessoas`). Sem o CNPJ da empresa, segue com `company_document=None`.
    `known_codes` são os códigos do plano do cliente (o reconhecimento da conta pelo
    conteúdo precisa deles).
    """
    if detect_format(content) is not InputFileFormat.XLSX:
        raise FileFormatNotSupportedError(
            "relatório de terceiros não é planilha", user_message=_FORMAT_MESSAGE
        )
    try:
        raw = read_sheet_raw_rows(content)
    except FileInvalidError:
        _raise_invalid_file()

    by_name = find_parties_header(raw)
    header = by_name or recognize_party_columns(raw, known_codes)
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
    titled = _find_title(raw, header_line)
    if titled is None:
        if party_type is None:
            raise PartyTypeRequiredError("relatório de terceiros sem o título do Domínio")
        titled = party_type
    elif party_type is not None and party_type is not titled:
        raise PartyTypeMismatchError("tipo escolhido diverge do título do relatório")
    company = _find_company(raw, header_line)

    converted = convert_parties(raw, header_line, columns)
    problems = list(converted.problems)
    if by_name is None:
        # Pelo conteúdo, o "cabeçalho" é a linha acima do MAIOR bloco: uma pessoa acima
        # dele (o bloco partido por uma linha sem cara de conta) seria pulada calada.
        problems.extend(
            PartyLineProblem(line, "linha_fora_do_bloco")
            for line, cells in raw
            if line <= header_line and _looks_like_party(cells, columns)
        )
    if problems:
        raise_party_lines(problems)
    if converted.persons == 0:
        _raise_invalid_file("sem_pessoas")
    return PartiesReport(
        party_type=titled,
        company_document=company,
        lines=converted.lines,
        skipped=converted.skipped,
        persons=converted.persons,
    )
