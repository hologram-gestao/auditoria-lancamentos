"""Exportação do plano contábil no MODELO DA PLATAFORMA (86e3qhhrm).

O arquivo é o mesmo formato que a importação lê (`sheet.py`): CSV UTF-8 com BOM,
separado por `;`, cabeçalho `codigo_reduzido;nome;tipo;classificacao` e uma linha por
conta, na ordem da classificação (a do plano). Três usos: conferir o plano fora da
plataforma, reimportá-lo depois de editar e servir de insumo para a planilha de
importação da Omie (subtask 4 do épico 86e3qhhr0).

**Exportar e reimportar o mesmo arquivo não muda nada** (critério de aceite): as
colunas são as do modelo, o tipo sai no vocabulário que a leitura aceita, a
classificação vazia sai vazia e o nome sai como está gravado. A única transformação é
a da fórmula: nome que começa com `= + - @` ganha um apóstrofo na frente
(`sheet.neutralize_formula`), porque o Excel executaria o resto como fórmula ao abrir
o CSV, e a leitura tira o apóstrofo de volta (`sheet.restore_formula`).

O BOM está aí para o Excel abrir o arquivo como UTF-8 (sem ele, os acentos viram
lixo); a leitura aceita os dois. Situação e selo de ausente NÃO saem: o modelo não tem
essas colunas, e a planilha não muda a situação.

Função PURA: recebe as contas e os nomes já decifrados, devolve os bytes.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

from app.modules.client_accounting_chart.sheet import (
    COLUMN_CLASSIFICATION,
    COLUMN_CODE,
    COLUMN_NAME,
    COLUMN_TYPE,
    neutralize_formula,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from app.db.models.client_accounting_account import ClientAccountingAccount

#: A ordem das colunas no arquivo — a do exemplo do modelo.
EXPORT_COLUMNS: tuple[str, ...] = (COLUMN_CODE, COLUMN_NAME, COLUMN_TYPE, COLUMN_CLASSIFICATION)
#: O separador e a codificação do modelo (o `-sig` grava o BOM).
EXPORT_DELIMITER = ";"
EXPORT_ENCODING = "utf-8-sig"
EXPORT_MEDIA_TYPE = "text/csv; charset=utf-8"


def build_chart_csv(
    accounts: Sequence[ClientAccountingAccount], names: Mapping[UUID, str]
) -> bytes:
    """O plano em CSV no modelo. `accounts` já vem na ordem do plano; `names` tem
    TODOS os nomes decifrados (quem chama recusa antes se algum não abriu)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=EXPORT_DELIMITER, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for account in accounts:
        writer.writerow(
            (
                account.code,
                neutralize_formula(names[account.id]),
                account.account_type,
                account.classification or "",
            )
        )
    return buffer.getvalue().encode(EXPORT_ENCODING)


def export_filename(client_id: UUID) -> str:
    """Nome do arquivo: só o início do id do cliente, nunca o nome (§4.7)."""
    return f"plano-contabil-{client_id.hex[:8]}.csv"
