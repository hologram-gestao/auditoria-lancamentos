"""Camada de dados da BASE DE MOVIMENTOS (Sprint 12, BACK 12.1 — R0).

SQL puro, uma função por query. **Toda leitura carrega `client_id` no próprio
`WHERE`** — nenhuma query alcança uma linha só pela PK: ela pertence a um tenant, e
o tenant vem do `Client` já validado pela rota (§3.15).

**A lei desta camada: nunca `DELETE` de movimento.** Um movimento que sai da
origem vira `ausente_na_origem`; a linha FICA. O único `DELETE` da base é o do
encerramento do cliente inteiro, e ele mora na lista declarada de
`ClientRepository.close_client_purge` — não aqui.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import CursorResult, String, all_, bindparam, func, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.client_movement import (
    UQ_CLIENT_MOVEMENT_SOURCE,
    ClientMovement,
    MovementStatus,
)
from app.db.models.client_movement_sync import UQ_CLIENT_MOVEMENT_SYNC, ClientMovementSync

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

#: Linhas por comando no upsert. 12 placeholders por linha contra o teto de
#: 65.535 do protocolo do Postgres dá ~5.400; 1.000 é folga de 5x. Mesmo número da
#: carteira (S11) e do plano de contas (S10), de propósito: dois tetos diferentes
#: para o mesmo problema seriam duas coisas para lembrar.
UPSERT_CHUNK_SIZE = 1000


@dataclass(frozen=True, slots=True)
class MovementCycleOutcome:
    """O que um ciclo fez no banco — contagens, nunca linhas."""

    #: Quantos movimentos a origem devolveu e foram gravados (inseridos OU
    #: atualizados) neste ciclo.
    upserted: int
    #: Quantos estavam `presente` na competência e saíram nesta passada.
    absent: int


@dataclass(frozen=True, slots=True)
class MovementSyncState:
    """O estado da base de UMA competência — os dois relógios.

    `nunca_sincronizada` é campo derivado num lugar só (ADR-067-BE): a tela e a
    prévia do de-para afirmam O CAMPO, nunca "zero movimentos" — competência vazia
    de verdade e competência nunca consultada são respostas diferentes.
    """

    synced_at: datetime | None
    sync_failed_at: datetime | None

    @property
    def nunca_sincronizada(self) -> bool:
        return self.synced_at is None


class ClientMovementsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_for_competence(
        self,
        client_id: UUID,
        competence: date,
        *,
        status: MovementStatus | None = None,
    ) -> list[ClientMovement]:
        """Os movimentos DESTE cliente NESTA competência, em ordem estável.

        A ordem (`movement_date`, `source_type`, `source_movement_id`) é total —
        quem consome (a aplicação do de-para, 12.6) não depende dela, mas o teste e
        a leitura humana sim.
        """
        stmt = select(ClientMovement).where(
            ClientMovement.client_id == client_id,
            ClientMovement.competence == competence,
        )
        if status is not None:
            stmt = stmt.where(ClientMovement.status == status.value)
        stmt = stmt.order_by(
            ClientMovement.movement_date,
            ClientMovement.source_type,
            ClientMovement.source_movement_id,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_for_client(self, client_id: UUID) -> int:
        """Quantas linhas o cliente tem na base, em qualquer competência e situação.

        Usado pelo teste de "falha no meio preserva a base anterior" e pelo de
        encerramento: a contagem é a prova, não a ausência de erro.
        """
        stmt = select(func.count()).where(ClientMovement.client_id == client_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def get_sync_state(self, client_id: UUID, competence: date) -> MovementSyncState:
        """Os dois relógios da competência. Sem linha = nunca tentou."""
        stmt = select(ClientMovementSync.synced_at, ClientMovementSync.sync_failed_at).where(
            ClientMovementSync.client_id == client_id,
            ClientMovementSync.competence == competence,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return MovementSyncState(synced_at=None, sync_failed_at=None)
        return MovementSyncState(synced_at=row.synced_at, sync_failed_at=row.sync_failed_at)

    # ------------------------------ WRITE -----------------------------

    async def reconcile_cycle(
        self,
        client_id: UUID,
        *,
        source_type: str,
        competence: date,
        rows: Sequence[dict[str, Any]],
        synced_at: datetime,
    ) -> MovementCycleOutcome:
        """Aplica UM ciclo: atualiza o que mudou, insere o novo e marca quem saiu.

        As duas metades são inseparáveis e rodam na MESMA transação — publicar o
        que veio sem marcar o que saiu deixaria a base somando movimentos que a
        origem já não tem, e é assim que a cobertura do de-para mente.

        `rows` precisa chegar DEDUPLICADO por `source_movement_id` (o serviço faz
        isso): o mesmo par duas vezes no MESMO comando é erro do Postgres
        (`ON CONFLICT DO UPDATE command cannot affect row a second time`).
        """
        upserted = await self._upsert_many(client_id, rows, synced_at=synced_at)
        absent = await self._mark_absent(
            client_id,
            source_type=source_type,
            competence=competence,
            keep_source_ids=[str(row["source_movement_id"]) for row in rows],
        )
        return MovementCycleOutcome(upserted=upserted, absent=absent)

    async def _upsert_many(
        self,
        client_id: UUID,
        rows: Sequence[dict[str, Any]],
        *,
        synced_at: datetime,
    ) -> int:
        """Grava o que a origem devolveu. Devolve quantas linhas entraram.

        `ON CONFLICT (client_id, source_type, source_movement_id) DO UPDATE` — a
        idempotência é da UNIQUE do banco, não de uma leitura anterior.

        Toda linha revista volta a `presente`: movimento que estava
        `ausente_na_origem` e reapareceu volta a valer. E a `competence` é
        reescrita: movimento que a origem mudou de mês muda de competência aqui.
        """
        if not rows:
            return 0

        payload = [dict(row, client_id=client_id, last_synced_at=synced_at) for row in rows]
        for start in range(0, len(payload), UPSERT_CHUNK_SIZE):
            chunk = payload[start : start + UPSERT_CHUNK_SIZE]
            stmt = pg_insert(ClientMovement).values(chunk)
            stmt = stmt.on_conflict_do_update(
                constraint=UQ_CLIENT_MOVEMENT_SOURCE,
                set_={
                    "competence": stmt.excluded.competence,
                    "movement_date": stmt.excluded.movement_date,
                    "amount": stmt.excluded.amount,
                    "category_code": stmt.excluded.category_code,
                    "supplier_code": stmt.excluded.supplier_code,
                    "source_account_id": stmt.excluded.source_account_id,
                    "status": MovementStatus.PRESENTE.value,
                    "last_synced_at": stmt.excluded.last_synced_at,
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(stmt)
        return len(payload)

    async def _mark_absent(
        self,
        client_id: UUID,
        *,
        source_type: str,
        competence: date,
        keep_source_ids: Sequence[str],
    ) -> int:
        """Marca `ausente_na_origem` quem não veio nesta passada. **Nunca apaga.**

        O recorte é `(cliente, tipo de origem, competência)`: sincronizar junho não
        encosta em julho, e a origem Omie não encosta no que a Sprint 14 gravar
        como `arquivo`.

        A lista de mantidos vai como UM parâmetro de array (`<> ALL(:ids)`), não
        como `NOT IN (…)` expandido: uma competência grande estouraria o teto de
        65.535 placeholders do protocolo. Lista vazia (`<> ALL('{}')`) é verdade
        para toda linha — origem que devolveu nada marca tudo como ausente, que é
        o desfecho honesto de "a competência está vazia".

        `last_synced_at` NÃO é tocado: ele diz quando a linha foi vista pela
        origem pela última vez, e ela não foi.
        """
        keep = bindparam("keep_source_ids", value=list(keep_source_ids), type_=ARRAY(String()))
        stmt = (
            update(ClientMovement)
            .where(
                ClientMovement.client_id == client_id,
                ClientMovement.source_type == source_type,
                ClientMovement.competence == competence,
                ClientMovement.status == MovementStatus.PRESENTE.value,
                ClientMovement.source_movement_id != all_(keep),
            )
            .values(status=MovementStatus.AUSENTE_NA_ORIGEM.value, updated_at=func.now())
        )
        result = await self._session.execute(stmt)
        # UPDATE devolve CursorResult (com rowcount); o narrow é para o mypy.
        if not isinstance(result, CursorResult):  # pragma: no cover
            return 0
        return int(result.rowcount or 0)

    async def mark_sync_succeeded(self, client_id: UUID, competence: date, *, at: datetime) -> None:
        """Carimba o sucesso ÍNTEGRO e **limpa** a marca de falha.

        "Falhou" é sobre a ÚLTIMA tentativa: um sucesso depois de uma falha
        deixaria o aviso pendurado para sempre se a coluna só acumulasse.
        """
        stmt = pg_insert(ClientMovementSync).values(
            client_id=client_id, competence=competence, synced_at=at, sync_failed_at=None
        )
        stmt = stmt.on_conflict_do_update(
            constraint=UQ_CLIENT_MOVEMENT_SYNC,
            set_={"synced_at": at, "sync_failed_at": None, "updated_at": func.now()},
        )
        await self._session.execute(stmt)

    async def mark_sync_failed(self, client_id: UUID, competence: date, *, at: datetime) -> None:
        """Carimba a falha. **Nunca** toca o carimbo do último sucesso.

        É o que sustenta "falha preserva a última base íntegra" no schema, e não só
        no fluxo do serviço: o `set_` do conflito não contém `synced_at`.
        """
        stmt = pg_insert(ClientMovementSync).values(
            client_id=client_id, competence=competence, synced_at=None, sync_failed_at=at
        )
        stmt = stmt.on_conflict_do_update(
            constraint=UQ_CLIENT_MOVEMENT_SYNC,
            set_={"sync_failed_at": at, "updated_at": func.now()},
        )
        await self._session.execute(stmt)
