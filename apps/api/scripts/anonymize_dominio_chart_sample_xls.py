"""Gera a FIXTURE anonimizada do plano de contas do Domínio em `.xls` (86e3n70p6).

Molde: `anonymize_dominio_chart_sample.py` (o `.xlsx`, 86e3gkd7y). A decisão do que
trocar é a MESMA, estrutural e nunca por conteúdo: rótulos do banner, cabeçalho, marca
`S`, classificação, código e grau ficam; o valor do banner, o NOME de toda conta, o
rodapé e o nome da aba são trocados. O que muda é o COMO.

Não existe escritor de `.xls` confiável (o xlwt está abandonado), e regravar o arquivo
apagaria justamente o que o teste precisa provar: o export do Domínio tem milhares de
registros BLANK entre os globais e a aba, com o índice da aba apontando para eles (ver
`reader._first_sheet_at_bof`). Por isso a troca é NO LUGAR: cada string do SST recebe
um texto fictício com o MESMO número de caracteres, escrito em cada pedaço com a
codificação daquele pedaço (a string pode ser partida entre registros CONTINUE e trocar
de 8 para 16 bits no meio). Nenhum byte muda de posição: contêiner OLE2, registros,
deslocamentos e o defeito do Domínio ficam idênticos ao original.

A conferência final relê o SST da fixture e procura cada texto trocado nos bytes do
arquivo inteiro, em latin-1 e em UTF-16; imprime só contagens. O script não imprime
célula nenhuma.

Uso (de `apps/api/`; o caminho da amostra é argumento, nunca escrito aqui):

    uv run python -m scripts.anonymize_dominio_chart_sample_xls <amostra.xls> <saida_dir>

Saída: `<saida_dir>/plano_dominio.xls` e `<saida_dir>/plano_esperado.csv`.
"""

from __future__ import annotations

import csv
import io
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from xlrd import compdoc

from app.modules.client_accounting_chart.dominio import SYNTHETIC_MARK, find_dominio_header
from app.modules.client_accounting_chart.sheet import MODEL_COLUMNS, parse_chart_sheet
from app.modules.client_file_ingestion.reader import read_sheet_raw_rows
from scripts.anonymize_dominio_chart_sample import (
    _CLASSIFICATION,
    _FILLER,
    BANNER_LABELS,
    BANNER_VALUES,
    FOOTER_TEXT,
)

_SST = 0x00FC
_CONTINUE = 0x003C
_BOUNDSHEET = 0x0085
_LABELSST = 0x00FD
_EOF = 0x000A
_BOF = 0x0809
_HEADER_TEXTS = frozenset({"Código", "T", "Classificação", "Nome", "Grau"})
SHEET_TITLE = "Contas"


@dataclass
class _Text:
    """Uma string do arquivo: o texto e onde moram os caracteres no stream."""

    value: str
    #: `(posição no stream, caracteres, 16 bits?)` de cada pedaço.
    segments: list[tuple[int, int, bool]] = field(default_factory=list)


def _fit(text: str, length: int) -> str:
    """`text` com EXATAMENTE `length` caracteres (cortado ou completado)."""
    return (text + _FILLER * (1 + length // len(_FILLER)))[:length]


def _workbook_sectors(content: bytes) -> tuple[list[int], int, int]:
    """Setores do stream `Workbook`, tamanho do setor e tamanho do stream."""
    doc = compdoc.CompDoc(content, logfile=io.StringIO())
    node = next(d for d in doc.dirlist if d.name == "Workbook")
    if node.tot_size < 4096:
        sys.exit("stream pequeno (mini-stream): fora do que este script sabe escrever")
    sectors: list[int] = []
    sid = node.first_SID
    while sid >= 0:
        sectors.append(sid)
        sid = doc.SAT[sid]
    return sectors, doc.sec_size, node.tot_size


def _records(stream: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    """`(código, posição do dado, tamanho)` dos registros em `[start, end)`."""
    out: list[tuple[int, int, int]] = []
    pos = start
    while pos < end:
        code, length = struct.unpack_from("<HH", stream, pos)
        out.append((code, pos + 4, length))
        pos += 4 + length
    return out


def _sst_texts(stream: bytes, records: list[tuple[int, int, int]]) -> list[_Text]:
    """As strings do SST com os pedaços, no MESMO percurso de `xlrd.unpack_SST_table`."""
    first = next(i for i, (code, _, _) in enumerate(records) if code == _SST)
    chunks = [records[first]]
    for rec in records[first + 1 :]:
        if rec[0] != _CONTINUE:
            break
        chunks.append(rec)
    base, size = chunks[0][1], chunks[0][2]
    count: int = struct.unpack_from("<i", stream, base + 4)[0]
    index, pos = 0, 8
    texts: list[_Text] = []
    for _ in range(count):
        nchars, options = struct.unpack_from("<HB", stream, base + pos)
        pos += 3
        runs = phonetic = 0
        if options & 0x08:
            runs = struct.unpack_from("<H", stream, base + pos)[0]
            pos += 2
        if options & 0x04:
            phonetic = struct.unpack_from("<i", stream, base + pos)[0]
            pos += 4
        text = _Text("")
        got = 0
        while True:
            wide = bool(options & 0x01)
            width = 2 if wide else 1
            avail = min((size - pos) // width, nchars - got)
            raw = stream[base + pos : base + pos + avail * width]
            text.value += raw.decode("utf-16-le" if wide else "latin-1")
            if avail:
                text.segments.append((base + pos, avail, wide))
            pos += avail * width
            got += avail
            if got == nchars:
                break
            index += 1
            base, size = chunks[index][1], chunks[index][2]
            options = stream[base]
            pos = 1
        for _ in range(runs):
            if pos == size:
                index += 1
                base, size, pos = chunks[index][1], chunks[index][2], 0
            pos += 4
        pos += phonetic
        if pos >= size:
            pos -= size
            index += 1
            if index < len(chunks):
                base, size = chunks[index][1], chunks[index][2]
        texts.append(text)
    return texts


def _sheet_name(stream: bytes, records: list[tuple[int, int, int]]) -> _Text:
    pos = next(p for code, p, _ in records if code == _BOUNDSHEET)
    nchars, options = struct.unpack_from("<BB", stream, pos + 6)
    wide = bool(options & 0x01)
    start = pos + 8
    raw = stream[start : start + nchars * (2 if wide else 1)]
    return _Text(raw.decode("utf-16-le" if wide else "latin-1"), [(start, nchars, wide)])


def _cell_sst_index(stream: bytes) -> dict[tuple[int, int], int]:
    """`(linha 0-based, coluna) → índice no SST` de cada LABELSST da primeira aba."""
    # O BOF da aba é o SEGUNDO do stream (o primeiro abre os globais); percorrer
    # registro a registro, nunca procurar o padrão de bytes, que casaria num dado.
    bofs = [pos - 4 for code, pos, _ in _records(stream, 0, len(stream)) if code == _BOF]
    out: dict[tuple[int, int], int] = {}
    for code, pos, _ in _records(stream, bofs[1], len(stream)):
        if code == _LABELSST:
            row, col, _xf, isst = struct.unpack_from("<HHHi", stream, pos)
            out[(row, col)] = isst
        if code == _EOF:
            break
    return out


def _decide(content: bytes, cells: dict[tuple[int, int], int], texts: list[_Text]) -> list[str]:
    """O texto final de cada string do SST (o original quando é estrutura)."""
    raw = read_sheet_raw_rows(content)
    header_line = find_dominio_header(raw)
    if header_line is None:
        sys.exit("cabeçalho do Domínio não encontrado: o layout mudou, nada foi gerado")
    keep: set[int] = set()
    replace: dict[int, str] = {}

    def put(line: int, col: int, new: str | None) -> None:
        isst = cells[(line - 1, col)]
        if new is None:
            keep.add(isst)
        else:
            replace.setdefault(isst, _fit(new, len(texts[isst].value)))

    in_block = True
    for line, row in raw:
        filled = [(col, v) for col, v in enumerate(row) if v is not None]
        strings = [(col, v) for col, v in filled if isinstance(v, str)]
        if line < header_line:
            label = None
            for col, value in strings:
                if value in BANNER_LABELS:
                    label = value
                    put(line, col, None)
                else:
                    put(line, col, BANNER_VALUES.get(label or "", "[removido]"))
            continue
        if line == header_line:
            for col, _ in strings:
                put(line, col, None)
            continue
        if not filled:
            if in_block and line > header_line + 1:
                in_block = False
            continue
        values = [v for _, v in filled]
        looks_like_account = (
            isinstance(values[0], int) and not isinstance(values[0], bool)
        ) or any(isinstance(v, str) and _CLASSIFICATION.fullmatch(v) for v in values)
        if in_block and looks_like_account:
            split = next(
                i
                for i, v in enumerate(values)
                if isinstance(v, str) and _CLASSIFICATION.fullmatch(v)
            )
            classification = values[split]
            synthetic = SYNTHETIC_MARK in values[1:split]
            for i, (col, value) in enumerate(filled):
                if not isinstance(value, str):
                    continue
                if split < i < len(filled) - 1:
                    kind = "Grupo" if synthetic else "Conta"
                    put(line, col, f"{kind} de exemplo {classification}")
                else:
                    put(line, col, None)
            continue
        in_block = False
        for col, _ in strings:
            put(line, col, FOOTER_TEXT)

    clash = keep & set(replace)
    if clash:
        sys.exit(f"{len(clash)} string(s) usada(s) como estrutura E como dado: nada foi gerado")
    final: list[str] = []
    for isst, text in enumerate(texts):
        if (
            isst in keep
            and text.value.strip() in (_HEADER_TEXTS | BANNER_LABELS | {SYNTHETIC_MARK})
        ) or (isst in keep and _CLASSIFICATION.fullmatch(text.value.strip())):
            final.append(text.value)
        else:
            # Sem referência (ou estrutura inesperada): troca também, na dúvida.
            final.append(replace.get(isst, _fit("[removido]", len(text.value))))
    return final


def _write(stream: bytearray, text: _Text, new: str) -> None:
    if len(new) != len(text.value):
        raise AssertionError("o texto novo precisa do mesmo comprimento")
    cursor = 0
    for pos, nchars, wide in text.segments:
        piece = new[cursor : cursor + nchars]
        encoded = piece.encode("utf-16-le" if wide else "latin-1")
        stream[pos : pos + len(encoded)] = encoded
        cursor += nchars


def anonymize(source: Path, out_dir: Path) -> None:
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
    final = _decide(content, _cell_sst_index(bytes(stream)), texts)

    replaced = {t.value.strip() for t, new in zip(texts, final, strict=True) if new != t.value}
    for text, new in zip(texts, final, strict=True):
        _write(stream, text, new)
    _write(stream, sheet, _fit(SHEET_TITLE, len(sheet.value)))
    if sheet.value != _fit(SHEET_TITLE, len(sheet.value)):
        replaced.add(sheet.value.strip())

    out = bytearray(content)
    for i, s in enumerate(sectors):
        chunk = stream[i * sec_size : (i + 1) * sec_size]
        start = 512 + s * sec_size
        if start + len(chunk) > len(out):
            sys.exit("setor fora do arquivo: nada foi gerado")
        out[start : start + len(chunk)] = chunk

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "plano_dominio.xls"
    target.write_bytes(bytes(out))

    original = parse_chart_sheet(content)
    parsed = parse_chart_sheet(target.read_bytes())
    same_structure = [
        (r.line, r.code, r.account_type, r.classification) for r in original.rows
    ] == [(r.line, r.code, r.account_type, r.classification) for r in parsed.rows]
    if not same_structure or any(
        a.name == b.name for a, b in zip(original.rows, parsed.rows, strict=True)
    ):
        sys.exit("a fixture não tem a estrutura da amostra (ou um nome não foi trocado)")
    with (out_dir / "plano_esperado.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\n")
        writer.writerow(["linha", *MODEL_COLUMNS])
        for r in parsed.rows:
            writer.writerow([r.line, r.code, r.name, r.account_type.value, r.classification])
    _check_no_leak(replaced, target)
    print(f"contas={len(parsed.rows)} layout={parsed.layout} bytes={len(out)}")


def _check_no_leak(originals: set[str], target: Path) -> None:
    """Nenhum texto trocado sobrou: nem no SST relido, nem em byte nenhum do arquivo."""
    content = target.read_bytes()
    sectors, sec_size, size = _workbook_sectors(content)
    stream = b"".join(content[512 + s * sec_size : 512 + (s + 1) * sec_size] for s in sectors)[
        :size
    ]
    globals_end = next(p + n for code, p, n in _records(stream, 0, len(stream)) if code == _EOF)
    sst = {t.value.strip() for t in _sst_texts(stream, _records(stream, 0, globals_end))}
    originals = {text for text in originals if text}
    leaked = sum(
        1
        for text in originals
        if text in sst
        or text.encode("latin-1", errors="ignore") in content
        or text.encode("utf-16-le") in content
    )
    print(f"textos_originais={len(originals)} encontrados_na_fixture={leaked}")
    if leaked:
        sys.exit("texto da amostra sobrou na fixture: NÃO versione o arquivo gerado")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    anonymize(Path(sys.argv[1]), Path(sys.argv[2]))
