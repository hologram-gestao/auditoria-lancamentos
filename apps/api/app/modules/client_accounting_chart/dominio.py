"""Export NATIVO do plano de contas do Domínio (.xlsx) → o modelo da plataforma (86e3gkd7y).

O escritório parceiro exporta o plano do Domínio e não deveria ter de reescrevê-lo à
mão. Este módulo reconhece o layout e o CONVERTE para as linhas do modelo
(`codigo_reduzido`, `nome`, `tipo`, `classificacao`), que seguem pelo MESMO
validador de `sheet.validate_rows` e pela mesma gravação tudo-ou-nada. Nada daqui
grava, e nada daqui decide reimportação: só lê.

**O layout** (uma amostra real, de 02/10/2026; a cópia anonimizada é a fixture
`tests/fixtures/accounting_chart_dominio/`):

- primeira aba; um banner nas primeiras linhas (empresa, CNPJ, título) e o
  CABEÇALHO numa linha própria com `Código`, `T`, `Classificação`, `Nome` e `Grau`;
- milhares de células mescladas, cabeçalho DESALINHADO do dado (`Código` numa
  coluna, o código na vizinha) e o nome INDENTADO pela hierarquia (a coluna do
  nome muda com o grau). Ler por letra de coluna quebra; a leitura é POR PADRÃO DE
  CÉLULA, na ordem em que as células preenchidas aparecem na linha:

      código (inteiro) · [ `S` ] · classificação · nome · grau (inteiro)

  `S` marca a conta sintética; sem ela, a conta é analítica. O tipo vem SEMPRE
  dessa célula: grupo sintético sem filho existe de verdade no Domínio (40 na
  amostra), então "sintética tem filho" não é regra e nada é inferido;
- depois das contas, um rodapé (assinaturas, documentos, licença) que nunca é
  interpretado: o bloco de contas termina na primeira linha que não tem cara de
  conta, e o que vem depois só é OLHADO para garantir que nenhuma conta ficou fora.

**Na dúvida, recusa** (lei da Sprint 2: nenhuma perda silenciosa). Linha do bloco
fora do padrão, grau que não bate com a profundidade da classificação,
classificação repetida e qualquer linha com cara de conta DEPOIS do fim do bloco
recusam o arquivo inteiro com `LINHAS_INVALIDAS`, motivo de vocabulário fechado e
número de linha. A célula nunca sai daqui: o banner tem razão social e CNPJ, o
rodapé tem nome e CPF, e o nome de conta é dado do cliente final (§4.5).

O que este módulo NÃO aceita, de propósito: o CSV nativo do Domínio (não há
amostra; cai na recusa de cabeçalho de hoje) e qualquer variação que não case com
o padrão acima. Amostra nova de outra versão vira fixture nova do teste-ouro antes
de o padrão afrouxar.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.db.models.client_accounting_account import AccountingAccountType

if TYPE_CHECKING:
    from collections.abc import Sequence

#: O cabeçalho do Domínio, na ordem, depois de aparar, tirar a caixa e o acento.
#: Tem de ser EXATAMENTE isto (nenhuma célula a mais na linha): parecido não basta.
DOMINIO_HEADER: tuple[str, ...] = ("codigo", "t", "classificacao", "nome", "grau")

#: Até onde o cabeçalho é procurado. O banner da amostra ocupa 4 linhas; 10 dá folga
#: sem transformar a detecção numa varredura do arquivo.
DOMINIO_HEADER_SEARCH_ROWS = 10

#: A marca de conta sintética na coluna `T`.
SYNTHETIC_MARK = "S"

#: A classificação hierárquica: números separados por ponto (`1`, `1.1.2.01.004`).
_CLASSIFICATION_PATTERN = re.compile(r"\d+(?:\.\d+)*")

#: Motivos PRÓPRIOS do layout do Domínio — vocabulário FECHADO, somado ao do modelo
#: (`sheet.ChartLineReason`) na mesma recusa `LINHAS_INVALIDAS`.
type DominioLineReason = Literal[
    # A linha está no bloco de contas mas não tem o código inteiro na 1ª célula.
    "codigo_ausente",
    # Nenhuma célula com a forma de classificação (`1.1.2`).
    "classificacao_ausente",
    # A mesma classificação em duas contas (a segunda ocorrência em diante).
    "classificacao_repetida",
    # O grau (inteiro no fim da linha) não existe.
    "grau_ausente",
    # O grau não é a profundidade da classificação: a leitura saiu do padrão.
    "grau_divergente",
    # Células fora do padrão (duas classificações, nome partido em duas células…).
    "linha_irreconhecivel",
    # Linha com cara de conta DEPOIS do fim do bloco: ela seria descartada calada.
    "conta_fora_do_bloco",
]


@dataclass(frozen=True, slots=True)
class DominioLineProblem:
    line: int
    reason: DominioLineReason


@dataclass(frozen=True, slots=True)
class DominioConversion:
    """As contas no formato do modelo, prontas para `sheet.validate_rows`.

    `rows` usa as colunas `MODEL_COLUMNS` (nessa ordem) e o número da linha FÍSICA
    da planilha, para a recusa apontar a linha que a pessoa vê no Excel.
    """

    rows: list[tuple[int, list[Any]]]
    problems: list[DominioLineProblem]

    def __repr__(self) -> str:
        # As linhas carregam nome de conta (dado do cliente final): nunca num repr.
        return f"<DominioConversion rows={len(self.rows)} problems={len(self.problems)}>"


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _filled(cells: Sequence[Any]) -> list[Any]:
    """As células preenchidas da linha, na ordem (as mescladas chegam `None`)."""
    return [cell for cell in cells if cell is not None]


def find_dominio_header(rows: Sequence[tuple[int, Sequence[Any]]]) -> int | None:
    """O número da linha do cabeçalho do Domínio nas primeiras linhas, ou `None`.

    Casa as células PREENCHIDAS da linha, na ordem, com `DOMINIO_HEADER` (aparadas,
    sem caixa e sem acento). As posições não importam: o cabeçalho real é
    desalinhado do dado.
    """
    for line, cells in rows[:DOMINIO_HEADER_SEARCH_ROWS]:
        filled = _filled(cells)
        if len(filled) != len(DOMINIO_HEADER):
            continue
        if all(isinstance(c, str) for c in filled) and (
            tuple(_fold(c) for c in filled) == DOMINIO_HEADER
        ):
            return line
    return None


def _as_int(value: Any) -> int | None:
    """Inteiro de célula NUMÉRICA (`649` ou `649.0`). Texto e booleano não contam."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _is_classification(value: Any) -> bool:
    return isinstance(value, str) and _CLASSIFICATION_PATTERN.fullmatch(value) is not None


def _looks_like_account(cells: Sequence[Any]) -> bool:
    """Código inteiro na 1ª célula OU alguma célula com forma de classificação.

    É a régua do fim do bloco e a do anti-perda: o que passa nela depois do bloco
    é uma conta que a leitura descartaria.
    """
    filled = _filled(cells)
    if not filled:
        return False
    return _as_int(filled[0]) is not None or any(_is_classification(c) for c in filled)


def _depth(classification: str) -> int:
    return classification.count(".") + 1


def _convert_line(
    cells: Sequence[Any],
) -> tuple[list[Any] | None, DominioLineReason | None]:
    """Uma linha do bloco → `[código, nome, tipo, classificação]` do modelo, ou o motivo."""
    filled = _filled(cells)
    code = _as_int(filled[0])
    if code is None:
        return None, "codigo_ausente"
    grade = _as_int(filled[-1]) if len(filled) >= 2 else None
    if grade is None:
        return None, "grau_ausente"
    middle = filled[1:-1]
    positions = [pos for pos, cell in enumerate(middle) if _is_classification(cell)]
    if not positions:
        return None, "classificacao_ausente"
    if len(positions) > 1:
        return None, "linha_irreconhecivel"
    split = positions[0]
    classification: str = middle[split]
    type_zone = middle[:split]
    name_zone = middle[split + 1 :]

    if not type_zone:
        account_type = AccountingAccountType.ANALITICA
    elif type_zone == [SYNTHETIC_MARK]:
        account_type = AccountingAccountType.SINTETICA
    else:
        # Qualquer outra marca é palpite (`A`? `s`?): o modelo recusa o mesmo caso.
        return None, "linha_irreconhecivel"

    if len(name_zone) > 1 or (name_zone and not isinstance(name_zone[0], str)):
        return None, "linha_irreconhecivel"
    name = name_zone[0] if name_zone else None

    if grade != _depth(classification):
        return None, "grau_divergente"
    # O nome vazio segue para o validador do modelo, que o recusa como `nome_vazio`.
    return [str(code), name, account_type.value, classification], None


def convert_dominio(
    rows: Sequence[tuple[int, Sequence[Any]]], header_line: int
) -> DominioConversion:
    """Converte o bloco de contas abaixo do cabeçalho; acumula TODOS os problemas.

    Função PURA. Linhas vazias logo depois do cabeçalho são puladas; o bloco termina
    na primeira linha sem cara de conta (`_looks_like_account`). Tudo depois dele só
    é conferido: linha com cara de conta ali vira `conta_fora_do_bloco`.
    """
    after_header = [(line, cells) for line, cells in rows if line > header_line]
    position = 0
    while position < len(after_header) and not _filled(after_header[position][1]):
        position += 1

    converted: list[tuple[int, list[Any]]] = []
    problems: list[DominioLineProblem] = []
    seen_classifications: set[str] = set()
    while position < len(after_header):
        line, cells = after_header[position]
        if not _looks_like_account(cells):
            break
        position += 1
        values, reason = _convert_line(cells)
        if reason is not None or values is None:
            problems.append(DominioLineProblem(line, reason or "linha_irreconhecivel"))
            continue
        classification = values[3]
        if classification in seen_classifications:
            problems.append(DominioLineProblem(line, "classificacao_repetida"))
            continue
        seen_classifications.add(classification)
        converted.append((line, values))

    problems.extend(
        DominioLineProblem(line, "conta_fora_do_bloco")
        for line, cells in after_header[position:]
        if _looks_like_account(cells)
    )
    return DominioConversion(rows=converted, problems=problems)
