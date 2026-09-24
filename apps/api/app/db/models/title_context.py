"""Modelo TitleContext — contexto humano sobre um título da carteira (Sprint 15, BACK 15.1).

**Por que existe.** O título (Sprint 11, `client_titles`) só sabe o que a origem
declarou: valor, vencimento, situação. O que falta é o que só existe na cabeça
de quem atende o cliente — um acordo de pagamento quadrimestral, uma
antecipação, uma nota a cancelar. Sem lugar para isso, todo título vencido
conta como inadimplência real, mesmo quando não é (CONTEXT.md, Sprint 15).

**Append-only, de propósito.** Registrar de novo NÃO apaga o anterior — a
classificação usa o mais recente (`ORDER BY created_at DESC`), mas o histórico
inteiro fica visível no detalhe. Não existe `UPDATE`/`DELETE` no service: a
única forma de mudar a leitura de um título é registrar um contexto novo.
Sobrescrever apagaria a memória do porquê, que é o que a sprint existe para
preservar.

**`client_id` na própria linha, pelo mesmo motivo do precedente.** Segue o
molde de `reconciliation_file_entries.user_note_encrypted`
(`db/models/reconciliation_file_entry.py`) e `core/crypto.py:156-158`: o AAD
amarra `client_id ‖ tabela ‖ coluna ‖ pk`, então sem o cliente na linha não há
como cifrar. `title_id` sozinho não bastaria — o cliente tem de estar aqui,
mesmo sendo derivável via `client_titles.client_id`.

**`title_id` com `ondelete="CASCADE"`.** Um título nunca é apagado no ciclo de
sincronização (`ClientTitlesRepository` — "a lei desta camada: nunca DELETE de
título"); a única forma de a linha de `client_titles` sumir é o encerramento
(`close_client_purge`) ou a exclusão definitiva do cliente
(`delete_client_cascade`), e nos dois casos o contexto pendurado não tem mais
para onde apontar — por isso cai junto. `client_id` também é `CASCADE`, pelo
mesmo motivo do `client_id` de `ClientTitle`.

**`author_id` com `ondelete="RESTRICT"`.** Autoria é histórico — o mesmo
motivo de `created_by` em conciliações e clientes (`users.client_id`,
`d5c81a4e9b27`). Usuário nunca é apagado (só anonimizado), então a restrição
nunca é exercida na prática; ela existe para que um `DELETE` acidental de
`users` falhe alto em vez de apagar autoria em silêncio.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH


class TitleContextType(StrEnum):
    """Vocabulário FECHADO do contexto — fonte ÚNICA do CHECK de `context_type`.

    Os seis cobrem os casos observados na reunião de 17/06/2026 (CONTEXT.md).
    `outro` é a escotilha declarada: um tipo novo é acréscimo de enum, nunca
    migração de dado — não faz sentido travar a sprint esperando descobrir um
    sétimo caso real.

    **`PERDA_PROVAVEL` classifica como inadimplência, não como acordo** (R4) —
    é o único tipo cujo contexto mais recente NÃO tira o título do grupo de
    inadimplência real no relatório da BACK 15.2. Os outros quatro tipam
    "vencido com contexto".
    """

    ACORDO_DE_PAGAMENTO = "acordo_de_pagamento"
    PAGAMENTO_ANTECIPADO = "pagamento_antecipado"
    NOTA_A_CANCELAR = "nota_a_cancelar"
    COBRANCA_SUSPENSA = "cobranca_suspensa"
    PERDA_PROVAVEL = "perda_provavel"
    OUTRO = "outro"


#: Os tipos cujo contexto MAIS RECENTE classifica o título como "vencido com
#: contexto" no relatório da BACK 15.2 — todos MENOS `perda_provavel`, que
#: continua contando como inadimplência real.
CONTEXT_TYPES_NOT_DELINQUENT = frozenset(
    {
        TitleContextType.ACORDO_DE_PAGAMENTO,
        TitleContextType.PAGAMENTO_ANTECIPADO,
        TitleContextType.NOTA_A_CANCELAR,
        TitleContextType.COBRANCA_SUSPENSA,
        TitleContextType.OUTRO,
    }
)

#: Rótulo (não o nome final) do CHECK — a `NAMING_CONVENTION` do `Base` prefixa
#: `ck_title_contexts_`.
TITLE_CONTEXT_TYPE_CK_LABEL = "context_type"
TITLE_CONTEXT_TYPE_CONSTRAINT = f"ck_title_contexts_{TITLE_CONTEXT_TYPE_CK_LABEL}"

#: Índice que sustenta "histórico completo, mais recente primeiro" (GET) e a
#: verificação de existência do filtro "vencidos sem contexto" (GET /titles).
IX_TITLE_CONTEXT_TITLE_ID_CREATED_AT = "ix_title_contexts_title_id_created_at"


def title_context_type_check() -> str:
    """Predicado SQL do CHECK de `context_type` — a MESMA string vai na migration."""
    valores = ", ".join(f"'{t.value}'" for t in TitleContextType)
    return f"context_type IN ({valores})"


class TitleContext(UUIDPrimaryKeyMixin, Base):
    """Uma entrada de contexto sobre um título — append-only, sem `updated_at`.

    Não usa `TimestampMixin`: a linha nunca é atualizada, e um `updated_at` que
    nunca muda seria um campo mentindo sobre a natureza append-only da tabela.
    """

    __tablename__ = "title_contexts"

    __table_args__ = (
        Index(IX_TITLE_CONTEXT_TITLE_ID_CREATED_AT, "title_id", "created_at"),
        CheckConstraint(text(title_context_type_check()), name=TITLE_CONTEXT_TYPE_CK_LABEL),
    )

    #: CASCADE: a exclusão DEFINITIVA do cliente apaga tudo que pende dele
    #: (§4.12), e contexto sem cliente não é nada.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: CASCADE: título só desaparece no encerramento/exclusão do cliente — ver
    #: docstring do módulo. Índice próprio via `IX_TITLE_CONTEXT_TITLE_ID_CREATED_AT`.
    title_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("client_titles.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Valor de `TitleContextType`, travado por CHECK no banco.
    context_type: Mapped[str] = mapped_column(String(30), nullable=False)

    #: Texto livre do analista — AES-256-GCM (13º par de AAD do sistema,
    #: `core/crypto_service.AAD_TITLE_CONTEXT_TEXT`, congelado). Sempre
    #: presente: é o payload da entrada, não uma nota opcional.
    text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    text_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)

    #: RESTRICT: autoria é histórico — ver docstring do módulo.
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<TitleContext id={self.id} title={self.title_id} type={self.context_type!r}>"
