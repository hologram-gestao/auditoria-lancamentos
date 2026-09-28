"""Schemas da conta do banco de cada conta de origem (Sprint 16, BACK 16.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.client_movement import MAX_MOVEMENT_REF_CHARS, MAX_MOVEMENT_SOURCE_TYPE_CHARS
from app.db.models.mapping_catalog import DESTINATION_TYPE_PATTERN

if TYPE_CHECKING:
    from app.modules.client_source_accounts.service import SourceAccountEntry


class BankAccountResponse(BaseModel):
    """A conta do banco no plano contábil do cliente — código e nome da leitura."""

    id: UUID
    code: str = Field(description="Código reduzido (o que vai no arquivo contábil).")
    name: str = Field(
        description="Nome decifrado na leitura; `[indecifrável]` se a chave não abre."
    )
    name_resolved: bool = Field(alias="nameResolved")
    postable: bool = Field(
        description=(
            "Analítica e ativa HOJE. `false` = a conta foi inativada depois da associação "
            "(a associação segue valendo; trocar exige conta analítica e ativa)."
        )
    )

    model_config = ConfigDict(populate_by_name=True)


class SourceAccountEntryResponse(BaseModel):
    source_type: str = Field(
        alias="sourceType", description="Tipo do provedor (`omie`, `arquivo`)."
    )
    source_account_id: str | None = Field(
        default=None,
        alias="sourceAccountId",
        description="A conta de origem como a base a grava. `null` = o slot da CONTA PADRÃO.",
    )
    is_default: bool = Field(
        alias="isDefault",
        description=(
            "Slot da conta PADRÃO: cobre só as linhas SEM conta de origem (o arquivo sem "
            "coluna de conta). Conta de origem sem associação NUNCA cai nela."
        ),
    )
    pending: bool = Field(description="Sem conta do banco associada.")
    bank_account: BankAccountResponse | None = Field(default=None, alias="bankAccount")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, entry: SourceAccountEntry) -> SourceAccountEntryResponse:
        account = entry.account
        return cls(
            source_type=entry.source_type,
            source_account_id=entry.source_account_id,
            is_default=entry.is_default,
            pending=entry.pending,
            bank_account=(
                BankAccountResponse(
                    id=account.id,
                    code=account.code,
                    name=account.name,
                    name_resolved=account.name_resolved,
                    postable=account.postable,
                )
                if account is not None
                else None
            ),
        )


class SourceAccountListResponse(BaseModel):
    data: list[SourceAccountEntryResponse]


class SourceAccountBindingRequest(BaseModel):
    """Corpo de `PUT …/source-accounts` — define ou troca a conta do banco."""

    # `max_length` = a coluna (30): o padrão sozinho aceita até 60 e o INSERT estouraria em 500.
    source_type: str = Field(
        alias="sourceType",
        pattern=DESTINATION_TYPE_PATTERN,
        max_length=MAX_MOVEMENT_SOURCE_TYPE_CHARS,
        description="Tipo do provedor de origem (`omie`, `arquivo`…).",
    )
    source_account_id: str | None = Field(
        default=None,
        alias="sourceAccountId",
        max_length=MAX_MOVEMENT_REF_CHARS,
        description="A conta de origem; ausente/`null` = o slot da CONTA PADRÃO do tipo.",
    )
    accounting_account_id: UUID = Field(
        alias="accountingAccountId",
        description="A conta ANALÍTICA e ATIVA do plano contábil do cliente que é o banco.",
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("source_account_id")
    @classmethod
    def _clean_source_account(cls, value: str | None) -> str | None:
        # Identificador da origem: aparado; vazio não é "padrão" (é erro de forma, 400).
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("conta de origem vazia")
        return cleaned


class SourceAccountBindingPayload(BaseModel):
    entry: SourceAccountEntryResponse
    created: bool = Field(description="`true` = a associação não existia; `false` = foi trocada.")


class SourceAccountBindingEnvelope(BaseModel):
    data: SourceAccountBindingPayload
