"""Orquestrador async do processamento de uma sessão de conciliação (BACK 8.1-8.5).

Ponto de entrada como **FastAPI BackgroundTask**:
`run_reconciliation_processing(session_id)`. Agendado por
`POST /reconciliations` e `/reprocess` via `BackgroundTasks.add_task` — roda
DEPOIS da resposta HTTP, no mesmo processo da API (FASE 0: sem Redis/ARQ).

Fluxo:
    1. Carrega sessão + cliente + file_entries (eager).
    2. Chama Omie em memória (extrato + contas pagar/receber). Se falhar →
       marca a sessão como `error` e encerra.
    3. Em uma única transação:
        a. Aplica matches (UPDATE file_entries).
        b. Insere omie_entries não consumidos.
        c. Cria anomalias estruturais.
        d. Atualiza contadores + status='reviewing' + processed_at.
       Falha no meio = rollback completo desta transação. A sessão original
       (criada pelo endpoint) já está commitada com `status='processing'`,
       então conseguimos reabrir uma transação para gravar o `error`.

CLAUDE.md §3.7: nunca expor stack traces ao usuário. O processamento:
    - NUNCA propaga exceção para o caller (a request HTTP já respondeu;
      um traceback poderia vazar credenciais via repr). Captura tudo, loga,
      marca a sessão como `error`.
    - Mensagem em PT-BR vem do `AppError.user_message`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.alerting import Alert, AlertCode, send_alert
from app.core.config import Settings
from app.core.crypto_service import provision_client_cipher
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models import FileEntrySituation, ReconciliationOmieEntry
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.clients.omie_factory import build_omie_client
from app.modules.reconciliations.processing.anomalies import (
    DivergentMatch,
    _AnomalyTypeMissingError,
    create_structural_anomalies,
)
from app.modules.reconciliations.processing.balances import compute_balances
from app.modules.reconciliations.processing.matcher import (
    DATE_DIVERGENCE_RANGE,
    FileEntryForMatch,
    match,
)
from app.modules.reconciliations.processing.omie_fetch import (
    deduplicate_by_id,
    fetch_pending,
    fetch_realized,
)
from app.modules.reconciliations.repository import ReconciliationRepository

if TYPE_CHECKING:
    from app.core.crypto import ClientCipher

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _FileEntryBalanceSnap:
    """Snapshot detached do `ReconciliationFileEntry` pra cálculo de saldos.

    Cópia explícita do que importa pra `compute_balances` antes da sessão
    SQLAlchemy fechar (mesma estratégia do `FileEntryForMatch`).
    """

    transaction_date: date
    amount: Decimal
    balance: Decimal | None
    created_at: datetime


# Mensagens em PT-BR para o `error_message` da sessão. Caller (front) mostra
# direto na UI — manter curtas e acionáveis. CLAUDE.md §7.
_ERROR_MSG_INTERNAL = "Erro interno ao processar a conciliação. Tente novamente."
_ERROR_MSG_TIMEOUT = (
    "Processamento cancelado por exceder o tempo máximo. "
    "Pode descartar esta sessão e tentar novamente."
)
_ERROR_MSG_SEED_MISSING = (
    "Catálogo de anomalias não inicializado. Avise o administrador (seed pendente)."
)


async def run_reconciliation_processing(
    session_id: str,
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Processa uma sessão de conciliação em background (FastAPI BackgroundTasks).

    Roda DEPOIS da resposta HTTP, no mesmo processo da API. Abre suas PRÓPRIAS
    sessions de DB (a session da request já foi fechada quando isto executa);
    por isso recebe um `session_factory`, não uma `AsyncSession`.

    Args:
        session_id: UUID em string.
        settings: injetável para teste. Em runtime cai no singleton global.
        session_factory: injetável para teste. Em runtime cai no singleton
            global inicializado no `lifespan` da app (`get_session_factory`).
    """
    resolved_settings = settings or _get_settings_singleton()
    resolved_factory = session_factory or _get_session_factory_singleton()

    sid = UUID(session_id)
    log.info("reconciliation_processing_started", session_id=session_id)

    try:
        # Teto de tempo do processamento — substitui o antigo `job_timeout=900`
        # do ARQ. Sem ele, uma BackgroundTask travada num `await` seguraria uma
        # conexão do pool indefinidamente. Ao estourar, `asyncio.timeout`
        # cancela `_execute_processing` por dentro e levanta `TimeoutError` aqui.
        async with asyncio.timeout(resolved_settings.RECONCILIATION_TIMEOUT_SECONDS):
            await _execute_processing(sid, resolved_settings, resolved_factory)
    except AppError as exc:
        # Erro previsto (Omie/Anthropic/Crypto/etc) — temos `user_message`
        # confiável em PT-BR.
        log.warning(
            "reconciliation_processing_failed",
            session_id=session_id,
            code=exc.code.value,
            message=exc.message,
        )
        await _safe_mark_error(sid, resolved_factory, exc.user_message, settings=resolved_settings)
    except _AnomalyTypeMissingError as exc:
        log.error(
            "reconciliation_processing_seed_missing",
            session_id=session_id,
            message=str(exc),
        )
        await _safe_mark_error(
            sid, resolved_factory, _ERROR_MSG_SEED_MISSING, settings=resolved_settings
        )
    except TimeoutError:
        # Estourou RECONCILIATION_TIMEOUT_SECONDS. `_execute_processing` foi
        # cancelado por dentro do `asyncio.timeout` e convertido em TimeoutError.
        log.error("reconciliation_processing_timeout", session_id=session_id)
        await _safe_mark_error(
            sid, resolved_factory, _ERROR_MSG_TIMEOUT, settings=resolved_settings
        )
    except asyncio.CancelledError:
        # Cancelamento EXTERNO (shutdown do processo / instância Cloud Run
        # reciclada). CancelledError herda de BaseException, então o
        # `except Exception` abaixo NÃO pega. Marca a sessão best-effort e
        # RE-PROPAGA (higiene asyncio: nunca engolir CancelledError).
        # `asyncio.shield` protege o cleanup de ser cancelado de novo no meio.
        # Rede de segurança final: cron `mark_stuck_sessions_as_error` (25min).
        log.error("reconciliation_processing_cancelled", session_id=session_id)
        await asyncio.shield(
            _safe_mark_error(sid, resolved_factory, _ERROR_MSG_TIMEOUT, settings=resolved_settings)
        )
        raise
    except Exception:
        # Erro inesperado: NUNCA propagar para o caller. Sentry captura via
        # exc_info=True (se configurado).
        log.exception("reconciliation_processing_unexpected", session_id=session_id)
        await _safe_mark_error(
            sid, resolved_factory, _ERROR_MSG_INTERNAL, settings=resolved_settings
        )
    else:
        log.info("reconciliation_processing_finished", session_id=session_id)


async def _execute_processing(
    session_id: UUID,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Caminho feliz — exceções sobem para o `run_reconciliation_processing`."""
    # 1. Load session + client + entries
    async with session_factory() as db:
        repo = ReconciliationRepository(db)
        session_obj = await repo.get_session_with_client(session_id)
        if session_obj is None:
            log.warning("reconciliation_session_not_found", session_id=str(session_id))
            return
        # Detach: vamos usar os dados depois fora desta sessão. Cópia explícita
        # do que importa pro matcher antes de fechar.
        client = session_obj.client
        file_entries_for_matcher = [
            FileEntryForMatch(
                id=str(entry.id),
                transaction_date=entry.transaction_date,
                amount=entry.amount,
            )
            for entry in session_obj.file_entries
        ]
        # Snapshot pro cálculo de saldos pós-Omie (depois de fechar a sessão).
        # Tuplas em vez do model porque vamos usar fora do contexto async.
        file_entries_for_balance = [
            _FileEntryBalanceSnap(
                transaction_date=entry.transaction_date,
                amount=entry.amount,
                balance=entry.balance,
                created_at=entry.created_at,
            )
            for entry in session_obj.file_entries
        ]
        period_start = min(e.transaction_date for e in session_obj.file_entries)
        period_end = max(e.transaction_date for e in session_obj.file_entries)
        omie_conta_id = session_obj.omie_conta_id
        reference_month = session_obj.reference_month
        account_type = session_obj.account_type

        # Provisiona a DEK do cliente (gera+embrulha via KMS se legado) DENTRO
        # desta sessão, onde `client` está anexado. O processamento ESCREVE
        # campos cifrados (anomalias wrong_date, motivo da qualificação), então
        # precisa da DEK; o mesmo `cipher` (bytes em memória) serve para ler as
        # credenciais Omie e para as escritas nos blocos seguintes.
        needs_dek_persist = client.dek_wrapped is None
        cipher = await provision_client_cipher(client, settings=settings)
        if needs_dek_persist:
            # Persistir a DEK recém-gerada é essencial: sem isso cada run geraria
            # uma DEK efêmera nova e o dado cifrado antes ficaria indecifrável.
            # `refresh` recarrega os atributos (o commit os expira) para que o
            # `client` siga utilizável, detached, nos passos seguintes.
            await db.commit()
            await db.refresh(client)

    # 2. Fetch Omie data — toda a interação com credencial em claro
    #    acontece dentro do `async with` do OmieClient.
    omie_client = build_omie_client(client, settings, cipher)
    # Cache L1 local a este processamento: popula supplier/category de cada
    # lançamento da janela atual pra alimentar a qualificação (S19). É uma
    # instância própria, não o singleton do app — a Tela de Revisão popula o
    # cache dela sob demanda (mesmo comportamento de antes; o que mudou é que
    # não há mais L2 Redis, então cada cache vive no seu processo).
    lancamento_cache = OmieLancamentoCache()
    try:
        async with omie_client:
            realized = await fetch_realized(
                omie_client,
                omie_conta_id=omie_conta_id,
                period_start=period_start,
                period_end=period_end,
                tolerance_days=DATE_DIVERGENCE_RANGE,
            )
            pending = await fetch_pending(
                omie_client,
                omie_conta_id=omie_conta_id,
                reference_month=reference_month,
            )
            if settings.QUALIFICATION_ENABLED:
                # Popula cache com supplier/category. Mesma janela expandida
                # que `fetch_realized` consumiu — 1 chamada Omie redundante
                # mas isolada (vide TODO no docstring do módulo).
                try:
                    await lancamento_cache.populate_from_extrato(
                        client_id=client.id,
                        omie_client=omie_client,
                        omie_conta_id=omie_conta_id,
                        period_start=period_start - timedelta(days=DATE_DIVERGENCE_RANGE),
                        period_end=period_end + timedelta(days=DATE_DIVERGENCE_RANGE),
                    )
                except Exception:
                    log.warning(
                        "qualification_cache_populate_failed",
                        session_id=str(session_id),
                    )
    finally:
        # Garantia extra de fechamento se o contexto async falhou antes do __aexit__.
        await omie_client.aclose()

    omie_movements = deduplicate_by_id([*realized, *pending])
    log.info(
        "reconciliation_omie_fetched",
        session_id=str(session_id),
        realized=len(realized),
        pending=len(pending),
        deduped=len(omie_movements),
    )

    # 3. Match — função pura, sem I/O. Usa o range fixo DATE_DIVERGENCE_RANGE
    #    (default do matcher) — não há mais tolerância parametrizável (FASE 1).
    result = match(file_entries_for_matcher, omie_movements)

    # Mapas data-por-id p/ classificar e montar o contexto da anomalia
    # wrong_date (BACK 1.7: "Data arquivo / Data Omie").
    file_date_by_id = {fe.id: fe.transaction_date for fe in file_entries_for_matcher}
    omie_date_by_id = {mov.omie_id: mov.transaction_date for mov in omie_movements}

    # Classifica cada match pelo days_diff (CLAUDE.md §5.2, FASE 1):
    #   days_diff == 0  → conciliado (data exata)
    #   1 ≤ days ≤ 3    → conciliado_data_divergente (+ anomalia wrong_date)
    matches_to_apply: list[tuple[UUID, int, str]] = []
    divergent_matches: list[DivergentMatch] = []
    for file_id, omie_id in result.matches:
        file_uuid = UUID(file_id)
        if result.days_diff_by_file_id[file_id] == 0:
            situation = FileEntrySituation.CONCILIADO.value
        else:
            situation = FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value
            divergent_matches.append(
                DivergentMatch(
                    file_entry_id=file_uuid,
                    file_date=file_date_by_id[file_id],
                    omie_date=omie_date_by_id[omie_id],
                )
            )
        matches_to_apply.append((file_uuid, omie_id, situation))

    log.info(
        "reconciliation_matched",
        session_id=str(session_id),
        total_file=len(file_entries_for_matcher),
        matched=len(result.matches),
        divergent=len(divergent_matches),
        unmatched_omie=len(result.unmatched_omie_indices),
    )

    # 4. Apply tudo em uma única transação: matches + omie_entries + anomalies + counters.
    async with session_factory() as db, db.begin():
        repo = ReconciliationRepository(db)

        # Pares (file_id, omie_id) p/ a qualificação (S19) — inclui exatos e
        # divergentes (ambos estão conciliados por valor).
        match_pairs_uuid = [(UUID(file_id), omie_id) for file_id, omie_id in result.matches]
        await repo.apply_matches(matches_to_apply)

        unmatched_omie = [omie_movements[idx] for idx in result.unmatched_omie_indices]
        omie_entries = [
            ReconciliationOmieEntry(
                session_id=session_id,
                omie_lancamento_id=mov.omie_id,
                transaction_date=mov.transaction_date,
                omie_status=mov.status,
            )
            for mov in unmatched_omie
        ]
        await repo.add_omie_entries(omie_entries)

        # Re-load file_entries unmatched (com IDs) na MESMA sessão. Não dá pra
        # reusar os objetos do passo 1 (sessão fechada). Query só pelos que
        # ficaram `sem_omie` após o `apply_matches`.
        unmatched_file_entries = await _load_unmatched_file_entries(db, session_id)
        structural_count = await create_structural_anomalies(
            db,
            session_id=session_id,
            unmatched_file_entries=unmatched_file_entries,
            persisted_omie_entries=omie_entries,
            divergent_matches=divergent_matches,
            cipher=cipher,
        )

        # Qualificação (S19 BACK 12.1): IA + histórico + outlier sobre os
        # pares conciliados. Roda na MESMA transação — falha NÃO derruba
        # o matching (try/except + log). Reusa a mesma sessão SQLAlchemy.
        qualification_count = await _run_qualification_safely(
            db,
            settings=settings,
            session_id=session_id,
            client_id=client.id,
            match_pairs=match_pairs_uuid,
            cache=lancamento_cache,
            account_type=account_type,
            cipher=cipher,
        )
        anomaly_count = structural_count + qualification_count

        total = len(file_entries_for_matcher)
        conciliated = len(match_pairs_uuid)
        sem_omie = total - conciliated
        omie_sem_arquivo = len(unmatched_omie)

        # Saldos: derivados das file_entries (start/end_file) + movimentos
        # Omie realizados no período estrito (end_omie). Função pura — testada
        # em unit. Falha silenciosa se faltar dado: campos viram None e a
        # aba 1 do Excel mostra "Indisponível".
        # mypy não infere que dataclasses concretas satisfazem o Protocol
        # de `compute_balances` (limitação de structural typing + invariância
        # de Sequence). Tipos checados em runtime via duck typing.
        balances = compute_balances(
            file_entries_for_balance,  # type: ignore[arg-type]
            omie_movements,  # type: ignore[arg-type]
            period_start=period_start,
            period_end=period_end,
        )

        await repo.update_session_after_matching(
            session_id,
            total_file_entries=total,
            conciliated_count=conciliated,
            sem_omie_count=sem_omie,
            omie_sem_arquivo_count=omie_sem_arquivo,
            anomaly_count=anomaly_count,
            balance_start=balances.balance_start,
            balance_end_file=balances.balance_end_file,
            balance_end_omie=balances.balance_end_omie,
            balance_difference=balances.balance_difference,
        )

    log.info(
        "reconciliation_session_reviewing",
        session_id=str(session_id),
        conciliated=conciliated,
        sem_omie=sem_omie,
        omie_sem_arquivo=omie_sem_arquivo,
        anomaly_count=anomaly_count,
        balance_start=str(balances.balance_start) if balances.balance_start is not None else None,
        balance_end_file=str(balances.balance_end_file)
        if balances.balance_end_file is not None
        else None,
        balance_end_omie=str(balances.balance_end_omie)
        if balances.balance_end_omie is not None
        else None,
        balance_difference=str(balances.balance_difference)
        if balances.balance_difference is not None
        else None,
    )


async def _load_unmatched_file_entries(
    db: AsyncSession,
    session_id: UUID,
) -> list[Any]:
    """Carrega file_entries com `situation='sem_omie'` da sessão.

    Uma query simples — usado dentro do bloco transacional do worker. Tipo
    de retorno é `list[ReconciliationFileEntry]`, mas usamos `Any` na
    assinatura para evitar import de tipo no topo do módulo (poderia ser
    `from __future__` mas o anomalies module já tipa).
    """
    from sqlalchemy import select

    from app.db.models import ReconciliationFileEntry

    rows = await db.execute(
        select(ReconciliationFileEntry).where(
            ReconciliationFileEntry.session_id == session_id,
            ReconciliationFileEntry.situation == FileEntrySituation.SEM_OMIE.value,
        )
    )
    return list(rows.scalars().all())


async def _safe_mark_error(
    session_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
    user_message: str,
    *,
    settings: Settings,
) -> None:
    """Marca a sessão como `error` em uma transação SEPARADA e ALERTA o plantão.

    Best-effort: se este UPDATE também falhar (ex: Postgres caiu), só logamos.
    A sessão fica em `processing` indefinidamente e o front mostra "ainda
    processando". Não há `error_message` recovery automático — alguém
    precisa rodar uma migration manual depois (cenário extremamente raro,
    aceitável para o MVP).

    BACK 03.6 — sessão em `error` CONTA como falha no monitoramento mesmo NÃO
    sendo 5xx (cobre o `ADL-PARSE-TRUNCADO` da Sprint 2, que hoje não dispararia
    nada). O alerta leva só session_id + code + a mensagem PT-BR genérica (sem PII).
    """
    try:
        async with session_factory() as db, db.begin():
            repo = ReconciliationRepository(db)
            await repo.mark_session_error(session_id, user_message=user_message)
    except Exception:
        log.exception("reconciliation_mark_error_failed", session_id=str(session_id))

    await send_alert(
        Alert(
            code=AlertCode.SESSION_ERROR,
            message=user_message,
            session_id=str(session_id),
        ),
        settings,
    )


async def _run_qualification_safely(
    db: AsyncSession,
    *,
    settings: Settings,
    session_id: UUID,
    client_id: UUID,
    match_pairs: list[tuple[UUID, int]],
    cache: OmieLancamentoCache,
    account_type: str,
    cipher: ClientCipher,
) -> int:
    """Roda a qualificação (S19) com try/except total — falha NÃO derruba.

    Args:
        db: AsyncSession ATIVA na mesma transação do matching.
        settings: já carregado pelo caller.
        session_id, client_id, match_pairs: contexto da sessão atual.
        cache: cache local com lançamentos atuais já populados (supplier/category).
        cipher: `ClientCipher` do cliente (DEK) — decifra descrições e cifra o
            motivo das anomalias de qualificação no envelope corrente + AAD.

    Returns:
        Quantidade de anomalias de qualificação criadas. 0 quando a flag
        está desligada, quando não há pares, ou quando algo falhou e o
        bloco caiu no except.
    """
    if not settings.QUALIFICATION_ENABLED:
        log.info("qualification_disabled", session_id=str(session_id))
        return 0
    if not match_pairs:
        return 0
    # Imports locais pra evitar carregar a dependência da Anthropic
    # quando a feature flag tá desligada (`pnpm dev` em devs sem
    # ANTHROPIC_API_KEY funciona normalmente).
    from app.integrations.anthropic.client import AnthropicClient
    from app.modules.reconciliations.qualification import qualify_session

    anthropic = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.ANTHROPIC_MODEL_DEFAULT,
        timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
        max_output_tokens=settings.ADL_PARSE_MAX_OUTPUT_TOKENS,
    )
    # SAVEPOINT (nested transaction): se o `qualify_session` falhar — seja
    # numa query, num flush quebrado, ou na chamada Anthropic — dá rollback
    # SÓ das anomalias parciais que ele inseriu, sem abortar a transação
    # outer (matching + estruturais já persistidos). Sem savepoint, um
    # `IntegrityError` aqui marcaria a session SQLAlchemy como "aborted"
    # e o `update_session_after_matching` adiante falharia.
    try:
        async with db.begin_nested():
            report = await qualify_session(
                db,
                session_id=session_id,
                client_id=client_id,
                match_pairs=match_pairs,
                cache=cache,
                anthropic_client=anthropic,
                cipher=cipher,
                account_type=account_type,
            )
        log.info(
            "qualification_done",
            session_id=str(session_id),
            **report.as_log_dict(),
        )
        return report.suspeitas + report.incoerentes + report.padrao_quebrado + report.valor_outlier
    except Exception:
        log.exception("qualification_failed", session_id=str(session_id))
        return 0


def _get_settings_singleton() -> Settings:
    """Lazy import para evitar circular: `app.core.config` é leve mas vamos
    seguir o padrão do resto do projeto (lru_cache singleton).
    """
    from app.core.config import get_settings

    return get_settings()


def _get_session_factory_singleton() -> async_sessionmaker[AsyncSession]:
    """Idem — lazy import."""
    from app.db.session import get_session_factory

    return get_session_factory()


# Exposto para o agendamento via BackgroundTasks no módulo de rotas.
__all__ = ["run_reconciliation_processing"]
