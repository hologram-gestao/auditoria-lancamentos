"""Acesso ao DB do módulo de conciliações.

S8 (BACK 6.2): verificação de duplicata via chave de idempotência.
S10 (BACK 8.1/8.4/8.5/8.6): criação atômica de sessão + entries; persistência
de omie_entries pós-matching; atualização de contadores; marcação de erro;
leitura para o endpoint de status.

Decisão de modelagem: não há `lazy="raise"` workaround — o `selectinload` em
`get_session_with_client` carrega o `Client` (necessário para descriptografar
credenciais no worker) e os `file_entries` (necessários para o matcher) em
queries separadas com IN clause; nada de N+1 silencioso.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
    ReconciliationStatus,
)


class ReconciliationRepository:
    """Operações de leitura/escrita sobre o agregado de conciliação."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Idempotência (BACK 6.2)
    # ------------------------------------------------------------------

    async def exists_session_with_idempotency_key(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        reference_month: date,
        file_hash: str,
    ) -> bool:
        """Retorna True se já existe sessão com a tupla idempotente.

        Não carrega a `ReconciliationSession` inteira: seleciona apenas o `id`
        com `LIMIT 1` para que o Postgres responda direto pelo índice da
        UNIQUE — gasto de I/O constante e mínimo.
        """
        stmt = (
            select(ReconciliationSession.id)
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.omie_conta_id == omie_conta_id,
                ReconciliationSession.reference_month == reference_month,
                ReconciliationSession.file_hash == file_hash,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Criação atômica (BACK 8.1)
    # ------------------------------------------------------------------

    async def add_session_with_entries(
        self,
        session_obj: ReconciliationSession,
        entries: list[ReconciliationFileEntry],
    ) -> None:
        """Insere a sessão + suas linhas de arquivo em uma única transação.

        Espera que `session_obj` já tenha `client_id/created_by/...` setados.
        As `entries` precisam ter `session_id` apontando para `session_obj.id`
        OU usar o cascade `delete-orphan` configurado no relationship — aqui
        o caller passa explicitamente para evitar ambiguidade.

        O commit é responsabilidade do caller (route → DbSessionDep faz commit
        ao final do request com sucesso). Isso permite que o caller decida o
        ponto de commit (ex: se for chamar `.refresh()` antes).
        """
        self._session.add(session_obj)
        # Flush antes de adicionar entries garante `session_obj.id` populado
        # e evita FK violation se o caller esquecer de setar `session_id`
        # nas entries explicitamente.
        await self._session.flush()
        for entry in entries:
            entry.session_id = session_obj.id
        if entries:
            self._session.add_all(entries)
            await self._session.flush()

    # ------------------------------------------------------------------
    # Worker — leitura (BACK 8.2 + 8.4)
    # ------------------------------------------------------------------

    async def get_session_with_client(
        self,
        session_id: UUID,
    ) -> ReconciliationSession | None:
        """Carrega a sessão com `client` e `file_entries` eager.

        Necessário para o worker: o `client` traz as credenciais Omie
        criptografadas; os `file_entries` alimentam o matcher. Como todos os
        relationships estão `lazy="raise"`, eager-load é OBRIGATÓRIO.
        """
        stmt = (
            select(ReconciliationSession)
            .where(ReconciliationSession.id == session_id)
            .options(
                selectinload(ReconciliationSession.client),
                selectinload(ReconciliationSession.file_entries),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Worker — escrita pós-matching (BACK 8.4 + 8.5)
    # ------------------------------------------------------------------

    async def add_omie_entries(
        self,
        entries: list[ReconciliationOmieEntry],
    ) -> None:
        """Insere os lançamentos Omie sem correspondente no arquivo."""
        if not entries:
            return
        self._session.add_all(entries)
        await self._session.flush()

    async def apply_matches(
        self,
        matches: list[tuple[UUID, int]],
    ) -> None:
        """Aplica os pares (file_entry_id, omie_lancamento_id) nas linhas.

        Atualiza `situation='conciliado'` e `omie_lancamento_id` para cada
        par. Faz UPDATE individual (não bulk) porque o número de matches é
        bounded por `total_file_entries` (geralmente < 200) — performance
        é dominada por outras etapas, e UPDATE individual é mais legível
        que `update().values()` com CASE/WHEN.
        """
        from app.db.models import FileEntrySituation

        for file_entry_id, omie_lancamento_id in matches:
            await self._session.execute(
                update(ReconciliationFileEntry)
                .where(ReconciliationFileEntry.id == file_entry_id)
                .values(
                    situation=FileEntrySituation.CONCILIADO.value,
                    omie_lancamento_id=omie_lancamento_id,
                )
            )

    async def update_session_after_matching(
        self,
        session_id: UUID,
        *,
        total_file_entries: int,
        conciliated_count: int,
        sem_omie_count: int,
        omie_sem_arquivo_count: int,
        anomaly_count: int,
    ) -> None:
        """Atualiza contadores + status='reviewing' + processed_at=now()."""
        from datetime import UTC, datetime

        await self._session.execute(
            update(ReconciliationSession)
            .where(ReconciliationSession.id == session_id)
            .values(
                status=ReconciliationStatus.REVIEWING.value,
                total_file_entries=total_file_entries,
                conciliated_count=conciliated_count,
                sem_omie_count=sem_omie_count,
                omie_sem_arquivo_count=omie_sem_arquivo_count,
                anomaly_count=anomaly_count,
                processed_at=datetime.now(UTC),
                error_message=None,
            )
        )

    async def mark_session_error(
        self,
        session_id: UUID,
        *,
        user_message: str,
    ) -> None:
        """Marca a sessão como `status='error'` e popula `error_message`.

        Usado pelo worker quando alguma etapa falha (Omie indisponível,
        parsing inconsistente, etc). O `user_message` é em PT-BR — vem do
        `AppError.user_message` da exceção que disparou.

        Esta operação roda em transação SEPARADA do matching: se o matching
        falhou e fez rollback, ainda assim conseguimos marcar o erro porque
        a sessão original (criada pelo endpoint) já está commitada.
        """
        await self._session.execute(
            update(ReconciliationSession)
            .where(ReconciliationSession.id == session_id)
            .values(
                status=ReconciliationStatus.ERROR.value,
                error_message=user_message,
            )
        )

    # ------------------------------------------------------------------
    # Worker — leitura de anomalies (BACK 8.5)
    # ------------------------------------------------------------------

    async def add_anomalies(self, anomalies: list[ReconciliationAnomaly]) -> None:
        """Insere as anomalias estruturais. No-op se a lista estiver vazia."""
        if not anomalies:
            return
        self._session.add_all(anomalies)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Endpoint de status (BACK 8.6)
    # ------------------------------------------------------------------

    async def get_status_view(
        self,
        session_id: UUID,
    ) -> ReconciliationSession | None:
        """Carrega APENAS os campos necessários ao endpoint de status.

        Não eager-loadingo o `client` aqui: o RBAC busca o cliente via
        `require_client_access`, e o restante dos relationships não é
        usado pelo polling.
        """
        stmt = select(ReconciliationSession).where(ReconciliationSession.id == session_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_detail_view(
        self,
        session_id: UUID,
    ) -> ReconciliationSession | None:
        """Carrega a sessão para o endpoint GET /reconciliations/{id}.

        O detail expõe o mesmo escalar carregado por `get_status_view` —
        as colunas necessárias ao header da Tela de Revisão (reference_month,
        omie_conta_id, contadores, total_file_entries) já estão na
        `reconciliation_sessions`. Sem eager-load de relationships porque
        o front busca client/conta via `useClientDetail` separado.
        """
        return await self.get_status_view(session_id)
