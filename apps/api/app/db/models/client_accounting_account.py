"""Modelo ClientAccountingAccount — o PLANO DE CONTAS CONTÁBIL do cliente (Sprint 16, BACK 16.1 — R1).

⚠️ **Não é o plano de contas da Sprint 10** (`client_chart_of_accounts`), que é o
plano da ORIGEM (as categorias do Omie). Nem o catálogo de rótulos da organização
(`client_categories`). Este é o plano do SISTEMA CONTÁBIL DE DESTINO do mesmo
cliente: as contas em que o escritório lança (`649` = o banco, `662` = o aluguel do
inquilino D, na amostra). O código `662` de um cliente é outra coisa no plano de
outro — por isso a tabela é por CLIENTE, e não por organização.

**O código reduzido fica em CLARO** (é o que vai no arquivo contábil da Sprint 13, e
código não é nome, §4.5). **O nome é CIFRADO com a DEK do cliente**: nomes de conta
carregam nome de inquilino, de pessoa física, de fornecedor — dado do cliente final
(§4.5). Envelope AES-256-GCM, AAD por linha (`field_locator(AAD_ACCOUNTING_ACCOUNT_NAME,
<pk>)`), IV novo por operação; o CHECK do par (`name_pair`) mantém ciphertext e IV
juntos — molde de `client_connections` e de `client_file_categories`.

**Entra por importação de planilha, tudo ou nada** (`modules/client_accounting_chart/`).
A reimportação casa por código reduzido: conta nova entra, conta existente atualiza
nome/tipo/classificação (e volta a ativa), conta que sumiu da planilha vira INATIVA —
**nunca é apagada**, porque pode haver decisão do de-para apontando para ela (16.2).

**A lista sai na ordem da CLASSIFICAÇÃO** (86e3n70p9): `sort_key` é derivada em toda
escrita (`sort_key.chart_sort_key`) e a listagem só ordena por ela. Sintética em cima,
as analíticas dela abaixo, na ordem de TEXTO da classificação, que é a do Domínio.

**Só conta ANALÍTICA e ATIVA recebe decisão nova** — a regra mora num validador
único (`AccountingChartService.require_postable_account`), consumido pelo de-para
(16.2) e pela conta do banco (16.3).

**Encerramento:** entra EXPLICITAMENTE em `close_client_purge`, como o glossário: o
nome morre com a DEK, mas o código em claro sobreviveria. Na exclusão definitiva as
linhas saem ANTES dos usuários do tenant, por causa da autoria RESTRICT (ADR-074-BE).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.client import Client


class AccountingAccountType(StrEnum):
    """Tipo da conta no plano contábil — fonte ÚNICA do CHECK `account_type`.

    `analitica` recebe lançamento; `sintetica` só agrupa (totaliza as filhas) e
    NUNCA recebe decisão do de-para nem vira conta do banco.
    """

    ANALITICA = "analitica"
    SINTETICA = "sintetica"


#: Teto do código reduzido. Os sistemas contábeis usam código numérico curto (a
#: amostra tem 3 dígitos; o Domínio vai a 7). 20 é folga larga — e código maior é
#: RECUSADO na importação (`codigo_longo`), nunca truncado: truncado casaria com um
#: código-prefixo.
MAX_ACCOUNTING_ACCOUNT_CODE_CHARS = 20
#: Teto da classificação hierárquica opcional (`1.1.1.02.001`).
MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS = 40
#: Teto da chave de ordenação (`sort_key`, 86e3n70p9): a própria classificação (até
#: 40) ou o código reduzido (até 20) com os segmentos numéricos preenchidos até 6
#: dígitos, cujo pior caso (11 segmentos de 1 dígito) dá 11 x 6 + 10 = 76. 160 é a
#: largura que a coluna já tem; `tests/unit/test_accounting_chart_sort_key.py` prova
#: que cabe.
MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS = 160
#: A collation da coluna `sort_key`: `C` compara por BYTE. A do sistema (`en_US.utf8`
#: da glibc) ignora a pontuação e embaralha a classificação (`11 < 1.10 < 1.1.2`).
ACCOUNTING_ACCOUNT_SORT_KEY_COLLATION = "C"
#: Teto do NOME em claro, antes de cifrar — o mesmo do rótulo de categoria do arquivo
#: (`MAX_CATEGORY_LABEL_CHARS` da S14): nome de conta é rótulo, não texto livre.
MAX_ACCOUNTING_ACCOUNT_NAME_CHARS = 200

#: A UNIQUE que faz "um código por cliente" — a reimportação casa por ela.
UQ_ACCOUNTING_ACCOUNT_CLIENT_CODE = "uq_client_accounting_accounts_client_id_code"
#: O índice da LISTAGEM (86e3n70p9): toda página é `WHERE client_id ORDER BY sort_key`.
IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY = "ix_client_accounting_accounts_client_id_sort_key"

#: Rótulos (não os nomes finais) dos CHECKs — a `NAMING_CONVENTION` prefixa
#: `ck_client_accounting_accounts_`.
ACCOUNTING_ACCOUNT_NAME_PAIR_CK_LABEL = "name_pair"
ACCOUNTING_ACCOUNT_TYPE_CK_LABEL = "account_type"
ACCOUNTING_ACCOUNT_NAME_PAIR_CONSTRAINT = (
    f"ck_client_accounting_accounts_{ACCOUNTING_ACCOUNT_NAME_PAIR_CK_LABEL}"
)
ACCOUNTING_ACCOUNT_TYPE_CONSTRAINT = (
    f"ck_client_accounting_accounts_{ACCOUNTING_ACCOUNT_TYPE_CK_LABEL}"
)


def accounting_account_name_pair_check() -> str:
    """Predicado SQL do CHECK do par do nome — copiado na migration."""
    return "(name_encrypted IS NULL) = (name_iv IS NULL)"


def accounting_account_type_check() -> str:
    """Predicado SQL do CHECK do tipo, montado do enum — copiado na migration."""
    values = ", ".join(f"'{member.value}'" for member in AccountingAccountType)
    return f"account_type IN ({values})"


class ClientAccountingAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Uma conta do plano contábil do cliente."""

    __tablename__ = "client_accounting_accounts"

    __table_args__ = (
        UniqueConstraint("client_id", "code", name=UQ_ACCOUNTING_ACCOUNT_CLIENT_CODE),
        CheckConstraint(
            text(accounting_account_name_pair_check()),
            name=ACCOUNTING_ACCOUNT_NAME_PAIR_CK_LABEL,
        ),
        CheckConstraint(
            text(accounting_account_type_check()), name=ACCOUNTING_ACCOUNT_TYPE_CK_LABEL
        ),
        Index(IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY, "client_id", "sort_key"),
    )

    #: CASCADE: a exclusão DEFINITIVA do cliente leva o plano. O ENCERRAMENTO o
    #: purga explicitamente (`close_client_purge`). Sem índice próprio: a UNIQUE
    #: `(client_id, code)` começa por `client_id` e serve toda busca por cliente
    #: pelo prefixo (precedente `client_file_categories`, ADR-081-BE).
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: O código reduzido — em CLARO, é o que vai no arquivo contábil (Sprint 13).
    code: Mapped[str] = mapped_column(String(MAX_ACCOUNTING_ACCOUNT_CODE_CHARS), nullable=False)

    #: Classificação hierárquica (`1.1.1.02.001`), opcional. Estrutura, em claro.
    classification: Mapped[str | None] = mapped_column(
        String(MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS), nullable=True
    )

    #: A chave da ORDEM DA CLASSIFICAÇÃO (86e3n70p9), DERIVADA na gravação por
    #: `client_accounting_chart.sort_key.chart_sort_key` (a classificação como TEXTO,
    #: que é a ordem do Domínio; sem ela, o código com os segmentos numéricos
    #: preenchidos até 6 dígitos) e nunca inferida na leitura: a listagem só ordena por
    #: ela (`NULLS LAST`, depois código e id), em collation `C` (byte). Nula só em linha
    #: gravada por código anterior a esta coluna, na janela de deploy; a migration
    #: `b2f7c9e41d06` preencheu as existentes com a MESMA regra em SQL.
    sort_key: Mapped[str | None] = mapped_column(
        String(
            MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS, collation=ACCOUNTING_ACCOUNT_SORT_KEY_COLLATION
        ),
        nullable=True,
    )

    # ---- nome: SEMPRE cifrado, envelope com DEK do cliente + AAD ------------
    name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    name_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)

    account_type: Mapped[str] = mapped_column(String(20), nullable=False)

    #: `false` = sumiu da última planilha importada. A linha FICA (decisões podem
    #: apontar para ela); só não recebe decisão NOVA.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    #: Quem importou a planilha que criou/atualizou a linha. RESTRICT (ADR-074-BE):
    #: por isso a exclusão definitiva apaga o plano ANTES dos usuários do tenant.
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    client: Mapped[Client] = relationship("Client", lazy="raise")

    def __repr__(self) -> str:
        return (
            f"<ClientAccountingAccount id={self.id} client={self.client_id} "
            f"code={self.code!r} type={self.account_type} active={self.active}>"
        )
