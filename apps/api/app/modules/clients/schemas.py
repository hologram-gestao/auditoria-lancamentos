"""Schemas Pydantic do módulo de clientes BPO (S6 + S7).

Princípios:
    - **Credenciais Omie NUNCA aparecem em response** — nem mascaradas, nem
      em qualquer outro formato (CLAUDE.md §3.2).
    - Request usa validadores estritos; Response usa tipos básicos para não
      derrubar listagens com registros legados (memória `feedback_pydantic`).
    - Update é PATCH (parcial): apenas campos enviados são alterados.
    - `responsible_manager` opcional — em teoria todo cliente tem assignment,
      mas a tela de listagem nunca deve quebrar se o registro estiver órfão.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.users.schemas import PaginationMeta


class CreateClientRequest(BaseModel):
    """Body de POST /api/v1/clients — cria cliente + auto-assign do criador.

    O backend confia que o frontend já chamou `/test-connection` antes (Doc §9.2);
    aqui apenas criptografa e persiste.
    """

    name: str = Field(..., min_length=1, max_length=200, description="Nome interno na Hologram.")
    omie_app_key: str = Field(..., min_length=1, max_length=200)
    omie_app_secret: str = Field(..., min_length=1, max_length=200)


class UpdateClientRequest(BaseModel):
    """Body de PATCH /api/v1/clients/{id} — campos opcionais (PATCH semântico).

    Para atualizar credenciais é OBRIGATÓRIO enviar `omie_app_key` E
    `omie_app_secret` juntos. Apenas um dos dois resulta em 400
    `IncompleteCredentialsError` (S6 §3.4).
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    active: bool | None = None
    omie_app_key: str | None = Field(None, min_length=1, max_length=200)
    omie_app_secret: str | None = Field(None, min_length=1, max_length=200)


class TestConnectionRequest(BaseModel):
    """Body de POST /api/v1/clients/test-connection — credenciais em texto plano.

    NUNCA persistido. Recebe, faz a chamada Omie, devolve ok/erro, descarta.
    """

    omie_app_key: str = Field(..., min_length=1, max_length=200)
    omie_app_secret: str = Field(..., min_length=1, max_length=200)


class TestConnectionResponse(BaseModel):
    """Response do test-connection: SEM detalhes técnicos, mensagem em PT-BR.

    `ok=False` cobre todos os modos de falha (auth, timeout, fault genérico) —
    a UI não distingue, apenas exibe `message`.
    """

    ok: bool
    message: str


class AssignClientRequest(BaseModel):
    """Body de POST /api/v1/clients/{id}/assign — admin reatribui o cliente."""

    user_id: UUID = Field(
        ..., description="ID do novo gerente responsável (deve ser manager ativo)."
    )


# ----------------------------------------------------------------------
# Responses — NUNCA expõem credenciais
# ----------------------------------------------------------------------


class ManagerSummary(BaseModel):
    """Subset de `User` exposto na listagem de clientes (gerente responsável)."""

    id: UUID
    name: str
    # `email` é `str` (não `EmailStr`) propositalmente: validação estrita só no
    # input. Em response, qualquer linha legada precisa serializar (memória).
    email: str

    model_config = {"from_attributes": True}


class ClientResponse(BaseModel):
    """Representação pública de um Client. NUNCA inclui campos `*_encrypted`/`*_iv`."""

    id: UUID
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime
    responsible_manager: ManagerSummary | None = None
    reconciliation_count: int = Field(0, ge=0)

    model_config = {"from_attributes": True}


class ClientListResponse(BaseModel):
    """Body de GET /api/v1/clients — lista paginada com metadata de paginação."""

    data: list[ClientResponse]
    pagination: PaginationMeta


# ----------------------------------------------------------------------
# S7 — Detalhe do cliente + cache L1 de contas + histórico de conciliações
# ----------------------------------------------------------------------


class BankAccountResponse(BaseModel):
    """Conta corrente Omie do cache L1 — exposta na tela de detalhe do cliente.

    `account_type` é `'CC'` (corrente) ou `'CA'` (cartão). Mantemos `str` em
    vez de enum no response (memória `feedback_pydantic`): se o Omie introduzir
    um novo `tipo` no futuro, a tela continua funcionando mesmo antes de o
    backend ser atualizado para reconhecê-lo.
    """

    id: UUID
    omie_conta_id: int = Field(..., description="nCodCC do Omie (BigInteger).")
    name: str
    bank_name: str
    account_type: str = Field(..., description="'CC' ou 'CA'.")
    synced_at: datetime

    model_config = {"from_attributes": True}


class ClientDetailResponse(ClientResponse):
    """Body de GET /api/v1/clients/{id} — detalhe + contas do cache L1.

    Estende `ClientResponse`. `accounts_synced_at` é o MAX(synced_at) entre as
    linhas; o front usa para mostrar "Sincronizado há Xh". `None` apenas se
    não há nenhuma conta cacheada (cliente novo + Omie retornou zero contas).
    """

    accounts: list[BankAccountResponse] = Field(default_factory=list)
    accounts_synced_at: datetime | None = None


class ReconciliationSessionSummary(BaseModel):
    """Item da lista do histórico de conciliações (S7 BACK 4.2).

    O front resolve o nome da conta via cache do detalhe do cliente
    (Endpoint A) — aqui só vai o `omie_conta_id`. `error_message` aparece
    apenas em sessões com `status='error'`.
    """

    id: UUID
    omie_conta_id: int
    reference_month: date
    status: str
    created_at: datetime
    total_file_entries: int = Field(0, ge=0)
    conciliated_count: int = Field(0, ge=0)
    sem_omie_count: int = Field(0, ge=0)
    omie_sem_arquivo_count: int = Field(0, ge=0)
    anomaly_count: int = Field(0, ge=0)
    error_message: str | None = None

    model_config = {"from_attributes": True}


class ReconciliationSessionListResponse(BaseModel):
    """Body de GET /api/v1/clients/{id}/reconciliations — lista paginada."""

    data: list[ReconciliationSessionSummary]
    pagination: PaginationMeta
