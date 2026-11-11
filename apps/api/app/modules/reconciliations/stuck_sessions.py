"""Watchdog das conciliações paradas em `processing` (BACK 03.6 + 86e3mpmcw).

Rede de segurança do processamento: o `asyncio.timeout` do job e os `except` já
marcam erro no caminho normal; o que sobra é o processo morto no meio (OOM,
instância reciclada), que deixa a sessão em `processing` para sempre. O Cloud Run
Job `scripts/mark_stuck_sessions_as_error.py` chama este módulo de hora em hora.

O "heartbeat" é o `updated_at` da sessão, renovado também a cada etapa que o job
grava (`ProcessingStage`). A sessão sem sinal há mais de `STUCK_THRESHOLD_MINUTES`
vira `error` com `RECONCILIATION_TIMEOUT`, e o alerta de plantão diz, por sessão, os
IDs, a competência, a etapa em que parou, há quanto tempo foi o último sinal e o
link da tela. Só IDs, códigos, datas e números (§4.5, §4.7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text

from app.core.alerting import Alert, AlertCode, AlertContextLine
from app.core.exceptions import ErrorCode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Margem confortável acima do timeout do processamento (RECONCILIATION_TIMEOUT_SECONDS
# = 900s = 15min). 25min evita marcar como stuck um job que está só processando
# lentamente.
STUCK_THRESHOLD_MINUTES = 25

#: Teto de sessões detalhadas no alerta; acima disso, "e mais N".
STUCK_ALERT_MAX_SESSIONS = 5

#: Texto da etapa quando a sessão não a tem (anterior à coluna, ou não começou).
STAGE_NOT_RECORDED = "não registrada"

STUCK_ERROR_MESSAGE = (
    "Processamento cancelado: sessao em 'processing' por mais de "
    f"{STUCK_THRESHOLD_MINUTES} minutos sem conclusao (cleanup automatico)."
)

# A CTE trava e LÊ as linhas antes do UPDATE: é o único jeito de devolver o
# `updated_at` ANTERIOR (o UPDATE o sobrescreve com NOW()). `SKIP LOCKED` deixa de
# fora a sessão que o job está gravando neste instante (ela está viva). Os minutos
# sem sinal saem do relógio do BANCO, o mesmo que gravou o `updated_at`.
_MARK_STUCK_SQL = text(
    """
    WITH stuck AS (
        SELECT id, client_id, omie_conta_id, reference_month, updated_at, processing_stage
        FROM reconciliation_sessions
        WHERE status = 'processing'
          AND updated_at < NOW() - (:minutes || ' minutes')::interval
        FOR UPDATE SKIP LOCKED
    )
    UPDATE reconciliation_sessions AS s
    SET status = 'error',
        error_message = :msg,
        -- Sprint 4: o código acompanha a mensagem em TODO caminho que marca erro.
        error_code = :code,
        updated_at = NOW()
    FROM stuck
    WHERE s.id = stuck.id
    RETURNING stuck.id,
              stuck.client_id,
              stuck.omie_conta_id,
              stuck.reference_month,
              stuck.processing_stage,
              FLOOR(EXTRACT(EPOCH FROM (NOW() - stuck.updated_at)) / 60)::int AS idle_minutes
    """
).bindparams(
    msg=STUCK_ERROR_MESSAGE,
    code=ErrorCode.RECONCILIATION_TIMEOUT.value,
    minutes=STUCK_THRESHOLD_MINUTES,
)


@dataclass(frozen=True, slots=True)
class StuckSession:
    """Uma sessão que o watchdog acabou de marcar como erro. Só IDs e números."""

    session_id: UUID
    client_id: UUID
    omie_conta_id: int
    reference_month: date
    processing_stage: str | None
    idle_minutes: int


async def mark_stuck_sessions_as_error(db: AsyncSession) -> list[StuckSession]:
    """Marca como `error` as sessões sem sinal e devolve o que marcou.

    Roda na transação do CALLER. Ordem do retorno: a que está há mais tempo sem
    sinal primeiro (é a que o alerta mostra no topo).
    """
    rows = (await db.execute(_MARK_STUCK_SQL)).all()
    stuck = [
        StuckSession(
            session_id=row.id,
            client_id=row.client_id,
            omie_conta_id=row.omie_conta_id,
            reference_month=row.reference_month,
            processing_stage=row.processing_stage,
            idle_minutes=row.idle_minutes,
        )
        for row in rows
    ]
    stuck.sort(key=lambda s: (-s.idle_minutes, str(s.session_id)))
    return stuck


def session_link(frontend_url: str, *, client_id: UUID, session_id: UUID) -> str:
    """Link da tela da conciliação (`/clientes/{client}/conciliacao/{sessão}`)."""
    return f"{frontend_url.rstrip('/')}/clientes/{client_id}/conciliacao/{session_id}"


def _context_of(session: StuckSession, frontend_url: str) -> tuple[AlertContextLine, ...]:
    return (
        ("sessão", str(session.session_id)),
        ("cliente", str(session.client_id)),
        ("conta Omie", str(session.omie_conta_id)),
        ("competência", session.reference_month.strftime("%Y-%m")),
        ("etapa", session.processing_stage or STAGE_NOT_RECORDED),
        ("último sinal", f"há {session.idle_minutes} min"),
        (
            "link",
            session_link(frontend_url, client_id=session.client_id, session_id=session.session_id),
        ),
    )


def build_heartbeat_missing_alert(stuck: Sequence[StuckSession], *, frontend_url: str) -> Alert:
    """O alerta `heartbeat_missing` com um bloco por sessão (até o teto) e "e mais N"."""
    shown = stuck[:STUCK_ALERT_MAX_SESSIONS]
    count = len(stuck)
    return Alert(
        code=AlertCode.HEARTBEAT_MISSING,
        message=(
            f"{count} conciliação(ões) parada(s) em processamento, marcada(s) como erro "
            f"pelo watchdog: sem sinal há mais de {STUCK_THRESHOLD_MINUTES} minutos."
        ),
        context=tuple(_context_of(session, frontend_url) for session in shown),
        context_omitted=count - len(shown),
    )
