"""Acesso a `leads` (86e3fr9ut).

Sem escopo de tenant, de propósito: o lead não pertence a cliente nem a organização,
e nenhuma rota LÊ leads (o Slack é onde a equipe os lê na primeira versão). A única
leitura é a contagem do limite por e-mail, que não devolve linha nenhuma.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.db.models.lead import Lead

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class LeadRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def count_recent_by_email(self, email: str, *, window: timedelta) -> int:
        """Quantos leads com este e-mail (sem caixa) chegaram dentro da janela.

        A janela é medida pelo relógio do BANCO (`now()`), o mesmo que carimba
        `created_at`: comparar com o relógio da aplicação abriria um vão do tamanho
        da diferença entre os dois.
        """
        stmt = select(func.count(Lead.id)).where(
            func.lower(Lead.email) == email.lower(),
            Lead.created_at > func.now() - window,
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def add(self, lead: Lead) -> Lead:
        self._db.add(lead)
        await self._db.flush()
        return lead

    async def mark_notified(self, lead: Lead, *, at: datetime) -> None:
        """Carimba o aviso aceito, na MESMA transação que gravou o lead."""
        lead.notified_at = at
        await self._db.flush()
