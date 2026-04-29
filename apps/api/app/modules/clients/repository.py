"""Acesso ao DB do módulo de clientes BPO.

Responsabilidades (CLAUDE.md §6):
    - **Apenas SQL/ORM** — regras de negócio, criptografia e RBAC ficam no
      service / dependencies.
    - Listagem com filtro RBAC já aplicado pelo caller (passa `manager_id` ou
      `None` para admin).
    - Conta de conciliações via subquery escalar correlacionada — uma query
      por listagem total, sem N+1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Client,
    ClientAssignment,
    OmieAccountCache,
    ReconciliationSession,
    User,
)


class ClientRow(NamedTuple):
    """Linha agregada da listagem: cliente + manager responsável + contagem.

    `manager` é `None` quando o cliente está órfão (não deveria acontecer em
    produção pela auto-criação do assignment, mas a listagem precisa ser
    resiliente — não derrubamos a tela por dado inconsistente).
    """

    client: Client
    manager: User | None
    reconciliation_count: int


class ClientRepository:
    """Operações de leitura/escrita sobre `clients` e `client_assignments`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        manager_id: UUID | None = None,
    ) -> tuple[Sequence[ClientRow], int]:
        """Lista paginada de clientes com manager + count de conciliações.

        Args:
            page/page_size: paginação 1-based.
            search: ILIKE em `clients.name` (case-insensitive).
            manager_id: se não-None, filtra por `client_assignments.user_id`
                (RBAC do manager). Para admin, passar `None`.

        Returns:
            Tupla `(rows, total_count)`. Total é a contagem ANTES da paginação.
        """
        manager = aliased(User)

        # Subquery escalar: conta de sessões por cliente. Correlate evita o
        # SQLAlchemy referenciar `clients` da query externa duas vezes.
        recon_count_sq = (
            select(func.count(ReconciliationSession.id))
            .where(ReconciliationSession.client_id == Client.id)
            .correlate(Client)
            .scalar_subquery()
        )

        base = (
            select(Client, manager, recon_count_sq.label("recon_count"))
            .outerjoin(ClientAssignment, ClientAssignment.client_id == Client.id)
            .outerjoin(manager, manager.id == ClientAssignment.user_id)
        )
        count_base = select(func.count(Client.id.distinct())).select_from(Client)

        if manager_id is not None:
            # Para manager: filtra clientes da carteira via assignment direto.
            # `outerjoin` acima já está montado, mas o WHERE força inner-equivalent.
            base = base.where(ClientAssignment.user_id == manager_id)
            count_base = count_base.join(
                ClientAssignment, ClientAssignment.client_id == Client.id
            ).where(ClientAssignment.user_id == manager_id)

        if search:
            term = f"%{search.strip().lower()}%"
            base = base.where(func.lower(Client.name).like(term))
            count_base = count_base.where(func.lower(Client.name).like(term))

        # Ordem estável: created_at desc, id desc (desempate determinístico)
        base = base.order_by(Client.created_at.desc(), Client.id.desc())
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        total = (await self._session.execute(count_base)).scalar_one()
        result = await self._session.execute(base)
        rows = [
            ClientRow(client=row[0], manager=row[1], reconciliation_count=int(row[2] or 0))
            for row in result.all()
        ]
        return rows, int(total)

    async def get_detail(self, client_id: UUID) -> ClientRow | None:
        """Carrega 1 cliente com manager + count — usado em endpoints de retorno."""
        manager = aliased(User)
        recon_count_sq = (
            select(func.count(ReconciliationSession.id))
            .where(ReconciliationSession.client_id == Client.id)
            .correlate(Client)
            .scalar_subquery()
        )
        stmt = (
            select(Client, manager, recon_count_sq.label("recon_count"))
            .outerjoin(ClientAssignment, ClientAssignment.client_id == Client.id)
            .outerjoin(manager, manager.id == ClientAssignment.user_id)
            .where(Client.id == client_id)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return ClientRow(client=row[0], manager=row[1], reconciliation_count=int(row[2] or 0))

    async def get_by_id(self, client_id: UUID) -> Client | None:
        """Retorna o `Client` cru, sem joins — usado para writes (PATCH, assign)."""
        result = await self._session.execute(select(Client).where(Client.id == client_id))
        return result.scalar_one_or_none()

    async def get_assignment(self, client_id: UUID) -> ClientAssignment | None:
        """Retorna o assignment único do cliente (UNIQUE em `client_id`)."""
        result = await self._session.execute(
            select(ClientAssignment).where(ClientAssignment.client_id == client_id)
        )
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        """Lookup rápido de user — usado para validar manager-alvo do assign."""
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    # ------------------------------ WRITE -----------------------------

    async def add_client(self, client: Client) -> None:
        """Insere/atualiza Client com flush + refresh.

        Refresh é necessário para carregar `created_at`/`updated_at` populados
        server-side (evita `MissingGreenlet` na serialização Pydantic).
        """
        self._session.add(client)
        await self._session.flush()
        await self._session.refresh(client)

    async def add_assignment(self, assignment: ClientAssignment) -> None:
        """Persiste um ClientAssignment com refresh do `assigned_at`."""
        self._session.add(assignment)
        await self._session.flush()
        await self._session.refresh(assignment)

    # ------------------------- S7: cache L1 ---------------------------

    async def get_accounts_cache(self, client_id: UUID) -> Sequence[OmieAccountCache]:
        """Retorna todas as linhas do cache L1 do cliente, ordenadas por nome.

        Ordem por `name ASC` mantém a UI do detalhe estável entre requests
        (tela mostra cards das contas — sem ordem fixa, embaralha a cada hit
        no banco). `client_id` é indexado, então o ORDER BY não dói.
        """
        stmt = (
            select(OmieAccountCache)
            .where(OmieAccountCache.client_id == client_id)
            .order_by(OmieAccountCache.name.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def replace_accounts_cache(
        self,
        client: Client,
        items: Sequence[OmieAccountCache],
    ) -> datetime:
        """Substitui o cache L1 do cliente por `items` em uma única transação.

        Estratégia escolhida (Doc §5.2):
            DELETE de todas as linhas do cliente + INSERT em massa, dentro da
            mesma transação do request. Vantagens vs. UPSERT por linha:
                - clean-slate: contas removidas no Omie somem do nosso cache.
                - idempotência trivial: rodar 2x deixa o estado idêntico.
                - sem dependência de `INSERT ... ON CONFLICT` (Postgres-only,
                  difícil de testar com SQLite).
            UNIQUE(client_id, omie_conta_id) protege contra race entre 2 syncs
            concorrentes — o segundo a chegar levanta IntegrityError, que o
            handler global converte em 409, e a UI tenta de novo.

        O `synced_at` final é gravado em `clients.omie_accounts_synced_at`
        (NÃO derivar de MAX(omie_accounts_cache.synced_at) — quando o Omie
        devolve lista vazia, o MAX volta None e o TTL não dispara, fazendo
        toda request bater o Omie. Bug descoberto em 29/04/2026 com Quial).

        Retorna o `synced_at` aplicado (mesmo timestamp para todas as linhas
        e para a coluna do `Client`).
        """
        await self._session.execute(
            delete(OmieAccountCache).where(OmieAccountCache.client_id == client.id)
        )
        synced_at = datetime.now(UTC)
        for item in items:
            item.synced_at = synced_at
            self._session.add(item)
        client.omie_accounts_synced_at = synced_at
        # `add` em objeto já tracked é no-op, mas garante a presença na identity
        # map caso o caller tenha passado um Client detached por algum motivo.
        self._session.add(client)
        await self._session.flush()
        return synced_at

    # ------------------------- S7: histórico de conciliações ----------

    async def list_reconciliations_paginated(
        self,
        client_id: UUID,
        *,
        page: int,
        page_size: int,
        omie_conta_id: int | None = None,
        month_start: date | None = None,
        month_end: date | None = None,
    ) -> tuple[Sequence[ReconciliationSession], int]:
        """Lista paginada do histórico de conciliações de UM cliente.

        Filtros opcionais (combináveis):
            - `omie_conta_id`: igual.
            - `month_start`/`month_end`: range half-open `[start, end)` para
              filtrar `reference_month` em um mês específico. Caller calcula
              `[YYYY-MM-01, mês+1-01)` para evitar erro de timezone/granularidade.

        Ordem: `created_at DESC, id DESC` — desempate determinístico quando 2
        sessões caem no mesmo segundo (pode acontecer em testes).
        """
        base = select(ReconciliationSession).where(ReconciliationSession.client_id == client_id)
        count_base = (
            select(func.count(ReconciliationSession.id))
            .select_from(ReconciliationSession)
            .where(ReconciliationSession.client_id == client_id)
        )

        if omie_conta_id is not None:
            base = base.where(ReconciliationSession.omie_conta_id == omie_conta_id)
            count_base = count_base.where(ReconciliationSession.omie_conta_id == omie_conta_id)

        if month_start is not None and month_end is not None:
            base = base.where(
                ReconciliationSession.reference_month >= month_start,
                ReconciliationSession.reference_month < month_end,
            )
            count_base = count_base.where(
                ReconciliationSession.reference_month >= month_start,
                ReconciliationSession.reference_month < month_end,
            )

        base = base.order_by(
            ReconciliationSession.created_at.desc(),
            ReconciliationSession.id.desc(),
        )
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        total = (await self._session.execute(count_base)).scalar_one()
        result = await self._session.execute(base)
        rows = result.scalars().all()
        return rows, int(total)
