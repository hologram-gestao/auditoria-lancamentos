"""Parcela FUTURA provisionada no Contas a Pagar (86e3qhhtb).

Compra parcelada no cartão: a linha `7/10` da fatura de setembro provisiona o
título dela e, se a pessoa pedir, os títulos das parcelas 8, 9 e 10, para as
faturas seguintes conciliarem sozinhas. Cada parcela futura é uma linha desta
tabela, FILHA da intenção da linha que a criou (`reconciliation_omie_postings`).

**O medo do Gabriel é o critério:** subir a fatura de outubro não pode criar o
título da parcela 8 de novo. Por isso a parcela tem IDENTIDADE própria, e a
fatura seguinte a ENCONTRA (`claimed_by_file_entry_id`) em vez de criar outra:

  - a chave de integração do título vem da linha que criou a série e do NÚMERO
    da parcela (`keys.derive_installment_payable_key`), nunca do conteúdo — a
    Omie é idempotente sobre ela, e `UNIQUE(client_id, payable_key)` impede que
    duas parcelas do ADL usem a mesma;
  - `UNIQUE(posting_id, installment_number)`: uma série tem UMA parcela de cada
    número, e é o que torna o registro idempotente sob concorrência;
  - `UNIQUE(claimed_by_file_entry_id)`: uma linha de fatura encontra no máximo
    uma parcela, e o UPDATE condicional do encontro garante que uma parcela é
    encontrada por no máximo uma linha.

`client_id`, `omie_conta_id`, `supplier_code` e `amount` são copiados da série de
propósito: são a identidade que a fatura seguinte procura (mesmo cliente, mesmo
cartão, mesmo fornecedor, mesmo valor, mesmo `N/M`), e toda query filtra por
`client_id` sem depender de JOIN (§4.11). Só códigos e valores: nada de nome,
CNPJ nem descrição (§4.5).

O passo é `titulo` (a fazer) ou `concluido` (título criado). A parcela futura
não tem baixa: a baixa vem com a fatura em que ela cair.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

#: `codigo_lancamento_integracao` é `string60` na Omie; a chave da parcela tem 23.
INSTALLMENT_PAYABLE_KEY_LENGTH = 60
INSTALLMENT_STEP_LENGTH = 12


class InstallmentStep(StrEnum):
    """`titulo`: o título da parcela ainda não foi confirmado. `concluido`: foi."""

    TITULO = "titulo"
    CONCLUIDO = "concluido"


#: Rótulos e predicados dos CHECKs, copiados na migration `c8d1f4e6a2b9`;
#: `tests/unit/test_installment_schema.py` compara as duas fontes.
INSTALLMENT_STEP_CK_LABEL = "step_coherent"
INSTALLMENT_STEP_CK = (
    "step IN ('titulo', 'concluido') AND ((step = 'concluido') = (payable_id IS NOT NULL))"
)
#: Parcela FUTURA: nunca a primeira (a 1ª é a própria linha), nunca além do total.
INSTALLMENT_NUMBER_CK_LABEL = "number_in_range"
INSTALLMENT_NUMBER_CK = (
    "installment_number >= 2 AND installment_total <= 99 "
    "AND installment_number <= installment_total"
)
INSTALLMENT_AMOUNT_CK_LABEL = "amount_positive"
INSTALLMENT_AMOUNT_CK = "amount > 0"
#: Só uma parcela com título criado pode ser encontrada pela fatura seguinte.
INSTALLMENT_CLAIM_CK_LABEL = "claim_after_title"
INSTALLMENT_CLAIM_CK = "claimed_by_file_entry_id IS NULL OR step = 'concluido'"


class ReconciliationOmiePostingInstallment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_omie_posting_installments"

    __table_args__ = (
        UniqueConstraint(
            "posting_id", "installment_number", name="uq_recon_omie_installments_posting_number"
        ),
        UniqueConstraint("client_id", "payable_key", name="uq_recon_omie_installments_client_key"),
        UniqueConstraint("claimed_by_file_entry_id", name="uq_recon_omie_installments_claimed_by"),
        # O que a fatura seguinte procura: a identidade da compra no mesmo cartão.
        Index(
            "ix_recon_omie_installments_identity",
            "client_id",
            "omie_conta_id",
            "supplier_code",
            "installment_total",
            "installment_number",
        ),
        CheckConstraint(text(INSTALLMENT_STEP_CK), name=INSTALLMENT_STEP_CK_LABEL),
        CheckConstraint(text(INSTALLMENT_NUMBER_CK), name=INSTALLMENT_NUMBER_CK_LABEL),
        CheckConstraint(text(INSTALLMENT_AMOUNT_CK), name=INSTALLMENT_AMOUNT_CK_LABEL),
        CheckConstraint(text(INSTALLMENT_CLAIM_CK), name=INSTALLMENT_CLAIM_CK_LABEL),
    )

    #: A intenção da linha que criou a série (a parcela `N` da fatura corrente).
    posting_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_omie_postings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: `nCodCC` da conta do cartão da série (o `id_conta_corrente` do título).
    omie_conta_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: `codigo_cliente_omie` do fornecedor da série (o mesmo do título da linha).
    supplier_code: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Valor da parcela, POSITIVO (o `valor_documento` do título).
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    installment_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    installment_total: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: Vencimento: o dia da compra nos meses seguintes (resposta (a) do Gabriel).
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Emissão e previsão do título (resposta (c)): a data da fatura em que a
    #: parcela cai no processo "na data da compra"; o vencimento no outro.
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: `codigo_lancamento_integracao` enviado à Omie.
    payable_key: Mapped[str] = mapped_column(String(INSTALLMENT_PAYABLE_KEY_LENGTH), nullable=False)
    step: Mapped[str] = mapped_column(
        String(INSTALLMENT_STEP_LENGTH),
        nullable=False,
        default=InstallmentStep.TITULO.value,
        server_default=text(f"'{InstallmentStep.TITULO.value}'"),
    )
    #: `codigo_lancamento_omie` do título da parcela.
    payable_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Envios do título sem resposta conclusiva (mesma regra da provisão: zera
    #: com `faultstring`, teto `MAX_STEP_SENDS`).
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Mensagem do provedor, VERBATIM, nunca logada (ADR-022-BE).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A linha da fatura seguinte que ENCONTROU esta parcela (`ja_lancada`).
    claimed_by_file_entry_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_file_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: A anomalia `parcela_nao_veio` que avisou desta parcela, para não avisar de
    #: novo. Reprocessar a conciliação apaga a anomalia (SET NULL) e o aviso volta.
    missing_anomaly_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_anomalies.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<OmiePostingInstallment id={self.id} posting={self.posting_id} "
            f"n={self.installment_number}/{self.installment_total} step={self.step}>"
        )
