"""Camada de dados do plano de contas do cliente (Sprint 10, BACK 10.1).

SQL puro, uma função por query. **Toda query filtra por `client_id`** — nenhuma
leitura alcança uma linha só pela PK: ela pertence a um tenant, e o tenant vem
do `Client` já validado pela rota (§3.15).

O serviço de sincronização (10.2) e as rotas (10.3) consomem estas funções; nada
aqui decide regra de negócio nem fala com a origem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import CursorResult, Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.client import Client
from app.db.models.client_chart_of_accounts import (
    UQ_CHART_OF_ACCOUNTS_CLIENT_CODE,
    ChartOfAccountsStatus,
    ClientChartOfAccount,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: Linhas por comando no upsert. 13 placeholders por linha contra o teto de
#: 65.535 do protocolo do Postgres dá ~5.000; 1.000 é folga de 5x sobre um
#: plano de contas que já seria absurdo para uma empresa só.
UPSERT_CHUNK_SIZE = 1000


@dataclass(frozen=True, slots=True)
class ChartOfAccountsCoverage:
    """As cinco contagens da cobertura (R3), calculadas no BANCO.

    O resultado de UMA query de agregação sobre o conjunto INTEIRO. Contar em
    Python sobre a página devolveria números que mudam conforme a paginação —
    a pergunta "quanto do de-para já vem pronto" é sobre o cliente todo, não
    sobre 20 linhas.
    """

    total: int
    ativas: int
    com_destino: int
    sem_destino: int
    com_conta_contabil: int


class ClientChartOfAccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    def _base_query(self, client_id: UUID) -> Select[tuple[ClientChartOfAccount]]:
        """Todo `SELECT` desta tabela nasce daqui, já preso ao tenant.

        Um único lugar coloca o `WHERE client_id = …`: é a diferença entre
        esquecer o filtro numa query nova e não ter como esquecê-lo.
        """
        return select(ClientChartOfAccount).where(ClientChartOfAccount.client_id == client_id)

    async def list_for_client(
        self,
        client_id: UUID,
        *,
        status: str | None = None,
        parent_code: str | None = None,
        code_contains: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[ClientChartOfAccount], int]:
        """Página do plano de contas + total que casa com os MESMOS filtros.

        A busca é **por CÓDIGO**, nunca por nome: nome não está nesta tabela
        (§4.5) e um `ILIKE` sobre a descrição exigiria persisti-lo. Os curingas
        do `LIKE` são escapados — termo com `%` procura o caractere, não
        "qualquer coisa".
        """
        stmt = self._base_query(client_id)
        if status is not None:
            stmt = stmt.where(ClientChartOfAccount.status == status)
        if parent_code is not None:
            stmt = stmt.where(ClientChartOfAccount.parent_code == parent_code)
        if code_contains:
            pattern = "%" + _escape_like(code_contains) + "%"
            stmt = stmt.where(ClientChartOfAccount.category_code.ilike(pattern, escape="\\"))

        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self._session.execute(total_stmt)).scalar_one()

        page_stmt = stmt.order_by(ClientChartOfAccount.category_code).limit(limit).offset(offset)
        rows = list((await self._session.execute(page_stmt)).scalars().all())
        return rows, total

    async def coverage(self, client_id: UUID) -> ChartOfAccountsCoverage:
        """As cinco contagens sobre o conjunto inteiro do cliente, numa query.

        `ativas`, `com destino` e `sem destino` saem da **mesma** base ativa de
        propósito: `com_destino + sem_destino == ativas` é a identidade que a
        tela promete, e contar "com destino" sobre o total (incluindo inativas
        e ausentes na origem) faria as parcelas não fecharem com a soma exibida
        ao lado.
        """
        ativa = ClientChartOfAccount.status == ChartOfAccountsStatus.ATIVA.value
        com_destino = ClientChartOfAccount.dre_code.is_not(None)
        com_contabil = ClientChartOfAccount.conta_contabil_code.is_not(None)

        stmt = select(
            func.count().label("total"),
            func.count().filter(ativa).label("ativas"),
            func.count().filter(ativa, com_destino).label("com_destino"),
            func.count().filter(ativa, ~com_destino).label("sem_destino"),
            func.count().filter(ativa, com_contabil).label("com_conta_contabil"),
        ).where(ClientChartOfAccount.client_id == client_id)

        row = (await self._session.execute(stmt)).one()
        return ChartOfAccountsCoverage(
            total=row.total,
            ativas=row.ativas,
            com_destino=row.com_destino,
            sem_destino=row.sem_destino,
            com_conta_contabil=row.com_conta_contabil,
        )

    async def existing_codes(self, client_id: UUID) -> set[str]:
        """Os códigos que o cliente já tem gravados.

        É o que permite descobrir quem SUMIU da origem sem trazer a tabela
        inteira como objetos ORM.
        """
        stmt = select(ClientChartOfAccount.category_code).where(
            ClientChartOfAccount.client_id == client_id
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def get_sync_state(self, client_id: UUID) -> tuple[datetime | None, datetime | None]:
        """`(última bem-sucedida, última falha)` — as duas colunas de `clients`.

        Fonte ÚNICA da validade de 24h (10.2) e do aviso da tela (10.3). Não
        derivar de `MAX(synced_at)` das linhas: cliente sem categoria nenhuma
        deixaria o MAX em NULL e o TTL nunca dispararia.
        """
        stmt = select(
            Client.chart_of_accounts_synced_at,
            Client.chart_of_accounts_sync_failed_at,
        ).where(Client.id == client_id)
        row = (await self._session.execute(stmt)).one()
        return row.chart_of_accounts_synced_at, row.chart_of_accounts_sync_failed_at

    # ------------------------------ WRITE -----------------------------

    async def upsert_many(
        self,
        client_id: UUID,
        rows: Sequence[dict[str, Any]],
        *,
        synced_at: datetime,
    ) -> int:
        """Grava o resultado de uma sincronização. Devolve quantas linhas entraram.

        `ON CONFLICT (client_id, category_code) DO UPDATE` — a idempotência é
        da UNIQUE do banco, não de uma leitura anterior: duas sincronizações
        simultâneas do mesmo cliente convergem em vez de estourar.

        Toda linha revista volta para a situação que a origem declarou AGORA:
        categoria que estava `ausente_na_origem` e reapareceu volta a valer, o
        inverso exato do que `mark_absent_from_origin` faz com quem sumiu.

        O lote é quebrado em `UPSERT_CHUNK_SIZE`: cada linha ocupa 13
        placeholders e o protocolo do Postgres não passa de 65.535 por comando.
        Os pedaços rodam na MESMA transação — quebrar o comando não quebra a
        atomicidade.
        """
        if not rows:
            return 0

        payload = [dict(row, client_id=client_id, synced_at=synced_at) for row in rows]
        for start in range(0, len(payload), UPSERT_CHUNK_SIZE):
            chunk = payload[start : start + UPSERT_CHUNK_SIZE]
            stmt = pg_insert(ClientChartOfAccount).values(chunk)
            stmt = stmt.on_conflict_do_update(
                constraint=UQ_CHART_OF_ACCOUNTS_CLIENT_CODE,
                set_={
                    "parent_code": stmt.excluded.parent_code,
                    "dre_code": stmt.excluded.dre_code,
                    "dre_level": stmt.excluded.dre_level,
                    "dre_sign": stmt.excluded.dre_sign,
                    "conta_contabil_code": stmt.excluded.conta_contabil_code,
                    "totalizadora": stmt.excluded.totalizadora,
                    "transferencia": stmt.excluded.transferencia,
                    "nao_exibir": stmt.excluded.nao_exibir,
                    "status": stmt.excluded.status,
                    "synced_at": stmt.excluded.synced_at,
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(stmt)
        return len(payload)

    async def mark_absent_from_origin(self, client_id: UUID, *, keep_codes: Sequence[str]) -> int:
        """Marca como `ausente_na_origem` quem não veio nesta sincronização.

        **Nunca apaga** — pode existir de-para (Sprint 12) apontando para a
        linha, e apagá-la transformaria um vínculo humano em órfão silencioso.
        `synced_at` NÃO é tocado: ele diz quando a linha foi vista pela origem
        pela última vez, e ela não foi.
        """
        stmt = (
            update(ClientChartOfAccount)
            .where(
                ClientChartOfAccount.client_id == client_id,
                ClientChartOfAccount.status != ChartOfAccountsStatus.AUSENTE_NA_ORIGEM.value,
            )
            .values(status=ChartOfAccountsStatus.AUSENTE_NA_ORIGEM.value, updated_at=func.now())
        )
        if keep_codes:
            stmt = stmt.where(ClientChartOfAccount.category_code.not_in(list(keep_codes)))
        result = await self._session.execute(stmt)
        # UPDATE devolve CursorResult (com rowcount); o narrow e para o mypy --
        # o caminho else nao existe em runtime.
        if not isinstance(result, CursorResult):  # pragma: no cover
            return 0
        return int(result.rowcount or 0)

    async def mark_sync_succeeded(self, client_id: UUID, *, at: datetime) -> None:
        """Carimba a sincronização boa e **limpa** a marca de falha.

        Limpar é parte do contrato da tela (R3): "falhou" é sobre a ÚLTIMA
        tentativa. Um sucesso depois de uma falha deixaria o aviso pendurado
        para sempre se a coluna só acumulasse.
        """
        await self._session.execute(
            update(Client)
            .where(Client.id == client_id)
            .values(chart_of_accounts_synced_at=at, chart_of_accounts_sync_failed_at=None)
        )

    async def mark_sync_failed(self, client_id: UUID, *, at: datetime) -> None:
        """Carimba a falha. **Nunca** toca a coluna do último sucesso.

        É o que sustenta "falha preserva a última sincronização bem-sucedida"
        (R2) no schema, e não só no fluxo do serviço.
        """
        await self._session.execute(
            update(Client).where(Client.id == client_id).values(chart_of_accounts_sync_failed_at=at)
        )

    async def delete_for_client(self, client_id: UUID) -> None:
        """Purga o plano de contas do cliente (encerramento, §4.12).

        Nada aqui é cifrado — não há PII —, então nada morre junto com a DEK.
        Some mesmo assim porque é **configuração** do cliente final, e cliente
        encerrado não tem plano de contas a operar.
        """
        await self._session.execute(
            delete(ClientChartOfAccount).where(ClientChartOfAccount.client_id == client_id)
        )


def _escape_like(term: str) -> str:
    """Neutraliza os curingas do `LIKE` no termo digitado pelo usuário.

    Sem isso, buscar `%` listaria o plano de contas inteiro e `_` casaria
    qualquer caractere — o usuário digitou um código, não um padrão.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
