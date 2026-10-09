"""Gerador MÍNIMO de `.xls` (BIFF8 num contêiner OLE2) para os testes (86e3n70p6).

Não existe escritor de `.xls` mantido, e os testes precisam de arquivos que nenhum
escritor faria: aba com BLANKs órfãos antes do BOF (o defeito do Domínio), registro
estranho no lugar errado, contêiner truncado, aba na última linha da BIFF8. Este módulo
monta os bytes à mão, registro a registro, com o que o xlrd precisa e nada mais.

Uma célula é `str` (texto, via SST), `int`/`float` (número), `bool`, `date` (número com
o formato de data 14) ou `None` (vazia, sem registro).

Referência dos registros: [MS-XLS] 2.4 (BOF, BOUNDSHEET, SST, LABELSST, NUMBER,
BOOLERR, BLANK, XF, DATEMODE, DIMENSIONS, EOF) e [MS-CFB] (contêiner, setor de 512).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SECTOR = 512
_FREE, _END, _FAT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
_NO_STREAM = 0xFFFFFFFF

BOF, EOF, BOUNDSHEET, SST, XF, DATEMODE = 0x0809, 0x000A, 0x0085, 0x00FC, 0x00E0, 0x0022
DIMENSIONS, LABELSST, NUMBER, BOOLERR, BLANK = 0x0200, 0x00FD, 0x0203, 0x0205, 0x0201
#: XF 0 = Geral (número), XF 1 = formato padrão 14 (data).
XF_GENERAL, XF_DATE = 0, 1


def record(code: int, data: bytes = b"") -> bytes:
    return struct.pack("<HH", code, len(data)) + data


def _bof(kind: int) -> bytes:
    return record(BOF, struct.pack("<HHHHII", 0x0600, kind, 0x0DBB, 0x07CC, 0, 0x06))


def _xf(fmt: int) -> bytes:
    return record(XF, struct.pack("<HHHBBBBIiH", 0, fmt, 0, 0x20, 0, 0, 0, 0, 0, 0x20C0))


def _excel_serial(value: date, datemode: int) -> float:
    epoch = datetime(1899, 12, 30) if datemode == 0 else datetime(1904, 1, 1)
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    return (moment - epoch).total_seconds() / 86400


@dataclass
class XlsBook:
    """Uma aba; `rows[i][j]` é a célula da linha i (0-based) e coluna j."""

    rows: list[list[Any]]
    datemode: int = 0
    #: BLANKs órfãos entre o EOF dos globais e o BOF da aba, com o BOUNDSHEET
    #: apontando para o primeiro deles (o export do Domínio).
    orphan_blanks: int = 0
    #: Registros arbitrários no mesmo trecho, DEPOIS dos BLANKs (o caso que recusa).
    orphan_extra: bytes = b""
    #: Células esparsas além de `rows`: `(linha, coluna, valor)`.
    extra_cells: list[tuple[int, int, Any]] = field(default_factory=list)
    #: Linhas declaradas no DIMENSIONS (por padrão, as de verdade).
    declared_rows: int | None = None
    #: Registros de célula prontos, depois das células (data inválida, erro…).
    raw_cells: bytes = b""

    def _sst(self) -> tuple[bytes, dict[str, int]]:
        strings: dict[str, int] = {}
        for row in self.rows:
            for value in row:
                if isinstance(value, str):
                    strings.setdefault(value, len(strings))
        for _, _, value in self.extra_cells:
            if isinstance(value, str):
                strings.setdefault(value, len(strings))
        body = b"".join(
            struct.pack("<HB", len(text), 0x01) + text.encode("utf-16-le") for text in strings
        )
        total = sum(1 for row in self.rows for v in row if isinstance(v, str))
        return record(SST, struct.pack("<ii", total, len(strings)) + body), strings

    def _cells(self, strings: dict[str, int]) -> bytes:
        out = bytearray()
        cells = [(r, c, v) for r, row in enumerate(self.rows) for c, v in enumerate(row)]
        for r, c, value in [*cells, *self.extra_cells]:
            if value is None:
                continue
            if isinstance(value, bool):
                out += record(BOOLERR, struct.pack("<HHHBB", r, c, XF_GENERAL, int(value), 0))
            elif isinstance(value, str):
                out += record(LABELSST, struct.pack("<HHHi", r, c, XF_GENERAL, strings[value]))
            elif isinstance(value, date):
                serial = _excel_serial(value, self.datemode)
                out += record(NUMBER, struct.pack("<HHHd", r, c, XF_DATE, serial))
            else:
                out += record(NUMBER, struct.pack("<HHHd", r, c, XF_GENERAL, float(value)))
        return bytes(out)

    def stream(self) -> bytes:
        """O stream `Workbook` (BIFF8 cru)."""
        sst, strings = self._sst()
        name = b"Plan1"
        boundsheet_len = 4 + len(struct.pack("<iBBBB", 0, 0, 0, 0, 0)) + len(name)
        head = _bof(0x0005) + record(DATEMODE, struct.pack("<H", self.datemode))
        head += _xf(0) + _xf(14)
        tail = sst + record(EOF)
        globals_end = len(head) + boundsheet_len + len(tail)
        boundsheet = record(
            BOUNDSHEET, struct.pack("<iBBBB", globals_end, 0, 0, len(name), 0) + name
        )
        globals_ = head + boundsheet + tail
        gap = record(BLANK, struct.pack("<HHH", 0, 0, 0)) * self.orphan_blanks + self.orphan_extra
        nrows = self.declared_rows if self.declared_rows is not None else len(self.rows)
        ncols = max((len(r) for r in self.rows), default=0)
        dims = record(DIMENSIONS, struct.pack("<iiHHH", 0, nrows, 0, ncols, 0))
        sheet = _bof(0x0010) + dims + self._cells(strings) + self.raw_cells + record(EOF)
        return globals_ + gap + sheet

    def to_bytes(self) -> bytes:
        return ole2(self.stream())


def ole2(stream: bytes, *, name: str = "Workbook") -> bytes:
    """Contêiner OLE2 (versão 3, setor de 512) com UM stream.

    O stream vai em setores normais: abaixo de 4096 bytes ele moraria no mini-stream,
    então é completado com zeros (o xlrd para no EOF da aba e não lê o resto).
    """
    stream = stream.ljust(4096, b"\0")
    data_sectors = -(-len(stream) // _SECTOR)
    fat_sectors = 1
    while fat_sectors * (_SECTOR // 4) < fat_sectors + 1 + data_sectors:
        fat_sectors += 1
    if fat_sectors > 109:
        raise ValueError("stream grande demais para o DIFAT do cabeçalho")
    dir_sector = fat_sectors
    first_data = fat_sectors + 1

    fat = [_FAT] * fat_sectors + [_END]
    fat += [first_data + i + 1 for i in range(data_sectors - 1)] + [_END]
    fat += [_FREE] * (fat_sectors * (_SECTOR // 4) - len(fat))

    difat = list(range(fat_sectors)) + [_FREE] * (109 - fat_sectors)
    header = OLE_SIGNATURE + b"\0" * 16
    header += struct.pack("<HHHHH", 0x003E, 0x0003, 0xFFFE, 9, 6) + b"\0" * 6
    header += struct.pack("<IIIIIIIII", 0, fat_sectors, dir_sector, 0, 4096, _END, 0, _END, 0)
    header += struct.pack("<109I", *difat)

    def entry(label: str, kind: int, child: int, start: int, size: int) -> bytes:
        encoded = (label + "\0").encode("utf-16-le") if label else b""
        out = encoded.ljust(64, b"\0") + struct.pack("<HBB", len(encoded), kind, 1)
        out += struct.pack("<III", _NO_STREAM, _NO_STREAM, child) + b"\0" * 16
        out += struct.pack("<I", 0) + b"\0" * 16 + struct.pack("<III", start, size, 0)
        return out

    directory = entry("Root Entry", 5, 1, _END, 0) + entry(
        name, 2, _NO_STREAM, first_data, len(stream)
    )
    directory += entry("", 0, _NO_STREAM, 0, 0) * 2
    body = (
        struct.pack(f"<{len(fat)}I", *fat) + directory + stream.ljust(data_sectors * _SECTOR, b"\0")
    )
    return header + body
