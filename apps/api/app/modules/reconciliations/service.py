"""Lógica de negócio do módulo de conciliações.

S8 (BACK 6.2): verificação de duplicata pré-criação de sessão.
S10 (BACK 8.1 + 8.6): criação atômica da sessão + entries (criptografando
descrições) e leitura do status para o polling.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.core.crypto import encrypt
from app.core.exceptions import DuplicateFileError, NotFoundError
from app.core.logging import get_logger
from app.core.search_index import compute_search_hmac
from app.db.models import (
    FileEntrySituation,
    OmieAccountType,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
)
from app.modules.reconciliations.repository import ReconciliationRepository
from app.modules.reconciliations.schemas import (
    CreateReconciliationRequest,
    SessionDetailPayload,
    SessionStatusPayload,
)

logger = get_logger(__name__)


def session_account_type_from_omie_tipo(omie_tipo: str | None) -> str:
    """Mapeia o `tipo` Omie da conta selecionada → `account_type` da sessão.

    Regra cravada (Risco #1 da FASE 1, validado com dado real da Austral em
    18/06): **apenas** `CR` (Cartão de Crédito) vira `credit_card`. Qualquer
    outro tipo — incluindo `CA` (Conta Aplicação) e `None` (conta não
    cacheada) — vira `checking`.

    ⚠️ NUNCA mapear `CA` para cartão: era exatamente o bug M-1 (auditoria
    20/05/2026) — `CA` é investimento, não cartão.
    """
    if omie_tipo == OmieAccountType.CREDIT_CARD.value:  # "CR"
        return SessionAccountType.CREDIT_CARD.value
    return SessionAccountType.CHECKING.value


class ReconciliationService:
    """Operações de domínio sobre conciliações."""

    def __init__(self, repository: ReconciliationRepository) -> None:
        self._repo = repository

    # ------------------------------------------------------------------
    # BACK 6.2 — verificação de duplicata
    # ------------------------------------------------------------------

    async def check_duplicate(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        reference_month: date,
        file_hash: str,
    ) -> bool:
        """Retorna True se já existe sessão com a tupla idempotente.

        O caller (route) é responsável pelo RBAC sobre `client_id` antes de
        chamar esta função; aqui é apenas uma consulta sem efeitos colaterais.
        Loga apenas o prefixo de 8 chars do hash — o valor completo é PII de
        higiene (não permite identificar o conteúdo do arquivo, mas evita
        deixar correlação fácil entre logs).
        """
        duplicate = await self._repo.exists_session_with_idempotency_key(
            client_id=client_id,
            omie_conta_id=omie_conta_id,
            reference_month=reference_month,
            file_hash=file_hash,
        )
        logger.info(
            "reconciliation_check_duplicate",
            client_id=str(client_id),
            omie_conta_id=omie_conta_id,
            month=reference_month.isoformat(),
            hash_prefix=file_hash[:8],
            duplicate=duplicate,
        )
        return duplicate

    # ------------------------------------------------------------------
    # BACK 8.1 — criação atômica da sessão
    # ------------------------------------------------------------------

    async def create_session_with_entries(
        self,
        *,
        request: CreateReconciliationRequest,
        created_by: UUID,
        encryption_key: SecretStr,
        search_blind_index_key: SecretStr,
    ) -> UUID:
        """Cria sessão `status='processing'` + file_entries criptografando
        cada `description` com IV próprio.

        Idempotência: se a UNIQUE
        `(client_id, omie_conta_id, reference_month, file_hash)` for violada
        (corrida com outro request), o `IntegrityError` vira `DuplicateFileError`
        (HTTP 409, code `DUPLICATE_FILE`). O front já fez o check otimista via
        `/check-duplicate`, mas a janela entre o GET e o POST permite race —
        cobrir aqui.

        Args:
            request: payload validado do front (statement do parsing + meta).
            created_by: UUID do usuário autenticado (vem da dependency).
            encryption_key: `OMIE_ENCRYPTION_KEY` em SecretStr — passamos
                explicitamente em vez de pegar de Settings dentro do service
                pra facilitar teste e deixar o fluxo de credencial mais óbvio
                (mesma regra que `omie_factory`).
            search_blind_index_key: `SEARCH_BLIND_INDEX_KEY` em SecretStr.
                Usada para computar o índice de busca paralelo
                (`description_search_hmac`) que viabiliza filtro `search`
                em SQL na Tela de Revisão (S16).

        Returns:
            UUID da sessão criada — caller usa pra enfileirar o job.
        """
        hex_key = encryption_key.get_secret_value()
        hex_blind_key = search_blind_index_key.get_secret_value()
        statement = request.statement

        # account_type vem do `tipo` Omie da conta SELECIONADA (cache L1),
        # não do palpite da IA no statement (CLAUDE.md §3.8 — não confiar no
        # client). Conta não cacheada → None → default 'checking'.
        omie_tipo = await self._repo.get_cached_account_type(
            client_id=request.client_id,
            omie_conta_id=request.omie_conta_id,
        )
        account_type = session_account_type_from_omie_tipo(omie_tipo)

        session_obj = ReconciliationSession(
            client_id=request.client_id,
            created_by=created_by,
            omie_conta_id=request.omie_conta_id,
            account_type=account_type,
            reference_month=request.reference_month,
            # Período REAL do statement — essencial para a Tela de Revisão
            # consultar /available-omie-entries com o intervalo correto
            # (extratos quebrados, faturas de cartão, atrasos).
            period_start=statement.period_start,
            period_end=statement.period_end,
            date_tolerance_days=request.date_tolerance_days,
            file_hash=request.file_hash,
            status=ReconciliationStatus.PROCESSING.value,
        )

        entries: list[ReconciliationFileEntry] = []
        for tx in statement.transactions:
            ct, iv = encrypt(tx.description, hex_key)
            # Blind index (S16) — gravado em paralelo à descrição criptografada.
            # Pode ser None para descrições só com pontuação/whitespace ou
            # tokens curtos — nesses casos a linha fica fora do filtro `search`,
            # mesmo comportamento que sessões pré-S16 (ver migration b6f1c4d29e57).
            search_hmac = compute_search_hmac(tx.description, hex_blind_key)
            entries.append(
                ReconciliationFileEntry(
                    transaction_date=tx.date,
                    description_encrypted=ct,
                    description_iv=iv,
                    description_search_hmac=search_hmac,
                    amount=tx.amount,
                    balance=tx.balance,
                    situation=FileEntrySituation.SEM_OMIE.value,
                )
            )

        try:
            await self._repo.add_session_with_entries(session_obj, entries)
        except IntegrityError as exc:
            # CLAUDE.md §5.8: UNIQUE violation = duplicata.
            if "uq_recon_sessions_idempotency" in str(exc.orig):
                raise DuplicateFileError(
                    "Sessão duplicada para a tupla "
                    f"(client_id={request.client_id}, conta={request.omie_conta_id}, "
                    f"mes={request.reference_month}, hash={request.file_hash[:8]})",
                ) from exc
            # Outras violações de UNIQUE/FK/etc: relança — vira 500 INTERNAL.
            raise

        logger.info(
            "reconciliation_session_created",
            session_id=str(session_obj.id),
            client_id=str(request.client_id),
            account_type=account_type,
            total_file_entries=len(entries),
            month=request.reference_month.isoformat(),
            tolerance_days=request.date_tolerance_days,
        )
        return session_obj.id

    # ------------------------------------------------------------------
    # BACK 8.6 — leitura para polling
    # ------------------------------------------------------------------

    async def get_session_status(self, session_id: UUID) -> SessionStatusPayload:
        """Retorna o estado atual da sessão para o polling do front.

        404 se sessão não existe. RBAC é responsabilidade do caller — esta
        função assume que `require_client_access` já validou.
        """
        session_obj = await self._repo.get_status_view(session_id)
        if session_obj is None:
            raise NotFoundError("Sessão de conciliação não encontrada.")
        return SessionStatusPayload(
            session_id=session_obj.id,
            status=session_obj.status,
            conciliated_count=session_obj.conciliated_count,
            sem_omie_count=session_obj.sem_omie_count,
            omie_sem_arquivo_count=session_obj.omie_sem_arquivo_count,
            anomaly_count=session_obj.anomaly_count,
            error_message=session_obj.error_message,
        )

    # ------------------------------------------------------------------
    # S11 — GET /reconciliations/{id}  (header da Tela de Revisão)
    # ------------------------------------------------------------------

    async def get_session_detail(self, session_id: UUID) -> SessionDetailPayload:
        """Retorna o detalhe da sessão para o header da Tela de Revisão.

        Espelha `get_session_status` mas devolve `SessionDetailPayload`,
        incluindo `client_id`, `omie_conta_id`, `reference_month` e
        `total_file_entries` — campos que o front antes resolvia via
        scan O(N) do histórico do cliente.

        404 se sessão não existe. RBAC é responsabilidade do caller.
        """
        session_obj = await self._repo.get_detail_view(session_id)
        if session_obj is None:
            raise NotFoundError("Sessão de conciliação não encontrada.")
        return SessionDetailPayload(
            session_id=session_obj.id,
            client_id=session_obj.client_id,
            omie_conta_id=session_obj.omie_conta_id,
            reference_month=session_obj.reference_month,
            status=session_obj.status,
            total_file_entries=session_obj.total_file_entries,
            conciliated_count=session_obj.conciliated_count,
            sem_omie_count=session_obj.sem_omie_count,
            omie_sem_arquivo_count=session_obj.omie_sem_arquivo_count,
            anomaly_count=session_obj.anomaly_count,
            error_message=session_obj.error_message,
            balance_start=session_obj.balance_start,
            balance_end_file=session_obj.balance_end_file,
            balance_end_omie=session_obj.balance_end_omie,
            balance_difference=session_obj.balance_difference,
        )
