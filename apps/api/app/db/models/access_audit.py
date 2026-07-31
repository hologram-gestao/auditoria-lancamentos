"""Modelo AccessAudit — auditoria de acesso a dados sensíveis (Sprint 3, BACK 03.5).

Nasce nesta sprint: a base NUNCA teve auditoria (as 9 tabelas da migration raiz
não incluíam nenhuma). Registra a lista FECHADA de eventos:
    - `denied` — tentativa de acesso fora da carteira (gravado ANTES da conversão
      403→404 anti-enumeração; ver `core/dependencies.require_client_access`).
    - `view`   — abrir a tela de conciliação de uma sessão.
    - `export` — exportar o relatório de um cliente (a leitura que MAIS decifra).

Sprint 5 (R6) — TENANT DO ATOR: além do tenant ALVO (`client_id`), a linha passa
a registrar `user_scope` (`system`|`client`) e `actor_client_id` (tenant do ator,
nulo para a equipe Hologram). Sem isso a negação cross-tenant fica cega: dá para
ver "negaram acesso ao cliente B", não "um usuário do tenant A pediu o B".

    **Por que `denied` e não uma 4ª ação `cross_tenant_denied`?** A ação responde
    "o que aconteceu" (o acesso foi negado) — isso não muda por causa do ator. O
    cross-tenant é uma PROPRIEDADE do ator, e já é derivável da própria linha:
    `user_scope='client' AND actor_client_id IS DISTINCT FROM client_id`. Criar
    uma 4ª ação partiria as consultas existentes da S3 em dois (`action='denied'
    OR action='cross_tenant_denied'`) e obrigaria backfill do histórico para
    reclassificar — custo sem ganho. A lista fechada { denied, view, export }
    permanece.

Guardrails (CONTEXT.md — ## Outcome):
    - **SEM PII — só IDs.** Nada de nome, razão social, descrição ou texto livre.
    - **Não é "todo GET"** — só os 3 eventos acima. Listar clientes na home NÃO
      gera registro (senão a tabela vira gargalo de escrita e a auditoria, inútil).
    - **Sem FK**: log append-only e durável, independente do ciclo de vida das
      linhas que referencia (apagar um cliente não apaga sua trilha de auditoria).
      Vale também para `actor_client_id` (Sprint 5).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin
from app.db.models.user import UserScope


class AccessAudit(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "access_audit"

    # Só IDs — nunca PII. Sem ForeignKey de propósito (log independente).
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # Tenant ALVO — o cliente cujo dado foi pedido/lido/exportado.
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # Escopo do ATOR: 'system' | 'client' — ver `app.db.models.user.UserScope`.
    # server_default 'system': as linhas da S3 (e qualquer INSERT que não passe
    # pelo ORM) são, por definição, de usuários da equipe Hologram.
    user_scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=UserScope.SYSTEM.value,
        default=UserScope.SYSTEM.value,
    )
    # Tenant do ATOR. NULO para `user_scope='system'` (a equipe Hologram não
    # pertence a tenant nenhum). Cross-tenant = client + actor_client_id != client_id.
    actor_client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, default=None
    )
    session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # 'denied' | 'view' | 'export' — ver `app.core.audit.AccessAction`.
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # Caminho da request (ex.: '/api/v1/clients/{id}'). Só o path — sem query
    # string (evita vazar filtros/termos de busca).
    rota: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Índices enxutos p/ a leitura do D+30 sem penalizar a escrita (guardrail
        # de volume). Consulta típica: por cliente ao longo do tempo, e por ação.
        Index("ix_access_audit_client_timestamp", "client_id", "timestamp"),
        Index("ix_access_audit_action_timestamp", "action", "timestamp"),
    )

    def __repr__(self) -> str:
        return (
            f"<AccessAudit id={self.id} action={self.action!r} "
            f"client_id={self.client_id} user_scope={self.user_scope!r}>"
        )
