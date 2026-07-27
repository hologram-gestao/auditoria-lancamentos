"""Modelo Notification — aviso in-app de fim de conciliação (Sprint 4, BACK 04.4).

Nasce para responder a pergunta literal da reunião de 07/07: *"a pessoa sai
dessa tela — como é que ela sabe que acabou?"*. Ao a sessão atingir `reviewing`
(Processada) ou `error` (Erro), o autor recebe uma linha aqui; o sino do header
faz poll de `/notifications/unread-count`.

**Sem PII, por construção.** O conteúdo é guardado em COLUNAS TIPADAS — conta
(`omie_conta_id`), mês (`reference_month`), tipo e código de erro — nunca um
texto livre. Não existe campo onde a descrição de um lançamento, um CNPJ ou uma
razão social caberia; o texto que o usuário lê é montado no front a partir
desses campos. `error_code` é CÓDIGO (S2/R9): a tela mostra "não foi possível
concluir (cód. X)", nunca a linguagem interna do erro.

**Sem FK** (mesma decisão de `access_audit`/`usage_events`): a trilha é
append-only e independe do ciclo de vida das linhas que referencia.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin


class NotificationType(StrEnum):
    """Desfecho que gerou o aviso — o vocabulário da UI (§17 / R4).

    `PROCESSADA` cobre a transição para `reviewing`; `ERRO`, para `error`. São
    os dois únicos momentos em que o sistema interrompe quem está trabalhando —
    notificar mais que isso é ruído, e ruído faz o sino ser ignorado (S-2).
    """

    PROCESSADA = "processada"
    ERRO = "erro"


class Notification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"

    # Destinatário. Hoje é sempre o AUTOR da conciliação; notificar também os
    # gerentes da carteira é explicitamente opcional no PRD e ficou de fora
    # para não transformar o sino em ruído antes de S-2 ser testada.
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # Guardado junto para o RBAC da leitura (o filtro por carteira acontece sem
    # precisar buscar a sessão) e para o front rotear sem uma 2ª chamada.
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    # 'processada' | 'erro' — ver `NotificationType`.
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)

    # Payload mínimo: conta + mês identificam a conciliação na frase do aviso
    # ("Cartão Itaú — Junho/2026"). O NOME da conta não entra: é dado do
    # cliente final e o front já o resolve pelo cache de contas.
    omie_conta_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_month: Mapped[date] = mapped_column(SQLDate, nullable=False)
    # Preenchido só quando `tipo='erro'`. CÓDIGO canônico, nunca mensagem.
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # NULL = não lida. Marcar como lida é idempotente (só escreve se NULL).
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # `unread-count` é chamado a cada 15 s por usuário logado: o índice
        # PARCIAL só indexa as não lidas, então a contagem toca um índice
        # pequeno que não cresce com o histórico já lido.
        Index(
            "ix_notifications_user_unread",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        # Listagem paginada do sino: mais recentes primeiro.
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} user={self.user_id} "
            f"tipo={self.tipo} session={self.session_id}>"
        )
