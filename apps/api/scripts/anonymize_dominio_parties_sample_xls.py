"""Gera a FIXTURE anonimizada das relações de clientes e fornecedores do Domínio (86e3qhhrn).

Molde: `anonymize_dominio_chart_sample_xls.py` (o plano em `.xls`, 86e3n70p6), de onde
vêm a leitura do SST, a troca NO LUGAR (cada string recebe um texto com o MESMO número de
caracteres, nenhum byte muda de posição: o contêiner OLE2 e o defeito dos BLANKs órfãos
ficam idênticos) e a conferência final de vazamento.

O que muda é a DECISÃO do que trocar, estrutural e nunca por conteúdo:

- ficam: os rótulos do banner (`Empresa:`, `C.N.P.J.:`, `Folha:`, `Emissão:`, `Hora:`), o
  TÍTULO do relatório (é de onde o tipo do vínculo sai), o cabeçalho, a linha de total e
  os números (código, conta e datas são células numéricas, fora do SST);
- trocam: o nome da empresa (`EMPRESA EXEMPLO LTDA`), o CNPJ dela (`00.000.000/0001-91`,
  válido), o nome e a razão social de toda pessoa (`Cliente de exemplo N` /
  `Fornecedor de exemplo N`, pelo comprimento do original) e o DOCUMENTO: um CNPJ ou
  CPF fictício, VÁLIDO, determinístico pela ordem da pessoa, preservando o tamanho (14 ou
  11) e preservando a INVALIDADE quando o original tem dígito verificador errado (nas
  duas amostras não há nenhum). A mesma pessoa que aparece nos dois relatórios não é
  correlacionada: cada arquivo recebe a própria sequência. O nome da aba é o título do
  relatório e fica.

Uso (de `apps/api/`; os caminhos das amostras são argumentos, nunca escritos aqui):

    uv run python -m scripts.anonymize_dominio_parties_sample_xls \\
        "<RELAÇÃO DE CLIENTES.xls>" "<RELAÇÃO DE FORNECEDORES.xls>" <saida_dir>

Saída: `<saida_dir>/relacao_clientes.xls`, `<saida_dir>/relacao_fornecedores.xls` e
`<saida_dir>/vinculos_esperados.csv` (linha, relatório, conta, documento fictício, tipo
do documento e situação: `vinculavel` ou o motivo do pulo).
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from app.core.cnpj import normalize_cnpj
from app.core.cpf import normalize_cpf
from app.modules.client_accounting_chart.parties import (
    PARTY_REPORT_TITLES,
    PartiesReport,
    _fold,
    find_parties_header,
    parse_parties_report,
)
from app.modules.client_file_ingestion.reader import read_sheet_raw_rows
from scripts.anonymize_dominio_chart_sample import BANNER_LABELS
from scripts.anonymize_dominio_chart_sample_xls import (
    _EOF,
    _cell_sst_index,
    _check_no_leak,
    _fit,
    _records,
    _sheet_name,
    _sst_texts,
    _workbook_sectors,
    _write,
)

#: Rótulos do banner das relações (os do plano mais os destas).
_LABELS = BANNER_LABELS | {"Emissão:", "Hora:"}
_COMPANY_VALUE = {"Empresa:": "EMPRESA EXEMPLO LTDA", "C.N.P.J.:": "00.000.000/0001-91"}
_TOTAL_PREFIX = "total de"
_SHEET_TITLE = "Relatório"


def _cnpj_check_digits(base12: str) -> str:
    weights_first = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    weights_second = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

    def digit(digits: str, weights: tuple[int, ...]) -> int:
        remainder = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
        return 0 if remainder < 2 else 11 - remainder

    first = digit(base12, weights_first)
    second = digit(base12 + str(first), weights_second)
    return f"{first}{second}"


def _cpf_check_digits(base9: str) -> str:
    def digit(digits: str, first_weight: int) -> int:
        total = sum(int(d) * w for d, w in zip(digits, range(first_weight, 1, -1), strict=True))
        remainder = (total * 10) % 11
        return 0 if remainder == 10 else remainder

    first = digit(base9, 10)
    second = digit(base9 + str(first), 11)
    return f"{first}{second}"


def fake_document(index: int, *, length: int, valid: bool, leading_zero: bool = False) -> str:
    """Um documento fictício de `length` dígitos (14 CNPJ, 11 CPF), válido ou não.

    `leading_zero` preserva o traço do original que COMEÇA com zero (um CNPJ da relação
    de clientes): é o caso que uma leitura por `int` destruiria, e a fixture tem de
    continuar provando isso.
    """
    if length == 14:
        base = (f"{index:08d}" if leading_zero else f"{10000000 + index:08d}") + "0001"
        digits = base + _cnpj_check_digits(base)
    elif length == 11:
        base = f"{index:09d}" if leading_zero else f"{100000000 + index:09d}"
        digits = base + _cpf_check_digits(base)
    else:
        raise ValueError("documento fictício só com 11 ou 14 dígitos")
    if not valid:
        # Troca o último verificador por outro dígito: continua com o tamanho certo.
        digits = digits[:-1] + str((int(digits[-1]) + 1) % 10)
    return digits


def _is_valid(digits: str) -> bool:
    try:
        if len(digits) == 14:
            normalize_cnpj(digits)
        elif len(digits) == 11:
            normalize_cpf(digits)
        else:
            return False
    except ValueError:
        return False
    return True


def _decide(
    content: bytes, cells: dict[tuple[int, int], int], texts: list[str], kind_label: str
) -> list[str]:
    """O texto final de cada string do SST (o original quando é estrutura)."""
    raw = read_sheet_raw_rows(content)
    header = find_parties_header(raw)
    if header is None:
        sys.exit("cabeçalho da relação não encontrado: o layout mudou, nada foi gerado")
    header_line, columns = header
    keep: set[int] = set()
    replace: dict[int, str] = {}

    def put(line: int, col: int, new: str | None) -> None:
        isst = cells[(line - 1, col)]
        if new is None:
            keep.add(isst)
        else:
            replace.setdefault(isst, _fit(new, len(texts[isst])))

    person = 0
    in_block = True
    for line, row in raw:
        strings = [(col, v) for col, v in enumerate(row) if isinstance(v, str)]
        if line < header_line:
            label = None
            for col, value in strings:
                folded = value.strip()
                if folded in _LABELS:
                    label = folded
                    put(line, col, None)
                elif _fold(folded) in PARTY_REPORT_TITLES:
                    put(line, col, None)
                else:
                    put(line, col, _COMPANY_VALUE.get(label or "", "[removido]"))
            continue
        if line == header_line:
            for col, _ in strings:
                put(line, col, None)
            continue
        if not strings and all(v is None for v in row):
            continue
        account = row[columns["conta"]] if columns["conta"] < len(row) else None
        document = row[columns["documento"]] if columns["documento"] < len(row) else None
        is_person = in_block and (
            isinstance(account, int) or (isinstance(document, str) and document.strip().isdigit())
        )
        if is_person:
            person += 1
            for col, value in strings:
                if col == columns["documento"]:
                    digits = re.sub(r"\D", "", value)
                    length = len(digits) if len(digits) in (11, 14) else 14
                    put(
                        line,
                        col,
                        fake_document(
                            person,
                            length=length,
                            valid=_is_valid(digits),
                            leading_zero=digits.startswith("0"),
                        ),
                    )
                elif col in (columns["nome"], columns["razao_social"]):
                    put(line, col, f"{kind_label} de exemplo {person}")
                else:
                    put(line, col, "[removido]")
            continue
        in_block = False
        for col, value in strings:
            if value.strip().lower().startswith(_TOTAL_PREFIX):
                put(line, col, None)
            else:
                put(line, col, "[removido]")

    clash = keep & set(replace)
    if clash:
        sys.exit(f"{len(clash)} string(s) usada(s) como estrutura E como dado: nada foi gerado")
    final: list[str] = []
    for isst, text in enumerate(texts):
        if isst in keep:
            final.append(text)
        else:
            final.append(replace.get(isst, _fit("[removido]", len(text))))
    return final


def anonymize_one(source: Path, target: Path, kind_label: str) -> PartiesReport:
    content = source.read_bytes()
    sectors, sec_size, size = _workbook_sectors(content)
    stream = bytearray(
        b"".join(content[512 + s * sec_size : 512 + (s + 1) * sec_size] for s in sectors)[:size]
    )
    globals_end = next(
        p + n for code, p, n in _records(bytes(stream), 0, len(stream)) if code == _EOF
    )
    records = _records(bytes(stream), 0, globals_end)
    texts = _sst_texts(bytes(stream), records)
    sheet = _sheet_name(bytes(stream), records)
    final = _decide(content, _cell_sst_index(bytes(stream)), [t.value for t in texts], kind_label)

    replaced = {t.value.strip() for t, new in zip(texts, final, strict=True) if new != t.value}
    for text, new in zip(texts, final, strict=True):
        _write(stream, text, new)
    # O nome da aba é o TÍTULO do relatório ("RELAÇÃO DE CLIENTES"): estrutura, fica.
    if _fold(sheet.value) not in PARTY_REPORT_TITLES:
        _write(stream, sheet, _fit(_SHEET_TITLE, len(sheet.value)))
        replaced.add(sheet.value.strip())

    out = bytearray(content)
    for i, s in enumerate(sectors):
        chunk = stream[i * sec_size : (i + 1) * sec_size]
        start = 512 + s * sec_size
        if start + len(chunk) > len(out):
            sys.exit("setor fora do arquivo: nada foi gerado")
        out[start : start + len(chunk)] = chunk
    target.write_bytes(bytes(out))

    original = parse_parties_report(content)
    parsed = parse_parties_report(target.read_bytes())
    same_structure = [(r.line, r.account_code, r.kind) for r in original.lines] == [
        (r.line, r.account_code, r.kind) for r in parsed.lines
    ] and [(p.line, p.reason, p.account_code) for p in original.skipped] == [
        (p.line, p.reason, p.account_code) for p in parsed.skipped
    ]
    if not same_structure or any(
        a.document == b.document for a, b in zip(original.lines, parsed.lines, strict=True)
    ):
        sys.exit("a fixture não tem a estrutura da amostra (ou um documento não foi trocado)")
    _check_no_leak(replaced, target)
    print(
        f"{target.name}: tipo={parsed.party_type.value} vinculaveis={len(parsed.lines)} "
        f"puladas={len(parsed.skipped)} bytes={len(out)}"
    )
    return parsed


def _expected_order(row: tuple[int, str, str, str, str, str]) -> tuple[str, int]:
    """Relatório, depois a linha física: a ordem do CSV esperado."""
    return row[1], row[0]


def anonymize(clients: Path, suppliers: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[int, str, str, str, str, str]] = []
    for source, target, label in (
        (clients, out_dir / "relacao_clientes.xls", "Cliente"),
        (suppliers, out_dir / "relacao_fornecedores.xls", "Fornecedor"),
    ):
        parsed = anonymize_one(source, target, label)
        rows += [
            (r.line, parsed.party_type.value, r.account_code, r.document, r.kind, "vinculavel")
            for r in parsed.lines
        ]
        rows += [
            (p.line, parsed.party_type.value, p.account_code or "", "", "", p.reason)
            for p in parsed.skipped
        ]
    with (out_dir / "vinculos_esperados.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\n")
        writer.writerow(["linha", "relatorio", "conta", "documento", "tipo_documento", "situacao"])
        ordered = sorted(rows, key=_expected_order)
        writer.writerows(ordered)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    anonymize(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
