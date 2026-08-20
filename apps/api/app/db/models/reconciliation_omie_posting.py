"""Modelo ReconciliationOmiePosting — a INTENÇÃO de lançar uma linha no Omie.

Sprint 7 (BACK 07.2) — ADR-021-BE.

**Por que uma tabela própria, e não colunas em `reconciliation_file_entries`.**
A `file_entry` carrega o **resultado** da conciliação (situação, lançamento
vinculado). A intenção de lançamento tem ciclo de vida próprio — nasce antes do
POST, sobrevive a timeout, acumula tentativas, guarda o erro do fornecedor e
pode terminar em `failed` sem que a linha mude de estado. Espremer isso em
colunas da `file_entry` misturaria "o que a linha é" com "o que tentamos fazer
com ela", e o caminho de falha (o que mais importa aqui — é dinheiro na
contabilidade do cliente) ficaria sem lugar para morar.

**A dedup primária mora AQUI, no banco do ADL.** Que a Omie imponha unicidade
sobre `cCodIntLanc` é suposição NÃO-VERIFICADA (S-1, ver ADR-019-BE); esta
tabela é a proteção que existe hoje e é verificável hoje. Duas garantias no
BANCO, não só na aplicação:

  - ``UNIQUE(file_entry_id)`` — uma linha da fatura tem **uma** intenção. É o
    que faz `register_intent` ser idempotente sob concorrência.
  - ``UNIQUE(client_id, cod_int_lanc)`` — a chave enviada à Omie não se repete
    dentro do tenant. Colisão vira erro tratado (`IntegrityError`), nunca
    silêncio.

**A chave é POR-LINHA, nunca por conteúdo** (ver `omie_posting/keys.py`): duas
compras idênticas na mesma fatura (mesma data, mesmo valor, mesma descrição)
têm de gerar DOIS lançamentos. Um hash de conteúdo colapsaria as duas e
**deixaria de lançar a segunda** — dinheiro faltando, que é pior que duplicado,
porque o rollback da sprint só vigia duplicado.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.reconciliation_file_entry import ReconciliationFileEntry

#: `cCodIntLanc` é `string20` na Omie. O limite é do fornecedor, não nosso — e é
#: por causa dele que a chave precisa de um encoding provadamente não-colidente
#: (ver `omie_posting/keys.py`), em vez de "o UUID truncado".
COD_INT_LANC_MAX_LENGTH = 20


class OmiePostingStatus(StrEnum):
    """Ciclo de vida da intenção de lançamento.

    `pending` é gravado **antes** do POST — é isso que dá ao ADL um estado para
    consultar quando a resposta não chega (timeout). `failed` é definitivo para
    aquela tentativa: erro de negócio do provedor (`faultstring`) não se
    retenta sozinho.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class ReconciliationOmiePosting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_omie_postings"

    __table_args__ = (
        # Uma intenção por linha da fatura. É o que torna `register_intent`
        # idempotente sob concorrência (duplo-clique, retry, dois workers).
        UniqueConstraint("file_entry_id", name="uq_recon_omie_postings_file_entry"),
        # A chave enviada à Omie é única dentro do tenant. Se o encoding um dia
        # colidir, o INSERT falha — e falhar alto é o comportamento correto:
        # duas linhas com a mesma chave seriam uma delas nunca lançada.
        UniqueConstraint("client_id", "cod_int_lanc", name="uq_recon_omie_postings_client_cod_int"),
        # Leitura do lote: "as intenções desta sessão", por status.
        Index("ix_recon_omie_postings_session_status", "session_id", "status"),
    )

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Tenant, desnormalizado de propósito. Toda query desta tabela filtra por
    #: ele (S5/R3) — sem a coluna, o filtro exigiria JOIN com `sessions` em todo
    #: acesso, e o dia em que alguém esquecer o JOIN o filtro some sem aviso.
    #: Também é metade da chave `UNIQUE(client_id, cod_int_lanc)`.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_file_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: `cCodIntLanc` enviado à Omie. Derivado da IDENTIDADE da linha
    #: (`file_entry_id`), nunca do conteúdo — ver `omie_posting/keys.py`.
    cod_int_lanc: Mapped[str] = mapped_column(
        String(COD_INT_LANC_MAX_LENGTH),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OmiePostingStatus.PENDING.value,
    )
    #: `nCodLanc` devolvido pela Omie. BigInteger como em
    #: `reconciliation_file_entries.omie_lancamento_id` — o ID do Omie não cabe
    #: em `Integer` com folga garantida.
    omie_lancamento_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Mensagem do provedor, VERBATIM. Decisão registrada (ADR-022-BE): fica
    #: guardada porque o usuário precisa vê-la inline para agir, e **nunca é
    #: logada** — é texto livre de terceiro e o §3.3 do CLAUDE.md proíbe supor
    #: que texto livre externo esteja limpo de PII.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_entry: Mapped[ReconciliationFileEntry] = relationship(
        "ReconciliationFileEntry",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return (
            f"<OmiePosting id={self.id} entry={self.file_entry_id} "
            f"status={self.status} omie={self.omie_lancamento_id}>"
        )
