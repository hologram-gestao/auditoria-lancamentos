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

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select, update
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
        """Retorna True se já existe sessão ATIVA com a tupla idempotente.

        Ignora sessões descartadas (`deleted_at IS NOT NULL`) — depois de
        descartar uma sessão, o usuário pode criar uma nova com o mesmo
        arquivo no mesmo mês. O índice UNIQUE no banco também é parcial,
        então a consistência fica garantida em ambas as camadas.

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
                ReconciliationSession.deleted_at.is_(None),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def find_active_session_by_hash(
        self,
        *,
        client_id: UUID,
        file_hash: str,
    ) -> tuple[UUID, datetime, str] | None:
        """Sessão ATIVA (não `error`, não descartada) com esse (client_id, hash).

        BACK 02.6 — dedup do `POST /parse` ANTES de qualquer chamada à IA.
        No parse só existem `client_id` + hash do conteúdo (recalculado no
        servidor); `omie_conta_id`/`reference_month` só chegam no
        `POST /reconciliations`. Por isso a checagem é por (client_id, hash) —
        mais ampla que a UNIQUE completa, mas o mesmo conteúdo para o mesmo
        cliente é praticamente sempre reenvio.

        Sessões em `error` NÃO contam (reimportar é permitido — não se pune o
        usuário pelo erro do sistema). Descartadas (`deleted_at`) idem. Retorna
        a mais recente `(id, created_at, status)` ou `None`.
        """
        stmt = (
            select(
                ReconciliationSession.id,
                ReconciliationSession.created_at,
                ReconciliationSession.status,
            )
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.file_hash == file_hash,
                ReconciliationSession.deleted_at.is_(None),
                ReconciliationSession.status != ReconciliationStatus.ERROR.value,
            )
            .order_by(ReconciliationSession.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return row.id, row.created_at, row.status

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
        """Carrega a sessão ATIVA com `client` e `file_entries` eager.

        Sessões descartadas (`deleted_at IS NOT NULL`) são tratadas como
        404 — se o worker pegar um job em fila apontando pra uma sessão
        que foi descartada nesse meio-tempo, simplesmente termina silencioso
        (vide `_execute_processing`, que já loga "session_not_found" nesse
        caso).

        Necessário para o worker: o `client` traz as credenciais Omie
        criptografadas; os `file_entries` alimentam o matcher. Como todos os
        relationships estão `lazy="raise"`, eager-load é OBRIGATÓRIO.
        """
        stmt = (
            select(ReconciliationSession)
            .where(
                ReconciliationSession.id == session_id,
                ReconciliationSession.deleted_at.is_(None),
            )
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
        matches: list[tuple[UUID, int, int]],
    ) -> None:
        """Aplica as triplas (file_entry_id, omie_lancamento_id, days_diff).

        Atualiza `situation='conciliado'`, `omie_lancamento_id` e `days_diff`
        (assinado; 0 = data exata — BACK 02.4) para cada linha conciliada. Faz
        UPDATE individual (não bulk) porque o número de matches é bounded por
        `total_file_entries` (geralmente < 200) — performance é dominada por
        outras etapas, e UPDATE individual é mais legível que `update().values()`
        com CASE/WHEN.
        """
        from app.db.models import FileEntrySituation

        for file_entry_id, omie_lancamento_id, days_diff in matches:
            await self._session.execute(
                update(ReconciliationFileEntry)
                .where(ReconciliationFileEntry.id == file_entry_id)
                .values(
                    situation=FileEntrySituation.CONCILIADO.value,
                    omie_lancamento_id=omie_lancamento_id,
                    days_diff=days_diff,
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
        balance_start: Decimal | None = None,
        balance_end_file: Decimal | None = None,
        balance_end_omie: Decimal | None = None,
        balance_difference: Decimal | None = None,
    ) -> None:
        """Atualiza contadores + saldos + status='reviewing' + processed_at=now()."""
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
                balance_start=balance_start,
                balance_end_file=balance_end_file,
                balance_end_omie=balance_end_omie,
                balance_difference=balance_difference,
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

    async def reset_session_for_reprocess(self, session_id: UUID) -> None:
        """Reset a sessão para `status='processing'` pra ser re-enfileirada.

        Caso de uso: sessão entrou em `error` (ex.: Omie devolveu 5xx),
        problema foi corrigido (credencial atualizada, Omie voltou) e o
        usuário clicou "Tentar novamente" na UI.

        No fluxo atual, sessões em `error` nunca tiveram matching bem-
        sucedido (o worker só marca `status='reviewing'` dentro da
        transação atômica de gravação dos matches; qualquer falha antes
        cai em `mark_session_error` antes do `apply_matches`). Logo
        `file_entries` estão como criadas (situation=`sem_omie`,
        `omie_lancamento_id=NULL`, sem `user_action`), e
        `omie_entries`/`anomalies` estão vazios.

        Mesmo assim deletamos `omie_entries`/`anomalies` da sessão pra
        ser defensivo contra mudanças futuras no worker que persistam
        algo antes do erro — custo é nulo (tabelas vazias).

        Não mexemos em `user_action`/`user_note` de `file_entries`:
        invariante preservada (sessão em erro nunca permitiu revisão),
        e se algum dia esse invariante quebrar, preservar trabalho do
        analista é o comportamento certo.
        """
        from app.db.models import ReconciliationFileEntry

        # 1. Limpa dados parciais (defensivo). Cascade da FK também faria,
        #    mas explícito aqui mostra a intenção e protege contra ordem
        #    de DELETEs.
        await self._session.execute(
            delete(ReconciliationAnomaly).where(ReconciliationAnomaly.session_id == session_id)
        )
        await self._session.execute(
            delete(ReconciliationOmieEntry).where(ReconciliationOmieEntry.session_id == session_id)
        )
        # 2. `file_entries`: volta ao estado pós-parse. Se ficaram com
        #    `omie_lancamento_id` por algum motivo, limpa.
        await self._session.execute(
            update(ReconciliationFileEntry)
            .where(ReconciliationFileEntry.session_id == session_id)
            .values(
                situation="sem_omie",
                omie_lancamento_id=None,
                # BACK 02.4 — zera a divergência de data; o re-matching regrava
                # para as linhas que voltarem a conciliar.
                days_diff=None,
            )
        )
        # 3. Reset da sessão.
        await self._session.execute(
            update(ReconciliationSession)
            .where(ReconciliationSession.id == session_id)
            .values(
                status=ReconciliationStatus.PROCESSING.value,
                error_message=None,
                processed_at=None,
                conciliated_count=0,
                sem_omie_count=0,
                omie_sem_arquivo_count=0,
                anomaly_count=0,
                # BACK 02.3 — `balance_start`/`balance_end_file` vêm do PARSE
                # (gravados na criação) e são preservados no reprocess, igual às
                # `file_entries`: reprocessar não re-extrai o arquivo. Só os
                # saldos DERIVADOS do matching (end_omie/difference) são zerados.
                balance_end_omie=None,
                balance_difference=None,
            )
        )

    async def soft_delete_session(self, session_id: UUID) -> None:
        """Marca a sessão como descartada (`deleted_at = now()`).

        Caso de uso: usuário clica em "Descartar" no card de sessão em
        erro. Operação é **idempotente** — chamar 2x não tem efeito.
        Não toca em `file_entries`/`omie_entries`/`anomalies`: o histórico
        fica preservado pra auditoria; o filtro `deleted_at IS NULL` em
        todas as queries de leitura/listagem esconde a sessão da UI.

        Libera a tupla idempotente (client_id, omie_conta_id,
        reference_month, file_hash) pra criar uma sessão nova com o
        mesmo arquivo — o índice UNIQUE no banco é parcial com
        `WHERE deleted_at IS NULL` (ver migration `d1e8a4b9f2c5`).
        """
        from datetime import UTC, datetime

        await self._session.execute(
            update(ReconciliationSession)
            .where(
                ReconciliationSession.id == session_id,
                ReconciliationSession.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
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

        Filtra sessões descartadas — front trata como 404 mesma coisa que
        sessão inexistente.

        Não eager-loadingo o `client` aqui: o RBAC busca o cliente via
        `require_client_access`, e o restante dos relationships não é
        usado pelo polling.
        """
        stmt = select(ReconciliationSession).where(
            ReconciliationSession.id == session_id,
            ReconciliationSession.deleted_at.is_(None),
        )
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
