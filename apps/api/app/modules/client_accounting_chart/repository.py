"""Camada de dados do plano contábil do cliente (Sprint 16, BACK 16.1).

SQL puro, uma função por query. **Toda query carrega `client_id` no próprio
`WHERE`** — conta de outro cliente não é carregada, então o miss vira 404 sem
vazar existência (§3.15). O tenant vem do `Client` já validado pela rota.

**Escritas em lote são Core, não ORM** (§7 Backend, ADR-042-QA): `insert`/`update`
sobre `ClientAccountingAccount.__table__`, com `client_id` no WHERE da atualização.
O ORM com lista de parâmetros e WHERE além da pk levanta `InvalidRequestError: bulk
synchronize…` com sessão real — foi um 500 em todo envio de arquivo na S14.

**A ordem é a da CLASSIFICAÇÃO** (86e3n70p9): toda listagem ordena por `sort_key`
(derivada na gravação por `sort_key.chart_sort_key`, `NULLS LAST` para a linha que
código anterior tenha gravado sem ela), depois `code` e `id` para a página ser
estável. A ordem mora AQUI, num lugar só: o seletor de conta do de-para e a conta do
banco leem a mesma lista e herdam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import Table, bindparam, func, insert, select, text, update

from app.db.models.client_accounting_account import ClientAccountingAccount
from app.db.models.client_mapping import ClientMappingDecision
from app.db.models.client_source_account_binding import ClientSourceAccountBinding

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute
    from sqlalchemy.sql import ColumnElement, Select


@dataclass(frozen=True, slots=True)
class AccountingChartFilters:
    """Recortes da listagem. `None` = sem recorte."""

    code_prefix: str | None = None
    account_type: str | None = None
    active: bool | None = None


def _escape_like(term: str) -> str:
    """Neutraliza os curingas do `LIKE`: a pessoa digitou um código, não um padrão."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _table() -> Table:
    return cast(Table, ClientAccountingAccount.__table__)


def _chart_order() -> tuple[ColumnElement[Any] | InstrumentedAttribute[Any], ...]:
    """O `ORDER BY` ÚNICO do plano: classificação (`sort_key`), depois código e id.

    `NULLS LAST` cobre a linha gravada sem a chave por código anterior à coluna
    (janela de deploy): ela vai para o fim, ordenada pelo código, em vez de sumir
    ou de vir na frente.
    """
    return (
        ClientAccountingAccount.sort_key.nulls_last(),
        ClientAccountingAccount.code,
        ClientAccountingAccount.id,
    )


class AccountingChartRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_query(self, client_id: UUID) -> Select[tuple[ClientAccountingAccount]]:
        return select(ClientAccountingAccount).where(ClientAccountingAccount.client_id == client_id)

    # ------------------------------------------------------------------ leitura

    async def get(self, client_id: UUID, account_id: UUID) -> ClientAccountingAccount | None:
        """A conta pela pk, SÓ se for do cliente — a de outro cliente não é carregada."""
        stmt = self._base_query(client_id).where(ClientAccountingAccount.id == account_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_for_update(
        self, client_id: UUID, account_id: UUID
    ) -> ClientAccountingAccount | None:
        """A conta pela pk, SÓ se for do cliente, travada até o fim da transação.

        A edição manual (86e3nb816) lê, decide e grava: a linha não muda no meio.
        `populate_existing` porque a mesma conta pode já estar na sessão.
        """
        stmt = (
            self._base_query(client_id)
            .where(ClientAccountingAccount.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count_usage(self, client_id: UUID, account_id: UUID) -> tuple[int, int]:
        """Quantas decisões do de-para e quantas contas do banco apontam para a conta.

        Toda decisão conta, de qualquer vigência: a vigência substituída continua
        decidindo as competências anteriores ainda não materializadas, então ela
        também quebraria. As duas contagens com `client_id` no próprio `WHERE`
        (§3.15), mesmo a FK já sendo do plano deste cliente.
        """
        decisions = select(func.count()).where(
            ClientMappingDecision.client_id == client_id,
            ClientMappingDecision.accounting_account_id == account_id,
        )
        bindings = select(func.count()).where(
            ClientSourceAccountBinding.client_id == client_id,
            ClientSourceAccountBinding.accounting_account_id == account_id,
        )
        decision_count = int((await self._session.execute(decisions)).scalar_one())
        binding_count = int((await self._session.execute(bindings)).scalar_one())
        return decision_count, binding_count

    async def get_many(
        self, client_id: UUID, account_ids: Collection[UUID]
    ) -> list[ClientAccountingAccount]:
        """As contas pelas pks, SÓ as do cliente (as de outro somem do resultado)."""
        if not account_ids:
            return []
        stmt = self._base_query(client_id).where(ClientAccountingAccount.id.in_(account_ids))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_all(self, client_id: UUID) -> list[ClientAccountingAccount]:
        """O plano inteiro do cliente, na ordem da classificação (a reimportação casa
        por código sobre ele)."""
        stmt = self._base_query(client_id).order_by(*_chart_order())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_by_codes(
        self, client_id: UUID, codes: Collection[str]
    ) -> list[ClientAccountingAccount]:
        """As contas pelo código reduzido, SÓ as do cliente (`UNIQUE(client_id, code)`
        garante no máximo uma por código — código de outro cliente não casa nada).

        Usado pela portabilidade do de-para (86e3fxqqe) pra resolver `codigo_alvo`
        da planilha no destino `conta_contabil`; `require_postable_accounts` segue
        sendo o validador único de POSTABILIDADE — esta função só encontra a conta.
        """
        if not codes:
            return []
        stmt = self._base_query(client_id).where(ClientAccountingAccount.code.in_(codes))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_page(
        self,
        client_id: UUID,
        *,
        filters: AccountingChartFilters,
        limit: int,
        offset: int,
    ) -> tuple[list[ClientAccountingAccount], int]:
        """Página + total que casa com os MESMOS filtros, na ordem da classificação.

        A busca é por PREFIXO de CÓDIGO: nome é cifrado (§4.5) e não é buscável no
        servidor. Curingas do `LIKE` são literais.
        """
        stmt = self._base_query(client_id)
        if filters.code_prefix:
            pattern = _escape_like(filters.code_prefix) + "%"
            stmt = stmt.where(ClientAccountingAccount.code.ilike(pattern, escape="\\"))
        if filters.account_type is not None:
            stmt = stmt.where(ClientAccountingAccount.account_type == filters.account_type)
        if filters.active is not None:
            stmt = stmt.where(ClientAccountingAccount.active.is_(filters.active))

        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())
        page_stmt = stmt.order_by(*_chart_order()).limit(limit).offset(offset)
        rows = list((await self._session.execute(page_stmt)).scalars().all())
        return rows, total

    # ------------------------------------------------------------------ escrita

    async def lock_client_chart(self, client_id: UUID) -> None:
        """Serializa as importações do MESMO cliente até o fim da transação.

        Duplo clique ou duas abas importando juntas: sem a trava, as duas leriam o
        plano antigo e tentariam inserir o mesmo código — a UNIQUE recusaria a
        segunda com `IntegrityError` (500). Chave de DOIS inteiros com o nome da
        tabela no primeiro, como o lock das conexões (ADR-083-BE): não cruza com o
        lock de um inteiro da ingestão nem com o `(cliente, destino)` do de-para.
        """
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('client_accounting_accounts'), hashtext(:client))"
            ),
            {"client": str(client_id)},
        )

    async def insert_accounts(self, rows: Sequence[dict[str, Any]]) -> None:
        """INSERT em lote (Core). Cada linha já traz `id` e o nome CIFRADO com ele."""
        if not rows:
            return
        await self._session.execute(insert(_table()), list(rows))

    async def update_accounts(self, updates: Sequence[dict[str, Any]]) -> None:
        """Atualiza nome/tipo/classificação/chave de ordem e REATIVA, por pk, em lote (Core).

        Cada item: `{"b_client", "b_id", "b_ct", "b_iv", "b_type", "b_class",
        "b_sort", "b_author"}`. `client_id` no WHERE junto da pk (§3.15). `updated_at`
        pelo `clock_timestamp()`: o `onupdate` do ORM não vale para UPDATE de Core.
        """
        if not updates:
            return
        table = _table()
        stmt = (
            update(table)
            .where(table.c.client_id == bindparam("b_client"), table.c.id == bindparam("b_id"))
            .values(
                name_encrypted=bindparam("b_ct"),
                name_iv=bindparam("b_iv"),
                account_type=bindparam("b_type"),
                classification=bindparam("b_class"),
                sort_key=bindparam("b_sort"),
                active=True,
                updated_by=bindparam("b_author"),
                updated_at=func.clock_timestamp(),
            )
        )
        await self._session.execute(stmt, list(updates))

    async def update_account(
        self, client_id: UUID, account_id: UUID, values: dict[str, Any]
    ) -> None:
        """Atualiza UMA conta (edição manual, 86e3nb816) — Core, com `client_id` no WHERE.

        `values` traz só as colunas que mudam (e `updated_by`); `updated_at` sai do
        `clock_timestamp()`, como em `update_accounts`.
        """
        table = _table()
        stmt = (
            update(table)
            .where(table.c.client_id == client_id, table.c.id == account_id)
            .values(**values, updated_at=func.clock_timestamp())
        )
        await self._session.execute(stmt)

    async def deactivate_absent(
        self, client_id: UUID, *, present_codes: Collection[str], author_id: UUID
    ) -> int:
        """Inativa as contas ATIVAS que não vieram na planilha. **Nunca apaga.**

        Devolve quantas passaram de ativa a inativa nesta importação (as que já
        estavam inativas não contam de novo). Conta inativa pode ter decisão
        apontando para ela (16.2): apagá-la quebraria a decisão.
        """
        table = _table()
        stmt = (
            update(table)
            .where(
                table.c.client_id == client_id,
                table.c.active.is_(True),
                table.c.code.not_in(list(present_codes)),
            )
            .values(active=False, updated_by=author_id, updated_at=func.clock_timestamp())
            .returning(table.c.id)
        )
        return len((await self._session.execute(stmt)).all())
