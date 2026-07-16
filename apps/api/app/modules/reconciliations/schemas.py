"""Schemas Pydantic do módulo de conciliações.

S8 (BACK 6.2): response do check-duplicate.
S9 (BACK 7.1): response do parsing IA.
S10 (BACK 8.1 + 8.6): payload de criação de sessão e response do polling.

Convenção de envelope (CLAUDE.md §7): respostas de sucesso vão dentro de
`{"data": {...}}`.

Memória `feedback_pydantic_strict_input_lenient_output`: requests usam
validação rígida (UUID, regex, ge/le); responses usam tipos simples (str)
para evitar derrubar listagens com registros legados.
"""

from __future__ import annotations

import re
from datetime import date as _date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction

# ----------------------------------------------------------------------
# S8 — check-duplicate
# ----------------------------------------------------------------------


class DuplicateCheckPayload(BaseModel):
    """Conteúdo do envelope `{data: ...}` do check-duplicate."""

    duplicate: bool


class CheckDuplicateResponse(BaseModel):
    """Response de GET /api/v1/reconciliations/check-duplicate."""

    data: DuplicateCheckPayload


# ----------------------------------------------------------------------
# S9 — parse
# ----------------------------------------------------------------------


class ChecksumResult(BaseModel):
    """Resultado do checksum de saldos do parse (BACK 02.3).

    O checksum é a defesa contra parse INCOMPLETO — se linhas sumiram (ex: o
    truncamento que o BACK 02.1 deixar passar) ou um valor foi adulterado, a
    identidade não fecha e `ok=False`, com `reason` em PT-BR para o front
    BLOQUEAR a confirmação da prévia e exibir o motivo.

    Identidades (tolerância R$ 0,01, aritmética Decimal):
        - Conta corrente: `saldo_inicial + Σ(movimentações) == saldo_final`.
        - Cartão: `Σ(movimentações exceto is_payment, invertendo o sinal de
          débito) == total_da_fatura` (o saldo final declarado). Ver ⚠️ S-1.

    ⚠️ **S-1 (ASSUMIDA — NÃO TESTADA / RISCO):** para cartão, assume-se que o
    pagamento da fatura anterior NÃO entra no checksum e que o total da fatura
    é o `closing_balance` declarado. É semântica contábil do BPO (a confirmar
    com o Galhardo), não decisão nossa. O QA não reprova por essa suposição.
    """

    ok: bool
    account_type: Literal["checking", "credit_card"]
    # Alvo declarado no documento (saldo final / total da fatura).
    expected: Decimal
    # Valor reconstruído a partir das transações extraídas.
    computed: Decimal
    # `expected - computed` (assinado). |difference| <= 0.01 ⇒ ok.
    difference: Decimal
    tolerance: Decimal
    # PT-BR quando `ok=False`; None quando fecha. Front exibe direto.
    reason: str | None = None


class ParseResponse(BaseModel):
    """Response de POST /api/v1/reconciliations/parse.

    `data` reusa o `ExtractedStatement` do módulo de integração — o shape
    exposto para o front é exatamente o que veio do tool use, sem renomeação
    (aditivo: consumidores existentes de `data` seguem funcionando).

    `checksum` (BACK 02.3) é o sinal de bloqueio da prévia: quando
    `checksum.ok=False`, o front bloqueia a confirmação e mostra
    `checksum.reason`. O statement continua no `data` para a prévia poder
    exibir os valores e a divergência.
    """

    data: ExtractedStatement
    checksum: ChecksumResult


# ----------------------------------------------------------------------
# S10 — POST /reconciliations
# ----------------------------------------------------------------------


_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


class ReconciliationStatementInput(BaseModel):
    """Statement vindo do parsing (S9), revalidado no servidor.

    Reusa `ExtractedStatement` mas garantindo que `transactions` não esteja
    vazio (o constraint já existe no schema da Anthropic, mas `min_length=1`
    aqui torna explícito o contrato do POST).
    """

    bank_name: str = Field(min_length=1, max_length=200)
    account_type: Literal["checking", "credit_card"]
    period_start: _date
    period_end: _date
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[ExtractedTransaction] = Field(min_length=1)

    model_config = ConfigDict(strict=False)


class CreateReconciliationRequest(BaseModel):
    """Body do POST /api/v1/reconciliations.

    O front envia o ParsedStatement (output do S9) + a meta da conciliação
    (qual cliente, qual conta Omie, mês de referência, hash do arquivo,
    tolerância). Nada do arquivo original — segue CLAUDE.md §3.10 (arquivo
    nunca persiste).
    """

    client_id: UUID
    omie_conta_id: int = Field(ge=1, description="nCodCC do Omie.")
    reference_month: _date = Field(
        description=(
            "1º dia do mês de referência. O front pode mandar 'YYYY-MM-01' "
            "ou um Date completo — o validator normaliza pra dia 1."
        ),
    )
    date_tolerance_days: Annotated[int, Field(ge=1, le=7)] = 3
    file_hash: str = Field(description="SHA-256 hex (64 chars).")
    statement: ReconciliationStatementInput

    @field_validator("file_hash", mode="after")
    @classmethod
    def _normalize_hash(cls, v: str) -> str:
        if not _HASH_PATTERN.match(v):
            raise ValueError("file_hash precisa ser SHA-256 em hexadecimal (64 caracteres).")
        return v.lower()

    @field_validator("reference_month", mode="after")
    @classmethod
    def _normalize_to_first_day(cls, v: _date) -> _date:
        # `reference_month` é Date no DB mas semanticamente é "mês". Normaliza
        # qualquer data → dia 1, evitando duplicatas por divergência de dia.
        return v.replace(day=1)


class CreateReconciliationPayload(BaseModel):
    """Conteúdo do envelope da criação."""

    session_id: UUID
    status: Literal["processing"]


class CreateReconciliationResponse(BaseModel):
    """Response do POST /api/v1/reconciliations (HTTP 201)."""

    data: CreateReconciliationPayload


# ----------------------------------------------------------------------
# S10 — GET /reconciliations/{id}/status
# ----------------------------------------------------------------------


class SessionStatusPayload(BaseModel):
    """Conteúdo do envelope do polling.

    Usa `str` para o status em vez de Literal para sobreviver a estados
    legados (memória `feedback_pydantic_strict_input_lenient_output`).
    """

    session_id: UUID
    status: str
    conciliated_count: int
    sem_omie_count: int
    omie_sem_arquivo_count: int
    anomaly_count: int
    error_message: str | None = None


class SessionStatusResponse(BaseModel):
    """Response do GET /api/v1/reconciliations/{id}/status."""

    data: SessionStatusPayload


# ----------------------------------------------------------------------
# S11 — GET /reconciliations/{id}  (sem /status)
# ----------------------------------------------------------------------


class SessionDetailPayload(BaseModel):
    """Conteúdo do envelope do GET /reconciliations/{id}.

    Substitui o scan `useReconciliationsList(pageSize:100) + .find()` do
    front da tela de revisão. Expõe só o que o header da tela precisa —
    `period_start/period_end` ficam internos ao back (review service usa
    no /available-omie-entries).

    Status `str` lenient (memória `feedback_pydantic_strict_input_lenient_output`).
    """

    session_id: UUID
    client_id: UUID
    omie_conta_id: int
    reference_month: _date
    status: str
    total_file_entries: int
    conciliated_count: int
    sem_omie_count: int
    omie_sem_arquivo_count: int
    anomaly_count: int
    # Populado pelo worker em `mark_session_error` quando `status='error'`.
    # Front usa pra renderizar a tela de erro com `error_message` legível
    # antes de oferecer o botão "Tentar novamente".
    error_message: str | None = None
    # Saldos agregados da sessão. Calculados pós-matching em
    # `processing/balances.py` (commit cad9dbb). NULL em sessões legadas
    # processadas antes do backfill; front mostra "Indisponível" nessas.
    balance_start: Decimal | None = None
    balance_end_file: Decimal | None = None
    balance_end_omie: Decimal | None = None
    balance_difference: Decimal | None = None


class SessionDetailResponse(BaseModel):
    """Response do GET /api/v1/reconciliations/{id}."""

    data: SessionDetailPayload
