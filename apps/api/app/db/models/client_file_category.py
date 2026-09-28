"""Modelo ClientFileCategory — as CATEGORIAS DE ORIGEM derivadas do arquivo (Sprint 14, BACK 14.4 — R4).

Cliente sem ERP não tem plano de contas para sincronizar: as categorias dele vêm
no próprio arquivo — uma coluna de categoria, ou uma coluna de classificação livre
(R4: "arquivo sem coluna de categoria não fica sem saída"). Esta tabela é o
REGISTRO dessas categorias: uma linha por grafia distinta encontrada, por cliente.

**`code` é um identificador ESTÁVEL gerado na primeira ocorrência — NUNCA derivado
do rótulo.** Um hash do rótulo faria "corrigir um acento no mês seguinte" virar
categoria nova e perder a decisão do mês anterior — exatamente o que o R4 proíbe.
Por isso `code` é `arq-` + hex aleatório (`new_file_category_code`), e o rótulo é
guardado à parte, como rótulo. Corrigir a grafia continua criando uma segunda
linha (fusão de grafias está fora de escopo global), mas a decisão da primeira
fica intacta.

**O rótulo é a grafia ORIGINAL da célula, CIFRADA com a DEK do cliente** (§4.5:
nome de categoria não persiste em claro; precedente `client_glossary_entries.
name_encrypted`). Envelope AES-256-GCM, AAD por linha
(`field_locator(AAD_FILE_CATEGORY_LABEL, <pk>)`), IV novo por operação. O CHECK
do par (`label_pair`) mantém ciphertext e IV juntos — mesmo molde de
`client_connections`. **Sem hash de lookup**: o casamento rótulo → código é feito
em memória pelo registry (dezenas/centenas de linhas por cliente, a mesma escala
que a ADR-076-BE aceita para o universo do de-para). Criar um HMAC com pepper aqui
seria mecanismo novo para um problema que não existe nessa escala.

**A "marcação derivada do arquivo" é o `source_type = 'arquivo'` das linhas da
base e do universo do de-para** — não uma coluna desta tabela. Esta tabela só
existe para dar NOME ao código na leitura.

**Encerramento:** entra explicitamente em `close_client_purge` (é configuração do
cliente e, cifrada, morre com a DEK de qualquer jeito); a exclusão definitiva a
leva por CASCADE.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH
from app.db.models.client_movement import MAX_MOVEMENT_CATEGORY_CODE_CHARS

if TYPE_CHECKING:
    from app.db.models.client import Client

#: Prefixo do código gerado. Diz de onde a categoria veio sem consultar nada, e
#: nunca colide com um código do Omie (`2.04.94`) nem do plano de contas.
FILE_CATEGORY_CODE_PREFIX = "arq-"

#: Bytes aleatórios do sufixo (12 hex). Com o prefixo dá 16 caracteres — folga
#: larga sob o teto de 50 do código de categoria da base.
_FILE_CATEGORY_CODE_RANDOM_BYTES = 6

#: A UNIQUE que faz "um código por cliente" — e o que o registry confia ao criar.
UQ_CLIENT_FILE_CATEGORY_CLIENT_CODE = "uq_client_file_categories_client_id_code"

#: Rótulo (não o nome final) do CHECK do par — a `NAMING_CONVENTION` prefixa
#: `ck_client_file_categories_`.
FILE_CATEGORY_LABEL_PAIR_CK_LABEL = "label_pair"
FILE_CATEGORY_LABEL_PAIR_CONSTRAINT = (
    f"ck_client_file_categories_{FILE_CATEGORY_LABEL_PAIR_CK_LABEL}"
)


def file_category_label_pair_check() -> str:
    """Predicado SQL do CHECK do par rótulo — copiado na migration."""
    return "(label_encrypted IS NULL) = (label_iv IS NULL)"


def new_file_category_code() -> str:
    """Um código NOVO, aleatório, sem relação com rótulo nenhum (R4)."""
    return f"{FILE_CATEGORY_CODE_PREFIX}{secrets.token_hex(_FILE_CATEGORY_CODE_RANDOM_BYTES)}"


class ClientFileCategory(UUIDPrimaryKeyMixin, Base):
    """Uma categoria de origem derivada do arquivo — append-only (sem `updated_at`).

    A linha nunca muda: o código é estável e o rótulo é a grafia da PRIMEIRA
    ocorrência. Uma grafia nova é outra linha.
    """

    __tablename__ = "client_file_categories"

    __table_args__ = (
        UniqueConstraint("client_id", "code", name=UQ_CLIENT_FILE_CATEGORY_CLIENT_CODE),
        CheckConstraint(
            text(file_category_label_pair_check()), name=FILE_CATEGORY_LABEL_PAIR_CK_LABEL
        ),
    )

    #: CASCADE: a exclusão DEFINITIVA leva as categorias. O ENCERRAMENTO as purga
    #: explicitamente (`close_client_purge`). Sem índice próprio: a UNIQUE serve
    #: toda busca por `client_id` pelo prefixo.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: O identificador ESTÁVEL (`arq-<hex>`), e o que a base de movimentos grava
    #: em `category_code`. Mesmo teto da base, de propósito: é o mesmo código.
    code: Mapped[str] = mapped_column(String(MAX_MOVEMENT_CATEGORY_CODE_CHARS), nullable=False)

    # ---- rótulo: SEMPRE cifrado, envelope com DEK do cliente + AAD ----------
    label_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    label_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)

    #: Quando a grafia apareceu pela primeira vez num arquivo do cliente.
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    client: Mapped[Client] = relationship("Client", lazy="raise")

    def __repr__(self) -> str:
        return f"<ClientFileCategory id={self.id} client={self.client_id} code={self.code!r}>"
