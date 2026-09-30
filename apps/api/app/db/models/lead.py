"""Modelo Lead — contato deixado no formulário da landing pública (86e3fr9ut).

É dado de PROSPECT, não do cliente final: quem preenche é alguém interessada em
conhecer a plataforma, antes de existir cliente, organização ou usuário. Por isso:

    - **fica em claro** (decisão do Pedro, 28/09/2026). A §4.5 do primer trata do
      dado do cliente final vindo da origem; aqui não há DEK para usar (a DEK é por
      cliente e não existe cliente) e cifrar exigiria uma chave de plataforma nova.
      A compensação é guardar o MÍNIMO: só os campos do formulário, sem IP, sem
      user agent, sem cookie;
    - **não tem FK** para `clients`, `organizations` ou `users`: o lead não pertence
      a tenant nenhum, e a rota que o grava é pública (`NON_TENANT_ENDPOINTS`);
    - `consent_at` e `consent_text_version` são a evidência do consentimento LGPD: o
      momento (relógio do SERVIDOR) e a versão do texto que a pessoa aceitou.

Os limites de tamanho são constantes daqui, reusadas pelo schema da rota: o limite
do schema é o da COLUNA (§7 Backend do primer), com teste que amarra os dois.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LEAD_NAME_MAX = 120
LEAD_EMAIL_MAX = 254
LEAD_COMPANY_MAX = 120
LEAD_WHATSAPP_MAX = 20
LEAD_MESSAGE_MAX = 1000
LEAD_CONSENT_VERSION_MAX = 20
LEAD_SOURCE_MAX = 30

#: Origem padrão do lead. Coluna existe para a próxima origem (evento, indicação)
#: não precisar de migration.
LEAD_SOURCE_LANDING = "landing"

#: Índice do limite por e-mail ("3 envios em 24h"): a consulta compara
#: `lower(email)` numa janela de `created_at`.
IX_LEADS_EMAIL_LOWER_CREATED_AT = "ix_leads_email_lower_created_at"


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index(IX_LEADS_EMAIL_LOWER_CREATED_AT, func.lower(text("email")), "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(LEAD_NAME_MAX), nullable=False)
    email: Mapped[str] = mapped_column(String(LEAD_EMAIL_MAX), nullable=False)
    company: Mapped[str | None] = mapped_column(String(LEAD_COMPANY_MAX), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(LEAD_WHATSAPP_MAX), nullable=True)
    message: Mapped[str | None] = mapped_column(String(LEAD_MESSAGE_MAX), nullable=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_text_version: Mapped[str] = mapped_column(
        String(LEAD_CONSENT_VERSION_MAX), nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(LEAD_SOURCE_MAX), nullable=False, server_default=LEAD_SOURCE_LANDING
    )
    #: Quando o Slack aceitou o aviso. Nulo = não avisado (webhook ausente ou falhou);
    #: o lead fica gravado do mesmo jeito (fail-soft).
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        # Sem nome nem e-mail: repr acaba em log e em traceback.
        return f"<Lead id={self.id} source={self.source!r}>"
