"""Schemas Pydantic do módulo de clientes BPO (S6 + S7).

Princípios:
    - **Credenciais Omie NUNCA aparecem em response** — nem mascaradas, nem
      em qualquer outro formato (CLAUDE.md §3.2).
    - Request usa validadores estritos; Response usa tipos básicos para não
      derrubar listagens com registros legados (memória `feedback_pydantic`).
    - Update é PATCH (parcial): apenas campos enviados são alterados.
    - `responsible_manager` opcional — em teoria todo cliente tem assignment,
      mas a tela de listagem nunca deve quebrar se o registro estiver órfão.
    - Carteira compartilhada (86e390kz8): `responsible_manager` é UM (o que
      responde pelo cliente); `manager_count` diz quantos têm ACESSO; a lista
      completa sai em `GET /clients/{id}/managers` (`ClientManagerResponse`).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import ReconciliationStatus
from app.modules.reconciliations.schemas import SessionAuthor
from app.modules.users.schemas import PaginationMeta


class CreateClientRequest(BaseModel):
    """Body de POST /api/v1/clients — cria cliente + auto-assign do criador.

    O backend confia que o frontend já chamou `/test-connection` antes (Doc §9.2);
    aqui apenas criptografa e persiste.
    """

    name: str = Field(..., min_length=1, max_length=200, description="Nome interno na Hologram.")
    omie_app_key: str = Field(..., min_length=1, max_length=200)
    omie_app_secret: str = Field(..., min_length=1, max_length=200)
    category_id: UUID | None = Field(
        None, description="Categoria do catálogo (86e34jd8m). Ausente ou null = sem categoria."
    )


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
    # Tri-estado (86e34jd8m): OMITIDO mantém; `null` explícito limpa; UUID troca.
    # A rota distingue omitido de null via `model_fields_set`.
    category_id: UUID | None = Field(
        None, description="Omitir mantém a categoria; `null` limpa; UUID troca."
    )


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


class ManagerTargetRequest(BaseModel):
    """Alvo de uma ação de carteira (86e390kz8): um gerente do SISTEMA, ativo.

    Base das duas ações que recebem um gerente no body; validação nova do alvo
    entra aqui uma vez e vale para as duas.
    """

    user_id: UUID = Field(..., description="ID do gerente (manager ativo).")


class AssignClientRequest(ManagerTargetRequest):
    """Body de PATCH /api/v1/clients/{id}/assign — define o RESPONSÁVEL.

    Não remove o acesso de ninguém: quem era responsável continua vendo o
    cliente como colaborador. Se o alvo ainda não tinha acesso, passa a ter.
    """


class AddClientManagerRequest(ManagerTargetRequest):
    """Body de POST /api/v1/clients/{id}/managers — concede ACESSO a um gerente."""


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


class ClientManagerListResponse(BaseModel):
    """Body de GET/POST/DELETE em /api/v1/clients/{id}/managers — a lista inteira."""

    data: list[ClientManagerResponse]


class ClientManagerResponse(ManagerSummary):
    """Uma pessoa com acesso ao cliente (86e390kz8).

    A identidade é a de `ManagerSummary` — herdada, não redeclarada, para mudar
    num lugar só. Nunca a linha de `users` (§3.2): o `id` entra porque é o alvo
    do `DELETE .../managers/{user_id}`; `active` porque um responsável DESATIVADO
    precisa ficar visível para o admin passar o bastão (o servidor recusa
    promover inativo e recusa remover o responsável — sem o flag o admin não
    veria o beco). `is_responsible` marca o único responsável; `assigned_at` é
    quando o acesso foi concedido.
    """

    active: bool
    is_responsible: bool
    assigned_at: datetime


class ClientCategorySummary(BaseModel):
    """Categoria do cliente como aparece na lista e no detalhe (86e34jd8m)."""

    id: UUID
    name: str
    tone: str


class ClientResponse(BaseModel):
    """Representação pública de um Client. NUNCA inclui campos `*_encrypted`/`*_iv`."""

    id: UUID
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime
    responsible_manager: ManagerSummary | None = None
    reconciliation_count: int = Field(0, ge=0)
    # 86e34jd5a — favorito é POR USUÁRIO: reflete quem pede, nunca o cliente em
    # si. `False` quando o viewer é desconhecido (response sem usuário no contexto).
    is_favorite: bool = Field(
        False,
        description="Cliente favoritado pelo usuário autenticado (preferência por usuário).",
    )
    # 86e34jd8m — categoria (nicho/segmento) do catálogo; `null` = sem categoria.
    category: ClientCategorySummary | None = None
    # 86e36pm1z — cliente ENCERRADO (terminal): histórico só-leitura, escrita 409.
    closed_at: datetime | None = None
    # 86e390kz8 — quantas pessoas têm ACESSO (responsável incluído). A lista
    # mostra "Fulana +N" sem carregar os nomes de todo mundo em cada linha.
    manager_count: int = Field(
        0, ge=0, description="Pessoas com acesso ao cliente, responsável incluído."
    )

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

    `account_type` é o código de 2 letras devolvido pela Omie em
    `tipo_conta_corrente` — `'CC'` (Conta Corrente), `'CR'` (Cartão de
    Crédito), `'CA'` (Conta Aplicação), entre outros. Mantemos `str` em
    vez de enum no response (memória `feedback_pydantic`): se o Omie
    introduzir um novo tipo, a tela continua funcionando mesmo antes do
    backend reconhecê-lo formalmente.
    """

    id: UUID
    omie_conta_id: int = Field(..., description="nCodCC do Omie (BigInteger).")
    name: str
    bank_name: str
    account_type: str = Field(
        ...,
        description="Código Omie: 'CC' (corrente), 'CR' (cartão), 'CA' (aplicação), etc.",
    )
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


#: Vocabulário de status do PRODUTO → status do banco (Sprint 4, R1/§17).
#: "Processada" cobre `reviewing` (aguardando revisão) e `done` (revisada): do
#: ponto de vista de quem opera a lista, ambas terminaram de processar. O
#: filtro da UI trabalha com as chaves; o banco, com os valores.
UI_STATUS_TO_DB: dict[str, list[str]] = {
    "processing": [ReconciliationStatus.PROCESSING.value],
    "processed": [ReconciliationStatus.REVIEWING.value, ReconciliationStatus.DONE.value],
    "error": [ReconciliationStatus.ERROR.value],
}


class ReconciliationSessionSummary(BaseModel):
    """Item da lista de conciliações do cliente (S7 BACK 4.2 + BACK 04.3).

    O front resolve o nome da conta via cache do detalhe do cliente
    (Endpoint A) — aqui só vai o `omie_conta_id`. `error_message` aparece
    apenas em sessões com `status='error'`.

    Os contadores vêm das COLUNAS da sessão, materializadas pela fonte única
    (`reconciliations.totals`) — a lista não recalcula nada e por isso não
    diverge do detalhe.
    """

    id: UUID
    omie_conta_id: int
    # Tipo normalizado da conta (FASE 1): 'checking' ou 'credit_card'. O card
    # do histórico ramifica nisso pra mostrar o badge "Cartão de Crédito".
    # `str` lenient (memória `feedback_pydantic_strict_input_lenient_output`).
    account_type: str
    reference_month: date
    status: str
    created_at: datetime
    total_file_entries: int = Field(0, ge=0)
    conciliated_count: int = Field(0, ge=0)
    sem_omie_count: int = Field(0, ge=0)
    omie_sem_arquivo_count: int = Field(0, ge=0)
    anomaly_count: int = Field(0, ge=0)
    error_message: str | None = None
    # BACK 04.4 — código canônico do erro; o card mostra "(cód. X)".
    error_code: str | None = None
    # BACK 04.2/04.3 — nº de partes (arquivos) da conciliação. O card da lista
    # mostra "3 arquivos"; vem de subquery na própria query da listagem.
    total_files: int = Field(0, ge=0)
    # 86e2n39f1 — quem CRIOU a conciliação, já mascarado por escopo do
    # observador no service. Preenchido SÓ pelo mapper (`model_copy`): o
    # `validation_alias` impossível impede o `model_validate(from_attributes)`
    # de ler o atributo ORM homônimo `created_by` — que é o UUID cru da FK e
    # quebraria a validação (e o relationship `user` inteiro JAMAIS pode
    # serializar, §3.2). Na serialização o campo sai como `created_by` normal.
    created_by: SessionAuthor | None = Field(
        default=None, validation_alias="never_populated_by_orm"
    )

    model_config = {"from_attributes": True}


class ReconciliationSessionListResponse(BaseModel):
    """Body de GET /api/v1/clients/{id}/reconciliations — lista paginada."""

    data: list[ReconciliationSessionSummary]
    pagination: PaginationMeta
