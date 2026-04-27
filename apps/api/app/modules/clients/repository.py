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
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Client,
    ClientAssignment,
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
