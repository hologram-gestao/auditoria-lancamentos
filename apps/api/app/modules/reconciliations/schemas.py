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
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.exceptions import ErrorCode
from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction

#: Códigos aceitos em `ReconciliationFileInput.error_code`. Fechado por
#: construção: é o mesmo enum que o handler global usa nas respostas de erro.
_VALID_ERROR_CODES: frozenset[str] = frozenset(code.value for code in ErrorCode)

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
    truncamento que o teto de tokens deixar passar) ou um valor foi adulterado,
    a identidade não fecha e `ok=False`, com `reason` em PT-BR para o front
    BLOQUEAR a confirmação da prévia e exibir o motivo.

    Identidades (tolerância R$ 0,01, aritmética Decimal — CLAUDE.md §4.4/§5.1):
        - Conta corrente: `saldo_inicial + Σ(movimentações) == saldo_final`.
        - Cartão: `Σ(movimentações exceto is_payment, invertendo o sinal de
          débito) == total_da_fatura` (o saldo final declarado). Ver ⚠️ S-1.
        - Conta aplicação (`investment`): **não se aplica** (`applicable=False`)
          — ver abaixo.

    ⚠️ **S-1 (ASSUMIDA — NÃO TESTADA / RISCO):** para cartão, assume-se que o
    pagamento da fatura anterior NÃO entra no checksum e que o total da fatura
    é o `closing_balance` declarado. É semântica contábil do BPO (a confirmar
    com o Galhardo), não decisão nossa.

    **`investment` não é verificável por identidade de saldo.** O prompt manda
    NÃO emitir IOF, IR nem rendimento como transações (`prompts.py`, regra 14):
    eles entram no saldo sem virar movimentação. Logo `inicial + Σ != final`
    por construção, e aplicar a regra de conta corrente bloquearia conciliações
    VÁLIDAS. Por isso `applicable=False` e `ok=True` — os números seguem sendo
    calculados de verdade (informativos), mas não barram a prévia.
    """

    ok: bool
    # False quando a identidade não é verificável para o tipo de conta
    # (`investment`). Nesse caso `ok` é sempre True e o front não deve exibir
    # o checksum como veredito — não há o que afirmar.
    applicable: bool
    account_type: Literal["checking", "credit_card", "investment"]
    # Alvo declarado no documento (saldo final / total da fatura).
    expected: Decimal
    # Valor reconstruído a partir das transações extraídas.
    computed: Decimal
    # `expected - computed` (assinado). |difference| <= tolerance ⇒ ok.
    difference: Decimal
    tolerance: Decimal
    # Motivo acionável em PT-BR; preenchido só quando `ok=False`.
    reason: str | None = None


class ParseResponse(BaseModel):
    """Response de POST /api/v1/reconciliations/parse.

    Reusa o `ExtractedStatement` do módulo de integração — o shape exposto
    para o front é exatamente o que veio do tool use, sem renomeação.

    `checksum` (BACK 02.3) é o sinal de bloqueio da prévia: quando
    `checksum.ok=False`, o front bloqueia a confirmação e mostra `reason`.

    `file_hash` (BACK 04.2) é o SHA-256 do conteúdo **calculado pelo servidor**
    sobre os bytes efetivamente recebidos (S0/A10). O front devolve ESTE valor
    ao criar/anexar a conciliação, em vez de calcular um por conta própria —
    dedup e identidade de parte passam a se apoiar no mesmo número.
    """

    data: ExtractedStatement
    checksum: ChecksumResult
    file_hash: str


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
    account_type: Literal["checking", "credit_card", "investment"]
    period_start: _date
    period_end: _date
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[ExtractedTransaction] = Field(min_length=1)

    model_config = ConfigDict(strict=False)


#: Teto de partes por request. Uma fatura quebrada em mais de 20 PDFs não é um
#: caso de uso — é um upload acidental de pasta inteira. Barrar aqui protege o
#: guardrail do PRD ("o tempo de conciliação não pode subir com múltiplos
#: arquivos") antes de qualquer trabalho ser feito.
MAX_FILES_PER_REQUEST = 20


class ReconciliationFileInput(BaseModel):
    """Uma **parte** (arquivo) de uma conciliação — BACK 04.2.

    Duas formas mutuamente exclusivas, e exatamente uma tem de vir:

    - `statement` preenchido → a parte foi extraída com sucesso em `/parse` e
      suas linhas entram na sessão (`status='parsed'`);
    - `error_code` preenchido → a extração daquela parte FALHOU. A parte é
      registrada mesmo assim (`status='error'`, sem linhas) para que a tela
      diga **qual** arquivo falhou e ofereça removê-lo. Sem isso, um upload de
      3 PDFs em que o 2º falha vira uma conciliação silenciosamente incompleta.

    `error_code` é validado contra o enum canônico `ErrorCode` — código, nunca
    mensagem: texto livre aqui seria porta de PII e de linguagem interna
    vazando para o usuário (S2/R9).
    """

    model_config = ConfigDict(extra="forbid")

    file_hash: str = Field(description="SHA-256 hex (64 chars) devolvido por /parse.")
    filename: str | None = Field(
        default=None,
        max_length=255,
        description="Nome do arquivo. Persistido CIFRADO (pode conter razão social).",
    )
    statement: ReconciliationStatementInput | None = None
    error_code: str | None = Field(
        default=None,
        description="Código canônico do erro quando a extração desta parte falhou.",
    )

    @field_validator("file_hash", mode="after")
    @classmethod
    def _normalize_hash(cls, v: str) -> str:
        if not _HASH_PATTERN.match(v):
            raise ValueError("file_hash precisa ser SHA-256 em hexadecimal (64 caracteres).")
        return v.lower()

    @field_validator("error_code", mode="after")
    @classmethod
    def _known_error_code(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_ERROR_CODES:
            raise ValueError("error_code precisa ser um código canônico da API.")
        return v

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> ReconciliationFileInput:
        if (self.statement is None) == (self.error_code is None):
            raise ValueError(
                "Cada arquivo precisa de `statement` (extração ok) OU `error_code` "
                "(extração falhou) — nunca os dois, nunca nenhum."
            )
        return self

    @property
    def parsed_ok(self) -> bool:
        return self.statement is not None


class CreateReconciliationRequest(BaseModel):
    """Body do POST /api/v1/reconciliations.

    O front envia o ParsedStatement (output do S9) + a meta da conciliação
    (qual cliente, qual conta Omie, mês de referência, hash do arquivo).
    Nada do arquivo original — segue CLAUDE.md §3.10 (arquivo nunca persiste).

    **BACK 04.2 — N arquivos.** Uma conciliação é *uma conta + um mês* com N
    partes consolidadas num só resumo, então o campo canônico é `files`. A
    forma antiga (`file_hash` + `statement` soltos, 1 arquivo) continua aceita
    e é normalizada para uma lista de um item — é **legada**: existe só para o
    front atual não quebrar enquanto migra para a gaveta multi-upload. Mandar
    as duas formas na mesma request é erro.

    FASE 1: a tolerância de data deixou de ser parametrizável — é fixa no
    backend (`matcher.DATE_DIVERGENCE_RANGE`). `date_tolerance_days` não é
    mais aceito; se o front enviar, o Pydantic ignora (extra="ignore", default).
    """

    client_id: UUID
    omie_conta_id: int = Field(ge=1, description="nCodCC do Omie.")
    reference_month: _date = Field(
        description=(
            "1º dia do mês de referência. O front pode mandar 'YYYY-MM-01' "
            "ou um Date completo — o validator normaliza pra dia 1."
        ),
    )
    files: list[ReconciliationFileInput] = Field(
        default_factory=list,
        max_length=MAX_FILES_PER_REQUEST,
        description="Partes da conciliação. Forma canônica (BACK 04.2).",
    )
    # --- Forma LEGADA (1 arquivo). Não usar em código novo. ---
    file_hash: str | None = Field(default=None, description="LEGADO: use `files`.")
    statement: ReconciliationStatementInput | None = Field(
        default=None, description="LEGADO: use `files`."
    )

    @field_validator("reference_month", mode="after")
    @classmethod
    def _normalize_to_first_day(cls, v: _date) -> _date:
        # `reference_month` é Date no DB mas semanticamente é "mês". Normaliza
        # qualquer data → dia 1, evitando duplicatas por divergência de dia.
        return v.replace(day=1)

    @model_validator(mode="after")
    def _normalize_files(self) -> CreateReconciliationRequest:
        legacy = self.file_hash is not None or self.statement is not None
        if self.files and legacy:
            raise ValueError(
                "Envie `files` OU o par legado (`file_hash` + `statement`), não os dois."
            )
        if not self.files:
            if self.file_hash is None or self.statement is None:
                raise ValueError(
                    "Informe `files` com ao menos um arquivo (ou o par legado "
                    "`file_hash` + `statement`)."
                )
            self.files = [
                ReconciliationFileInput(file_hash=self.file_hash, statement=self.statement)
            ]
        _reject_duplicate_hashes(self.files)
        if not any(f.parsed_ok for f in self.files):
            # Sem nenhuma parte extraída não há uma única linha para conciliar —
            # a sessão nasceria vazia e o processamento quebraria ao calcular o
            # período (min/max sobre lista vazia).
            raise ValueError(
                "Ao menos um arquivo precisa ter sido extraído com sucesso "
                "para criar a conciliação."
            )
        return self


class AttachFilesRequest(BaseModel):
    """Body do POST /api/v1/reconciliations/{id}/files — cenário S-3.

    "Criei a conciliação com a parte 1 e a parte 2 chegou no dia seguinte."
    Sem este caminho, a nova unicidade (uma conciliação por conta+mês) daria
    409 e o usuário ficaria sem saída.
    """

    model_config = ConfigDict(extra="forbid")

    files: list[ReconciliationFileInput] = Field(min_length=1, max_length=MAX_FILES_PER_REQUEST)

    @model_validator(mode="after")
    def _no_duplicates(self) -> AttachFilesRequest:
        _reject_duplicate_hashes(self.files)
        return self


def _reject_duplicate_hashes(files: list[ReconciliationFileInput]) -> None:
    """Mesma parte duas vezes NA MESMA request — barra antes de tocar o banco.

    A UNIQUE `(session_id, file_hash)` pegaria depois, mas como 409 genérico de
    integridade; aqui vira 422 com a mensagem certa e sem escrever nada.
    """
    hashes = [f.file_hash for f in files]
    if len(set(hashes)) != len(hashes):
        raise ValueError("Há arquivos repetidos (mesmo conteúdo) na mesma requisição.")


class CreateReconciliationPayload(BaseModel):
    """Conteúdo do envelope da criação."""

    session_id: UUID
    status: Literal["processing"]
    # Nº de partes registradas na sessão (BACK 04.2) — o resumo indica quantos
    # arquivos compõem a conciliação.
    total_files: int = 0


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
    # Tipo normalizado da conta (FASE 1): 'checking' ou 'credit_card'. A Tela
    # de Revisão ramifica nisso (badge/título/labels de cartão). `str` lenient.
    account_type: str
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
    # BACK 04.4 — CÓDIGO canônico do desfecho de erro. É o que a tela mostra
    # ("cód. X") e o que o usuário reporta ao suporte; a mensagem interna
    # nunca aparece (S2/R9). NULL fora do estado de erro e em sessões antigas.
    error_code: str | None = None
    # Saldos agregados da sessão. Calculados pós-matching em
    # `processing/balances.py` (commit cad9dbb). NULL em sessões legadas
    # processadas antes do backfill; front mostra "Indisponível" nessas.
    balance_start: Decimal | None = None
    balance_end_file: Decimal | None = None
    balance_end_omie: Decimal | None = None
    balance_difference: Decimal | None = None
    # 86e2u513f — somas da aba Resumo, computadas no BACKEND sobre a sessão
    # INTEIRA (antes o front somava as 50 primeiras linhas em float). Decimal
    # serializa como string no JSON — §3.4. `card_charges_total` só existe em
    # sessão de cartão (None fora dela); nas demais, sempre populados.
    credits_total: Decimal = Decimal("0")
    debits_total: Decimal = Decimal("0")
    card_charges_total: Decimal | None = None
    # Breakdown de anomalias da sessão inteira (mesma fonte do anomaly_count).
    anomalies_critical: int = 0
    anomalies_moderate: int = 0
    anomalies_info: int = 0
    anomalies_resolved: int = 0
    # BACK 04.2 — nº de partes (arquivos) consolidadas nesta conciliação.
    # Default 0 (e não REQUIRED) por higiene Pydantic v2, mas o service sempre
    # popula: sessões migradas têm 1 parte, criadas na Sprint 4 têm N.
    total_files: int = 0
    # BACK 06.4/06.5 — a qualificação desta sessão considerou o GLOSSÁRIO do
    # cliente? Vem da coluna escrita por `qualify_session` a partir do bloco
    # realmente injetado no prompt (não é hard-coded, nem recalculado aqui).
    # `false` em sessão antiga e em cliente sem glossário — a tela de revisão
    # simplesmente não mostra o selo, sem regressão.
    qualification_used_glossary: bool = False


class SessionDetailResponse(BaseModel):
    """Response do GET /api/v1/reconciliations/{id}."""

    data: SessionDetailPayload


# ----------------------------------------------------------------------
# BACK 04.2 — GET /reconciliations/{id}/files
# ----------------------------------------------------------------------


class SessionFileItem(BaseModel):
    """Uma parte da conciliação, como a UI precisa vê-la.

    `filename` já vem DECIFRADO (o nome é cifrado em repouso). `None` nas
    partes migradas da Sprint 3, que não têm nome guardado em lugar nenhum —
    a UI mostra "Arquivo N" nesses casos, não uma célula vazia.
    """

    file_id: UUID
    filename: str | None = None
    # 'parsed' (linhas carregadas) | 'error' (extração falhou). `str` lenient.
    status: str
    # Código canônico quando `status='error'` — a tela mostra o código, nunca
    # a linguagem interna do erro (S2/R9).
    error_code: str | None = None
    entry_count: int
    created_at: datetime


class SessionFilesPayload(BaseModel):
    """Conteúdo do envelope de GET /reconciliations/{id}/files."""

    session_id: UUID
    total_files: int
    files: list[SessionFileItem]


class SessionFilesResponse(BaseModel):
    """Response do GET /api/v1/reconciliations/{id}/files."""

    data: SessionFilesPayload


class AttachFilesPayload(BaseModel):
    """Conteúdo do envelope de anexar/remover parte.

    `reprocessing=True` avisa o front que o cruzamento Omie foi re-agendado (a
    sessão voltou para `processing`) — é o sinal para reativar o polling da
    lista/detalhe. `False` quando a operação não mudou o conjunto de linhas
    (ex.: anexar só o registro de uma parte que falhou na extração).
    """

    session_id: UUID
    total_files: int
    reprocessing: bool


class AttachFilesResponse(BaseModel):
    """Response de POST/DELETE em /api/v1/reconciliations/{id}/files."""

    data: AttachFilesPayload
