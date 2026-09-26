"""Camada de dados do de-para do cliente (Sprint 12, BACK 12.4 em diante).

SQL puro, uma função por query. **Toda query carrega `client_id` no próprio
`WHERE`** — o cliente vem da rota, já validado por `resolve_client_access` (§3.15).

**Decisão é append-only, com UMA exceção nomeada** (`resolve_inherited`): a decisão
HERDADA é proposta do sistema, não decisão de ninguém; quando uma pessoa decide a
mesma chave na MESMA competência de início, a linha herdada é resolvida no lugar
(ADR-075-BE). Decisão CONFIRMADA nunca é atualizada — alterar é vigência nova.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    ChartOfAccountsStatus,
    ClientChartOfAccount,
    ClientMappingDecision,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    ClientMovement,
    DecisionOrigin,
)
from app.db.models.client_mapping import UQ_CLIENT_MAPPING_DECISION
from app.db.models.mapping_catalog import MappingTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Itens por comando no INSERT da materialização — 13 placeholders por linha contra
#: o teto de 65.535; 1.000 é folga de 5x, o mesmo teto da base de movimentos.
_ITEM_CHUNK_SIZE = 1000


def _violated_constraint(exc: IntegrityError) -> str | None:
    """Nome da constraint violada, pelo diagnóstico do driver (psycopg)."""
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


def _decision_row(decision: ClientMappingDecision) -> dict[str, Any]:
    """As colunas que o serviço preenche; `created_at` fica com o default do banco.

    O `id` é gerado aqui: o objeto ORM ainda não passou por flush (o `default=uuid4`
    do mixin só roda no flush), e o INSERT é Core.
    """
    return {
        "id": decision.id or uuid4(),
        "client_id": decision.client_id,
        "source_type": decision.source_type,
        "category_code": decision.category_code,
        "destination_id": decision.destination_id,
        "decision_type": decision.decision_type,
        "target_id": decision.target_id,
        "origin": decision.origin,
        "effective_from": decision.effective_from,
        "author_id": decision.author_id,
    }


class ClientMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ DECISÕES --------------------------

    async def list_decisions(
        self,
        client_id: UUID,
        destination_id: UUID,
        *,
        category_codes: Iterable[str] | None = None,
    ) -> list[ClientMappingDecision]:
        """TODAS as vigências do cliente no destino (opcionalmente só destes códigos).

        A resolução de "qual vale" é da função pura (`vigencia.py`), sobre o
        conjunto completo — recortar aqui por data seria uma segunda implementação.
        """
        stmt = select(ClientMappingDecision).where(
            ClientMappingDecision.client_id == client_id,
            ClientMappingDecision.destination_id == destination_id,
        )
        if category_codes is not None:
            stmt = stmt.where(ClientMappingDecision.category_code.in_(list(set(category_codes))))
        stmt = stmt.order_by(
            ClientMappingDecision.source_type,
            ClientMappingDecision.category_code,
            ClientMappingDecision.effective_from,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def target_codes(self, target_ids: Iterable[UUID]) -> dict[UUID, str]:
        """Código de cada alvo referenciado — uma query para o lote."""
        wanted = list({tid for tid in target_ids if tid is not None})
        if not wanted:
            return {}
        stmt = select(MappingTarget.id, MappingTarget.code).where(MappingTarget.id.in_(wanted))
        return {row.id: row.code for row in (await self._session.execute(stmt)).all()}

    async def insert_decisions(self, decisions: Sequence[ClientMappingDecision]) -> bool:
        """Grava vigências NOVAS, tudo ou nada, num SAVEPOINT.

        A UNIQUE `(chave, effective_from)` é a rede final contra a corrida entre o
        "já existe?" do serviço e esta escrita (duplo clique, duas abas, importação
        concorrente). Devolve `False` se ESSA UNIQUE barrou o lote — nada foi gravado
        e a transação de quem chamou segue viva; o serviço responde 409. Qualquer
        outra violação (CHECK, FK) re-levanta: é defeito, não corrida.
        """
        try:
            async with self._session.begin_nested():
                self._session.add_all(list(decisions))
                await self._session.flush()
        except IntegrityError as exc:
            if _violated_constraint(exc) != UQ_CLIENT_MAPPING_DECISION:
                raise
            return False
        return True

    async def insert_inherited(self, decisions: Sequence[ClientMappingDecision]) -> int:
        """Grava as HERDADAS com `ON CONFLICT DO NOTHING` na UNIQUE da vigência.

        "Iniciar de-para" é idempotente também SOB CONCORRÊNCIA: duas requisições
        juntas passam no "já decidido?" do serviço, e a segunda vira no-op aqui em vez
        de `IntegrityError` → 500. Devolve quantas linhas entraram DE FATO.
        """
        if not decisions:
            return 0
        stmt = (
            pg_insert(ClientMappingDecision)
            .values([_decision_row(d) for d in decisions])
            .on_conflict_do_nothing(constraint=UQ_CLIENT_MAPPING_DECISION)
            .returning(ClientMappingDecision.id)
        )
        return len((await self._session.execute(stmt)).scalars().all())

    async def resolve_inherited(
        self,
        decision_id: UUID,
        *,
        client_id: UUID,
        decision_type: str,
        target_id: UUID | None,
        author_id: UUID,
    ) -> bool:
        """A pessoa decide a chave na MESMA competência de uma HERDADA: resolve no lugar.

        A ÚNICA escrita não-append da tabela, e condicional no próprio SQL
        (`origin = 'herdada'`): uma decisão confirmada nunca muda por aqui. Devolve
        `False` se a linha não era (mais) herdada — o serviço então responde 409.
        """
        stmt = (
            update(ClientMappingDecision)
            .where(
                ClientMappingDecision.id == decision_id,
                ClientMappingDecision.client_id == client_id,
                ClientMappingDecision.origin == DecisionOrigin.HERDADA.value,
            )
            .values(
                decision_type=decision_type,
                target_id=target_id,
                origin=DecisionOrigin.CONFIRMADA.value,
                author_id=author_id,
                created_at=func.now(),
            )
        )
        result = await self._session.execute(stmt)
        if not isinstance(result, CursorResult):  # pragma: no cover
            return False
        return bool(result.rowcount)

    # ------------------------------ MATERIALIZAÇÕES (leitura) ---------

    async def materialized_competences(
        self, client_id: UUID, destination_id: UUID, competences: Iterable[date]
    ) -> list[date]:
        """Quais destas competências já têm materialização no destino (R4)."""
        wanted = list(set(competences))
        if not wanted:
            return []
        stmt = (
            select(ClientMappingMaterialization.competence)
            .where(
                ClientMappingMaterialization.client_id == client_id,
                ClientMappingMaterialization.destination_id == destination_id,
                ClientMappingMaterialization.competence.in_(wanted),
            )
            .distinct()
            .order_by(ClientMappingMaterialization.competence)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def latest_version(self, client_id: UUID, destination_id: UUID, competence: date) -> int:
        """A maior versão materializada de (cliente, destino, competência); 0 = nenhuma."""
        stmt = select(func.coalesce(func.max(ClientMappingMaterialization.version), 0)).where(
            ClientMappingMaterialization.client_id == client_id,
            ClientMappingMaterialization.destination_id == destination_id,
            ClientMappingMaterialization.competence == competence,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def insert_materialization(
        self,
        materialization: ClientMappingMaterialization,
        items: Sequence[dict[str, Any]],
    ) -> None:
        """Grava UMA versão e os itens dela, num SAVEPOINT.

        É a ÚNICA escrita de materialização do sistema: não existe `UPDATE` nem
        `DELETE` aqui (imutável, R5). O SAVEPOINT isola a corrida de versão — duas
        materializações simultâneas disputam a MESMA `UNIQUE(cliente, destino,
        competência, versão)`; a perdedora levanta `IntegrityError` sem derrubar a
        transação de quem chamou, e o serviço responde 409.
        """
        async with self._session.begin_nested():
            self._session.add(materialization)
            await self._session.flush()
            payload = [
                dict(
                    item, materialization_id=materialization.id, client_id=materialization.client_id
                )
                for item in items
            ]
            for start in range(0, len(payload), _ITEM_CHUNK_SIZE):
                await self._session.execute(
                    pg_insert(ClientMappingMaterializationItem).values(
                        payload[start : start + _ITEM_CHUNK_SIZE]
                    )
                )

    # ------------------------------ PLANO DE CONTAS (herança, R7) -----

    async def active_chart(self, client_id: UUID) -> list[ClientChartOfAccount]:
        """As categorias ATIVAS do plano de contas sincronizado do cliente."""
        stmt = (
            select(ClientChartOfAccount)
            .where(
                ClientChartOfAccount.client_id == client_id,
                ClientChartOfAccount.status == ChartOfAccountsStatus.ATIVA.value,
            )
            .order_by(ClientChartOfAccount.category_code)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def chart_category_codes(self, client_id: UUID) -> list[str]:
        """Todos os códigos do plano de contas do cliente (qualquer situação)."""
        stmt = (
            select(ClientChartOfAccount.category_code)
            .where(ClientChartOfAccount.client_id == client_id)
            .order_by(ClientChartOfAccount.category_code)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def movement_category_keys(self, client_id: UUID) -> set[tuple[str, str]]:
        """`(tipo de origem, código)` das categorias VISTAS na base de movimentos (R0).

        Categoria que só aparece no extrato (nunca no plano de contas) também é
        universo do de-para — é movimento que precisa de decisão. `DISTINCT` no
        banco: a base pode ter milhares de linhas para dezenas de códigos.
        """
        stmt = (
            select(ClientMovement.source_type, ClientMovement.category_code)
            .where(
                ClientMovement.client_id == client_id,
                ClientMovement.category_code.is_not(None),
            )
            .distinct()
        )
        rows = (await self._session.execute(stmt)).all()
        return {(row.source_type, row.category_code) for row in rows if row.category_code}

    async def chart_dre_codes(self, client_id: UUID) -> dict[str, str | None]:
        """`category_code → dre_code` do plano de contas (qualquer situação).

        É o que a leitura compara com a decisão vigente para SINALIZAR divergência
        depois de uma re-sincronização (R7) — nunca para reescrever decisão.
        """
        stmt = select(ClientChartOfAccount.category_code, ClientChartOfAccount.dre_code).where(
            ClientChartOfAccount.client_id == client_id
        )
        return {
            row.category_code: row.dre_code for row in (await self._session.execute(stmt)).all()
        }
