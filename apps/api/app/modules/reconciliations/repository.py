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
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Client,
    OmieAccountCache,
    ReconciliationAnomaly,
    ReconciliationFile,
    ReconciliationFileEntry,
    ReconciliationFileStatus,
    ReconciliationOmieEntry,
    ReconciliationSession,
    ReconciliationStatus,
)

if TYPE_CHECKING:
    from app.core.crypto import ClientCipher

from app.modules.reconciliations.totals import (
    SessionAmountTotals,
    SessionAnomalyBreakdown,
    SessionCounters,
    compute_anomaly_breakdown,
    compute_card_charges_total,
    compute_session_amounts,
    compute_session_counters,
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
        """True se a conciliação ATIVA de (cliente, conta, mês) já tem esta parte.

        **Sprint 4 (BACK 04.2):** o hash desceu de nível. Antes a pergunta era
        "existe sessão com esta tupla de 4 colunas?"; agora é "a conciliação
        desta conta+mês já contém um arquivo com este conteúdo?" — um JOIN com
        `reconciliation_files`. O endpoint `/check-duplicate` mantém a mesma
        assinatura; o que mudou é o que "duplicata" significa.

        Ignora sessões descartadas (`deleted_at IS NOT NULL`) — depois de
        descartar, o usuário pode recriar com o mesmo arquivo no mesmo mês. O
        índice UNIQUE no banco também é parcial, então a consistência fica
        garantida em ambas as camadas.
        """
        stmt = (
            select(ReconciliationFile.id)
            .join(
                ReconciliationSession,
                ReconciliationSession.id == ReconciliationFile.session_id,
            )
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.omie_conta_id == omie_conta_id,
                ReconciliationSession.reference_month == reference_month,
                ReconciliationSession.deleted_at.is_(None),
                ReconciliationFile.file_hash == file_hash,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def find_active_session_for_account_month(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        reference_month: date,
    ) -> UUID | None:
        """Conciliação ATIVA de (cliente, conta, mês), ou None — BACK 04.2.

        Uma conciliação por conta+mês é a nova regra. Quem já tem uma e quer
        acrescentar uma parte deve ANEXAR (`POST /{id}/files`), não recriar;
        este método é o que permite responder isso com uma mensagem acionável
        em vez de um 409 de violação de índice.
        """
        session_id: UUID | None = await self._session.scalar(
            select(ReconciliationSession.id)
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.omie_conta_id == omie_conta_id,
                ReconciliationSession.reference_month == reference_month,
                ReconciliationSession.deleted_at.is_(None),
            )
            .limit(1)
        )
        return session_id

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

        Sprint 4: procura o hash em `reconciliation_files` (por ARQUIVO), não
        mais na coluna legada da sessão — senão o dedup do `/parse` pararia de
        enxergar as partes 2..N de qualquer conciliação multi-arquivo.
        """
        stmt = (
            select(
                ReconciliationSession.id,
                ReconciliationSession.created_at,
                ReconciliationSession.status,
            )
            .join(
                ReconciliationFile,
                ReconciliationFile.session_id == ReconciliationSession.id,
            )
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationFile.file_hash == file_hash,
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
    # Tipo da conta selecionada (BACK 1.3 — FASE 1)
    # ------------------------------------------------------------------

    async def get_cached_account_type(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
    ) -> str | None:
        """Retorna o `tipo` Omie cru (CC/CR/CA/…) da conta no cache L1, ou None.

        Usado por `create_session_with_entries` para derivar o `account_type`
        da sessão (`CR` → `credit_card`). `None` = conta não cacheada (cache
        vazio/expirado ou `omie_conta_id` inexistente para o cliente) — nesse
        caso o service cai no default `'checking'` (não-bloqueante).
        """
        account = await self._session.scalar(
            select(OmieAccountCache).where(
                OmieAccountCache.client_id == client_id,
                OmieAccountCache.omie_conta_id == omie_conta_id,
            )
        )
        return account.account_type if account is not None else None

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
    # Partes da conciliação (BACK 04.2)
    # ------------------------------------------------------------------

    async def add_files_with_entries(
        self,
        session_id: UUID,
        parts: list[tuple[ReconciliationFile, list[ReconciliationFileEntry]]],
    ) -> None:
        """Insere N partes e as linhas de cada uma, já vinculadas (`file_id`).

        O vínculo linha→parte é o que torna a remoção de uma parte cirúrgica: o
        `ON DELETE CASCADE` leva só as linhas dela. Um `flush` por parte garante
        o `file.id` populado ANTES de setar as entries (sem depender de ordem
        de INSERT do ORM).

        Commit é do caller (padrão do módulo).
        """
        for file_obj, entries in parts:
            file_obj.session_id = session_id
            self._session.add(file_obj)
            await self._session.flush()
            for entry in entries:
                entry.session_id = session_id
                entry.file_id = file_obj.id
            if entries:
                self._session.add_all(entries)
                await self._session.flush()

    async def list_files(self, session_id: UUID) -> list[tuple[ReconciliationFile, int]]:
        """Partes da sessão + quantas linhas cada uma trouxe, mais antiga primeiro.

        Uma query só (LEFT JOIN + GROUP BY) — a contagem por parte num loop
        seria N+1 numa tela que já é chamada a cada abertura de conciliação.
        """
        stmt = (
            select(ReconciliationFile, func.count(ReconciliationFileEntry.id))
            .outerjoin(
                ReconciliationFileEntry,
                ReconciliationFileEntry.file_id == ReconciliationFile.id,
            )
            .where(ReconciliationFile.session_id == session_id)
            .group_by(ReconciliationFile.id)
            .order_by(ReconciliationFile.created_at, ReconciliationFile.id)
        )
        rows = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in rows.all()]

    async def compute_counters(self, session_id: UUID) -> SessionCounters:
        """Totalizadores DERIVADOS das linhas — fonte única (BACK 04.3).

        Fina de propósito: a regra mora em `totals.py`, compartilhada com a
        revisão e com a materialização que a lista lê. O repository só expõe o
        acesso para o service não importar o módulo de regra direto.
        """
        return await compute_session_counters(self._session, session_id)

    async def compute_amounts(self, session_id: UUID) -> SessionAmountTotals:
        """Somas de crédito/débito da sessão INTEIRA (86e2u513f) — regra em `totals.py`."""
        return await compute_session_amounts(self._session, session_id)

    async def compute_anomaly_breakdown(self, session_id: UUID) -> SessionAnomalyBreakdown:
        """Breakdown de anomalias da sessão INTEIRA — regra em `totals.py`."""
        return await compute_anomaly_breakdown(self._session, session_id)

    async def compute_card_charges(self, session_id: UUID, cipher: ClientCipher) -> Decimal:
        """Encargos do cartão (descrição cifrada → decrypt em memória) — regra em `totals.py`."""
        return await compute_card_charges_total(self._session, session_id, cipher)

    async def get_client(self, client_id: UUID) -> Client | None:
        """Carrega o `Client` (o envelope cripto precisa de `dek_wrapped`)."""
        return (
            await self._session.execute(select(Client).where(Client.id == client_id))
        ).scalar_one_or_none()

    async def count_files(self, session_id: UUID) -> int:
        """Nº de partes da sessão (inclui as que falharam na extração)."""
        total: int | None = await self._session.scalar(
            select(func.count(ReconciliationFile.id)).where(
                ReconciliationFile.session_id == session_id
            )
        )
        return total or 0

    async def existing_file_hashes(self, session_id: UUID, hashes: list[str]) -> set[str]:
        """Interseção entre os hashes informados e os que a sessão já tem.

        Pré-check do anexo: permite responder "a parte X é duplicata, mas a Y é
        nova" — a UNIQUE sozinha só diria que a operação toda falhou.
        """
        if not hashes:
            return set()
        rows = await self._session.execute(
            select(ReconciliationFile.file_hash).where(
                ReconciliationFile.session_id == session_id,
                ReconciliationFile.file_hash.in_(hashes),
            )
        )
        return set(rows.scalars().all())

    async def get_file(self, session_id: UUID, file_id: UUID) -> ReconciliationFile | None:
        """Uma parte da sessão. Filtra por `session_id` de propósito: o id da
        parte vem da URL e não pode servir para alcançar outra conciliação."""
        file_obj: ReconciliationFile | None = await self._session.scalar(
            select(ReconciliationFile).where(
                ReconciliationFile.id == file_id,
                ReconciliationFile.session_id == session_id,
            )
        )
        return file_obj

    async def count_parsed_files(self, session_id: UUID) -> int:
        """Nº de partes que efetivamente trouxeram linhas (`status='parsed'`)."""
        total: int | None = await self._session.scalar(
            select(func.count(ReconciliationFile.id)).where(
                ReconciliationFile.session_id == session_id,
                ReconciliationFile.status == ReconciliationFileStatus.PARSED.value,
            )
        )
        return total or 0

    async def delete_file(self, file_id: UUID) -> None:
        """Remove a parte. As linhas dela vão junto pelo `ON DELETE CASCADE`."""
        await self._session.execute(
            delete(ReconciliationFile).where(ReconciliationFile.id == file_id)
        )

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
        matches: list[tuple[UUID, int, str]],
    ) -> None:
        """Aplica os matches `(file_entry_id, omie_lancamento_id, situation)`.

        Atualiza `omie_lancamento_id` e a `situation` de cada linha casada. A
        `situation` vem JÁ decidida pelo caller (job.py): `conciliado` para
        data exata, `conciliado_data_divergente` para 1-3 dias de divergência
        (FASE 1 — ver matcher.DATE_DIVERGENCE_RANGE). Faz UPDATE individual
        (não bulk) porque o número de matches é bounded por
        `total_file_entries` (geralmente < 200) — performance é dominada por
        outras etapas, e UPDATE individual é mais legível que
        `update().values()` com CASE/WHEN.
        """
        for file_entry_id, omie_lancamento_id, situation in matches:
            await self._session.execute(
                update(ReconciliationFileEntry)
                .where(ReconciliationFileEntry.id == file_entry_id)
                .values(
                    situation=situation,
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
        balance_start: Decimal | None = None,
        balance_end_file: Decimal | None = None,
        balance_end_omie: Decimal | None = None,
        balance_difference: Decimal | None = None,
    ) -> None:
        """Atualiza contadores + saldos + status='reviewing' + processed_at=now().

        Guarda de cancelamento (`WHERE status='processing'`): se o usuário
        cancelou a sessão (→ `error`) enquanto o job rodava, este UPDATE casa
        0 linhas e o cancelamento prevalece — o job não ressuscita a sessão
        cancelada como `reviewing`.
        """
        from datetime import UTC, datetime

        await self._session.execute(
            update(ReconciliationSession)
            .where(
                ReconciliationSession.id == session_id,
                ReconciliationSession.status == ReconciliationStatus.PROCESSING.value,
            )
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
                error_code=None,
            )
        )

    async def mark_session_error(
        self,
        session_id: UUID,
        *,
        user_message: str,
        error_code: str | None = None,
    ) -> None:
        """Marca a sessão como `status='error'` + `error_message` + `error_code`.

        Usado pelo worker quando alguma etapa falha (Omie indisponível,
        parsing inconsistente, etc). O `user_message` é em PT-BR — vem do
        `AppError.user_message` da exceção que disparou.

        `error_code` (Sprint 4) é o CÓDIGO canônico do desfecho: é o que a tela
        de erro e a notificação mostram para o usuário reportar, sem expor a
        linguagem interna (S2/R9). Opcional para não quebrar callers antigos —
        `None` deixa a coluna nula e a UI cai na mensagem.

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
                error_code=error_code,
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
            )
        )
        # 3. Reset da sessão.
        await self._session.execute(
            update(ReconciliationSession)
            .where(ReconciliationSession.id == session_id)
            .values(
                status=ReconciliationStatus.PROCESSING.value,
                error_message=None,
                error_code=None,
                processed_at=None,
                conciliated_count=0,
                sem_omie_count=0,
                omie_sem_arquivo_count=0,
                anomaly_count=0,
                balance_start=None,
                balance_end_file=None,
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
