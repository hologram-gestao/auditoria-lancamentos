"""Acesso ao DB do glossário por tenant (Sprint 6, BACK 06.2).

SQL puro sobre `client_glossary_entries` + o contador `clients.glossary_version`.
Não conhece criptografia (quem cifra/decifra é o service) nem política de erro.

**Isolamento (S5/R3) em duas camadas, dentro do próprio `SELECT`:**

1. `AND client_id = <alvo>` — sempre. O alvo já foi autorizado na rota por
   `resolve_client_access`; aqui ele é o filtro, não uma sugestão.
2. `scoped_by_tenant(...)` — para usuário `scope='client'`, força **o tenant da
   LINHA dele** por cima. Forjar `client_id` na URL não devolve entrada de outro
   tenant: o `SELECT` simplesmente não carrega a linha (→ 404), mesmo que alguém
   esqueça o guard da rota.

**Ordenação/paginação só por coluna em CLARO** (`kind`, `created_at`, `id`):
o texto é cifrado com IV novo a cada operação, logo não é comparável nem
ordenável. Não existe índice sobre ciphertext — e não se inventa um.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser, scoped_by_tenant
from app.db.models import Client, ClientGlossaryEntry


class ClientGlossaryRepository:
    """Operações de leitura/escrita do glossário de um tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Filtro base — um lugar só monta o `WHERE` do isolamento
    # ------------------------------------------------------------------

    @staticmethod
    def _scoped(
        stmt: Select[Any],
        *,
        client_id: UUID,
        user: CurrentUser | None,
    ) -> Select[Any]:
        """`AND client_id = <alvo> AND deleted_at IS NULL` (+ tenant da linha).

        `user=None` é o caminho do JOB (qualificação): não há request nem
        `CurrentUser`, e o `client_id` já vem da SESSÃO sendo processada — nunca
        de payload. A camada 1 do isolamento continua valendo.
        """
        stmt = stmt.where(
            ClientGlossaryEntry.client_id == client_id,
            ClientGlossaryEntry.deleted_at.is_(None),
        )
        if user is not None:
            stmt = scoped_by_tenant(stmt, ClientGlossaryEntry.client_id, user)
        return stmt

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    async def list_all_active(
        self,
        *,
        client_id: UUID,
        user: CurrentUser | None = None,
    ) -> list[ClientGlossaryEntry]:
        """Todas as entradas ativas do tenant, em ordem DETERMINÍSTICA.

        `(kind, created_at, id)` — o `id` desempata linhas criadas no mesmo
        instante (a fixture de transação única do teste tem `NOW()` constante, e
        em produção um import em lote também empata). Sem desempate estável, o
        bloco de prompt da BACK 06.4 mudaria de ordem entre chamadas e o prefixo
        nunca cachearia.
        """
        stmt = self._scoped(select(ClientGlossaryEntry), client_id=client_id, user=user)
        stmt = stmt.order_by(
            ClientGlossaryEntry.kind,
            ClientGlossaryEntry.created_at,
            ClientGlossaryEntry.id,
        )
        rows = await self._session.execute(stmt)
        entries: list[ClientGlossaryEntry] = list(rows.scalars().all())
        return entries

    async def list_page(
        self,
        *,
        client_id: UUID,
        user: CurrentUser,
        page: int,
        page_size: int,
    ) -> tuple[list[ClientGlossaryEntry], int]:
        """Página + total. O `COUNT` usa os MESMOS filtros da listagem."""
        total_stmt = self._scoped(
            select(func.count(ClientGlossaryEntry.id)), client_id=client_id, user=user
        )
        total = int(await self._session.scalar(total_stmt) or 0)
        stmt = self._scoped(select(ClientGlossaryEntry), client_id=client_id, user=user)
        stmt = (
            stmt.order_by(
                ClientGlossaryEntry.kind,
                ClientGlossaryEntry.created_at,
                ClientGlossaryEntry.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = await self._session.execute(stmt)
        page_entries: list[ClientGlossaryEntry] = list(rows.scalars().all())
        return page_entries, total

    async def get_active(
        self,
        *,
        entry_id: UUID,
        client_id: UUID,
        user: CurrentUser,
    ) -> ClientGlossaryEntry | None:
        """Detalhe por PK **com o tenant no próprio SELECT**.

        Entrada de outro tenant devolve `None` (→ 404 na rota), nunca o dado.
        """
        stmt = self._scoped(select(ClientGlossaryEntry), client_id=client_id, user=user)
        entry: ClientGlossaryEntry | None = await self._session.scalar(
            stmt.where(ClientGlossaryEntry.id == entry_id)
        )
        return entry

    async def count_active(self, *, client_id: UUID) -> int:
        """Entradas ativas do tenant — teto de quantidade e `n_categorias`."""
        stmt = self._scoped(
            select(func.count(ClientGlossaryEntry.id)), client_id=client_id, user=None
        )
        return int(await self._session.scalar(stmt) or 0)

    async def get_version(self, *, client_id: UUID) -> int:
        """Versão corrente do glossário do tenant (0 = nunca escrito)."""
        version = await self._session.scalar(
            select(Client.glossary_version).where(Client.id == client_id)
        )
        return int(version or 0)

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    async def add(self, entry: ClientGlossaryEntry) -> None:
        """Insere a entrada (já cifrada pelo service) e a torna visível na transação."""
        self._session.add(entry)
        await self._session.flush()

    async def soft_delete(self, entry: ClientGlossaryEntry) -> None:
        """Remoção lógica — DELETE físico é proibido (padrão do repo)."""
        entry.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def bump_version(self, *, client_id: UUID) -> int:
        """Incrementa `clients.glossary_version` e devolve o valor novo.

        **Fonte ÚNICA da versão.** Nenhum outro lugar calcula ou escreve este
        número — recalcular em paralelo é o learning "valor derivado em 2
        lugares diverge", e aqui divergir significa cache de prompt servindo
        glossário velho.

        `glossary_version + 1` no PRÓPRIO UPDATE (não `read → +1 → write`):
        duas edições concorrentes do mesmo tenant não perdem incremento.
        Roda na MESMA transação da escrita que a motivou — o caller commita as
        duas juntas ou nenhuma.
        """
        new_version = await self._session.scalar(
            update(Client)
            .where(Client.id == client_id)
            .values(glossary_version=Client.glossary_version + 1)
            .returning(Client.glossary_version)
        )
        return int(new_version or 0)
