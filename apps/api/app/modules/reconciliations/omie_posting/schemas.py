"""Contrato do lote de lançamento no Omie (Sprint 7 / BACK 07.4).

Entrada rígida, saída simples (memória `feedback_pydantic_strict_input_lenient_output`):
o request valida UUID, tamanho e teto de lote; a resposta usa tipos simples e
**códigos categóricos** — nunca texto livre — para que a UI possa ramificar sem
casar string em português.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.db.models import COD_INT_LANC_MAX_LENGTH

#: `cCodCateg` é `string20` na Omie (mesmo teto do `cCodIntLanc`).
COD_CATEGORIA_MAX_LENGTH = COD_INT_LANC_MAX_LENGTH

#: Desfecho de cada linha do lote. Fechado — a UI ramifica nestes três.
PostingLineStatus = Literal["lancada", "bloqueada", "erro"]

#: Motivos, **categóricos**. Existem para a UI decidir o que oferecer ao
#: operador (reclassificar? conferir no Omie? tentar de novo?) sem interpretar
#: a frase em português, que pode mudar.
PostingLineReason = Literal[
    # --- bloqueios (nada foi enviado ao Omie) ---
    "linha_inexistente",
    "nao_e_sem_omie",
    "linha_ignorada",
    "ja_lancada",
    "envio_anterior_sem_confirmacao",
    "chave_em_conflito",
    "lancamento_ja_vinculado",
    # --- erros (houve tentativa) ---
    "erro_omie",
    "omie_indisponivel",
    # --- sucesso por reconciliação (não houve novo POST) ---
    "reconciliada",
]


class OmiePostingLineRequest(BaseModel):
    """Uma linha da fatura a lançar, com a categoria escolhida pelo operador."""

    file_entry_id: UUID
    cod_categoria: str = Field(
        min_length=1,
        max_length=COD_CATEGORIA_MAX_LENGTH,
        description="`cCodCateg` do Omie. Sem ele o lançamento não existe (R2).",
    )

    @field_validator("cod_categoria", mode="after")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        """Categoria em branco é bloqueio, não lançamento vazio.

        Espaço em branco passaria pelo `min_length=1` e chegaria à Omie como
        categoria inexistente — um erro do fornecedor por uma causa nossa.
        """
        stripped = v.strip()
        if not stripped:
            raise ValueError("cod_categoria não pode ser vazio")
        return stripped


class OmiePostingBatchRequest(BaseModel):
    """Body do POST /api/v1/reconciliations/{session_id}/omie-postings.

    O teto de lote **não** está aqui: ele vive no `Settings`
    (`OMIE_POSTING_MAX_BATCH`) e é validado no service, para poder ser ajustado
    por ambiente sem deploy. `max_length` fixo no schema daria um 422 com um
    número que ninguém consegue mudar.
    """

    lines: list[OmiePostingLineRequest] = Field(
        min_length=1,
        description="Linhas selecionadas. Duplicatas do mesmo file_entry_id são recusadas.",
    )

    @field_validator("lines", mode="after")
    @classmethod
    def _no_duplicate_entries(cls, v: list[OmiePostingLineRequest]) -> list[OmiePostingLineRequest]:
        """A mesma linha duas vezes no lote seria pedir dois lançamentos dela.

        A dedup do banco impediria o segundo, mas o operador receberia um
        "bloqueada: já lançada" confuso para uma linha que ele mandou uma vez
        só. Recusar na entrada é mais honesto.
        """
        seen = {line.file_entry_id for line in v}
        if len(seen) != len(v):
            raise ValueError("file_entry_id repetido no lote")
        return v


class OmiePostingLineResult(BaseModel):
    """Desfecho de UMA linha. É o que a tela mostra ao lado dela."""

    file_entry_id: UUID
    status: PostingLineStatus
    reason: PostingLineReason | None = None
    #: Mensagem em PT-BR para o operador. Em `erro_omie` é a mensagem VERBATIM
    #: do provedor (é o que o torna acionável); nunca contém credencial nem
    #: stack, e nunca é logada (ADR-023-BE).
    message: str | None = None
    omie_lancamento_id: int | None = None


class OmiePostingBatchPayload(BaseModel):
    """Conteúdo do envelope `{data}` — resumo por linha + agregados."""

    lines: list[OmiePostingLineResult]
    lancadas: int
    bloqueadas: int
    com_erro: int


class OmiePostingBatchResponse(BaseModel):
    """Response do POST /reconciliations/{session_id}/omie-postings."""

    data: OmiePostingBatchPayload
