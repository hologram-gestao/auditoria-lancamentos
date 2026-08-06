"""Modelo ClientGlossaryEntry — glossário POR TENANT (Sprint 6, BACK 06.2).

O glossário é o vocabulário contábil do cliente final (categorias com uso,
fornecedores típicos, regras de auditoria). Ele existe para a **qualificação**
(`modules/reconciliations/qualification/semantic.py`) receber contexto do cliente
e flagar menos falso positivo — a hipótese do experimento da Sprint 6.

**Uma tabela só, com discriminador `kind`.** Categoria, fornecedor e regra têm o
mesmo formato (`code?` + `name` + `description?`), o mesmo dono (`client_id`), o
mesmo ciclo de vida e a mesma política de cripto. Três tabelas quase idênticas
triplicariam migration, repositório e caso negativo cross-tenant sem separar
nada de verdade. `kind` fica em CLARO: é enum fechado do sistema, não dado do
cliente, e é por ele que a leitura ordena/filtra.

**Campos textuais CIFRADOS** (CLAUDE.md §4.1/§4.5). Nome de categoria,
fornecedor típico e regra de auditoria são dado identificável do cliente final —
"Moinho Prado Ltda" num campo `name` em claro é exatamente o que §4.5 proíbe.
Envelope AES-256-GCM com a DEK do cliente, AAD por linha
(`field_locator(AAD_GLOSSARY_*, <pk>)`), IV novo a cada operação — o mesmo
caminho de `reconciliation_file_entries.description_encrypted`.

**Consequência que a camada de dados respeita:** ordenação, paginação e busca só
por colunas em CLARO (`kind`, `created_at`, `updated_at`, `id`). Não existe
índice sobre texto cifrado, e não se inventa um: ciphertext com IV novo por
operação não é comparável nem ordenável.

**Versão do glossário mora no TENANT**, não aqui: `clients.glossary_version` é um
contador incrementado na MESMA transação de qualquer escrita — inclusive a
remoção. `MAX(updated_at)` das entradas não serviria: um delete não mexe no MAX
e o cache do prompt (BACK 06.4) não seria invalidado.

**Soft delete** (`deleted_at`), padrão do repo: DELETE físico é proibido e toda
listagem filtra `deleted_at IS NULL`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.client import Client


class GlossaryEntryKind(StrEnum):
    """As três formas do glossário declaradas no PRD (R1).

    Fonte ÚNICA do vocabulário: o schema de entrada (BACK 06.3) e o renderizador
    do bloco de prompt (BACK 06.4) derivam deste enum, nunca redigitam a string.
    """

    CATEGORIA = "categoria"
    FORNECEDOR = "fornecedor"
    REGRA = "regra"


#: Tetos de tamanho, em caracteres do PLAINTEXT. Não são cosméticos: são eles
#: que impedem o bloco de system do glossário (BACK 06.4) de crescer sem limite
#: e estourar o teto de tokens (guardrail S-2/R9 do PRD). A validação de entrada
#: da BACK 06.3 usa ESTES números — não uma segunda cópia.
MAX_CODE_CHARS = 40
MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 500

#: Teto de entradas ATIVAS por cliente. Com os limites acima, o pior caso do
#: bloco de prompt é ~200 * (40 + 120 + 500) ≈ 132 KB de texto — ordem de 33k
#: tokens, abaixo do teto de entrada do modelo e ainda assim truncável pela
#: 06.4, que aplica o seu próprio limite determinístico.
MAX_ENTRIES_PER_CLIENT = 200


class ClientGlossaryEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_glossary_entries"

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # CASCADE como o resto do que pende de `clients`
        # (`omie_accounts_cache`, `reconciliation_sessions`): o glossário não
        # tem sentido sem o cliente e não deve travar a remoção dele.
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Discriminador em claro — enum do sistema, não dado do cliente.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # ---- campos textuais: SEMPRE cifrados, cada um com IV próprio ----
    #: Código contábil da categoria (ex.: "3.1.02"). Opcional: fornecedor e
    #: regra não têm código.
    code_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    #: Obrigatório nas três formas — nome da categoria, nome do fornecedor ou o
    #: enunciado da regra ("IOF nunca é classificado como juros").
    name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    name_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)

    #: Descrição de uso ("quando usar esta categoria"). Opcional.
    description_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    #: Soft delete (padrão do repo). Toda listagem filtra `IS NULL`.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    client: Mapped[Client] = relationship("Client", lazy="raise")

    __table_args__ = (
        # Listagem/leitura do prompt: "as entradas ativas deste cliente, na
        # ordem determinística (kind, created_at, id)". Parcial porque linha
        # removida nunca aparece em nenhuma das duas.
        Index(
            "ix_client_glossary_active",
            "client_id",
            "kind",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<ClientGlossaryEntry id={self.id} client_id={self.client_id} kind={self.kind!r}>"
