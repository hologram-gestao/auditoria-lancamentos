"""O que uma planilha muda no plano contábil do cliente (86e3qhhrm).

Função PURA, sem banco e sem cifra: recebe as linhas JÁ validadas da planilha, o
plano atual e os nomes atuais JÁ decifrados, e devolve a diferença. É a MESMA
diferença para a PRÉVIA (que só mostra) e para a IMPORTAÇÃO (que grava): as duas não
podem discordar sobre o que vai acontecer, então nenhuma delas calcula por conta
própria.

A regra da reimportação (decisão do Pedro, 14/10/2026), que esta função descreve:

- código que o cliente não tem → conta **nova** (entra ativa);
- código que o cliente tem → **alterada** se nome, tipo ou classificação mudaram;
  igual, fica como está. A situação (ativa/inativa) NUNCA muda pela planilha: ela é
  da edição manual (86e3nb816);
- conta do cliente que não veio na planilha → **ausente**: continua como estava
  (ativa ou não) e ganha o selo "não veio na última importação" (`absent_since`);
- conta com o selo que voltou na planilha → **voltou**: o selo sai.

Nome que não decifra (`failed`) conta como alterado: a importação regrava com o nome
da planilha, que é o estado honesto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from uuid import UUID

    from app.db.models.client_accounting_account import ClientAccountingAccount
    from app.modules.client_accounting_chart.sheet import ChartSheetRow

#: O que a planilha mudou numa conta existente — vocabulário FECHADO (o mesmo da
#: edição manual, sem a `situacao`, que a planilha não muda).
type ChartRowChange = Literal["nome", "tipo", "classificacao"]


@dataclass(frozen=True, slots=True)
class ChangedAccount:
    """Uma conta existente que a planilha altera: a linha nova e o que muda."""

    account: ClientAccountingAccount
    row: ChartSheetRow
    changes: tuple[ChartRowChange, ...]


@dataclass(frozen=True, slots=True)
class ChartDiff:
    """A diferença entre a planilha e o plano, na ordem da planilha (e do plano, nas ausentes).

    `returned` são as contas com o selo que voltaram: podem estar também em
    `changed` (voltaram com nome novo) ou não (voltaram iguais). `unchanged_rows`
    são as linhas de conta existente sem mudança, inclusive as que voltaram.
    """

    new_rows: tuple[ChartSheetRow, ...]
    changed: tuple[ChangedAccount, ...]
    absent: tuple[ClientAccountingAccount, ...]
    returned: tuple[ClientAccountingAccount, ...]
    unchanged_rows: tuple[ChartSheetRow, ...]
    #: Contas INATIVAS que vieram na planilha: a importação não as reativa (só a
    #: edição manual), e a prévia diz quantas são para a pessoa saber.
    inactive_present: tuple[ClientAccountingAccount, ...]

    @property
    def has_changes(self) -> bool:
        """Se a importação mudaria o plano: conta nova, alterada ou ausente nova.

        O selo que SÓ se mantém (a conta já estava ausente) não é mudança; o que sai
        (a conta voltou) é.
        """
        newly_absent = any(account.absent_since is None for account in self.absent)
        return bool(self.new_rows or self.changed or self.returned or newly_absent)


def diff_chart(
    rows: Sequence[ChartSheetRow],
    existing: Sequence[ClientAccountingAccount],
    *,
    names: Mapping[UUID, str],
    failed: Collection[UUID] = (),
) -> ChartDiff:
    """A diferença que a planilha `rows` faz sobre o plano `existing`.

    `names` são os nomes decifrados das contas existentes; quem não está nele, ou está
    em `failed`, tem o nome tratado como desconhecido (portanto alterado).
    """
    by_code = {account.code: account for account in existing}
    new_rows: list[ChartSheetRow] = []
    changed: list[ChangedAccount] = []
    unchanged: list[ChartSheetRow] = []
    returned: list[ClientAccountingAccount] = []
    inactive_present: list[ClientAccountingAccount] = []
    present: set[str] = set()
    for row in rows:
        present.add(row.code)
        account = by_code.get(row.code)
        if account is None:
            new_rows.append(row)
            continue
        if account.absent_since is not None:
            returned.append(account)
        if not account.active:
            inactive_present.append(account)
        changes = _row_changes(row, account, names=names, failed=failed)
        if changes:
            changed.append(ChangedAccount(account=account, row=row, changes=changes))
        else:
            unchanged.append(row)
    absent = tuple(account for account in existing if account.code not in present)
    return ChartDiff(
        new_rows=tuple(new_rows),
        changed=tuple(changed),
        absent=absent,
        returned=tuple(returned),
        unchanged_rows=tuple(unchanged),
        inactive_present=tuple(inactive_present),
    )


def _row_changes(
    row: ChartSheetRow,
    account: ClientAccountingAccount,
    *,
    names: Mapping[UUID, str],
    failed: Collection[UUID],
) -> tuple[ChartRowChange, ...]:
    changes: list[ChartRowChange] = []
    current_name = None if account.id in failed else names.get(account.id)
    if row.name != current_name:
        changes.append("nome")
    if row.account_type.value != account.account_type:
        changes.append("tipo")
    if row.classification != account.classification:
        changes.append("classificacao")
    return tuple(changes)
