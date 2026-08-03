"""DTOs do glossário por tenant (Sprint 6, BACK 06.2).

Aqui vivem apenas os DTOs **em claro** do snapshot de leitura — o que sai do
`service.load_glossary_snapshot` e entra no renderizador do bloco de prompt
(BACK 06.4). Os schemas de request/response HTTP são da BACK 06.3.

São `dataclass` (não Pydantic) de propósito: não atravessam a borda HTTP, são
consumidos por código de servidor, e carregam **plaintext do cliente** — quanto
menos maquinaria de serialização automática em volta, menor a chance de um
`model_dump()` distraído mandar isso para um log.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import (
    MAX_CODE_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    GlossaryEntryKind,
)
from app.modules.users.schemas import PaginationMeta

#: Placeholder de campo que não decifrou. Mesmo texto da tela de revisão e do
#: export (CLAUDE.md §4.1): nunca célula silenciosamente vazia.
UNDECIPHERABLE = "[indecifrável]"


@dataclass(frozen=True, slots=True)
class GlossaryEntryPlain:
    """Uma entrada do glossário, decifrada.

    `decrypt_failed=True` marca a entrada cujo texto não decifrou: a tela mostra
    `[indecifrável]`, e a BACK 06.4 a **omite** do bloco de prompt — injetar
    `[indecifrável]` como se fosse vocabulário do cliente seria pior que omitir.
    """

    id: UUID
    kind: GlossaryEntryKind
    code: str | None
    name: str
    description: str | None
    decrypt_failed: bool


@dataclass(frozen=True, slots=True)
class GlossarySnapshot:
    """Glossário completo de UM tenant, num instante.

    `version` é o `clients.glossary_version` lido na MESMA transação das
    entradas: é o que a BACK 06.4 usa para saber que o bloco cacheado mudou.
    `entries` vem em ordem DETERMINÍSTICA (`kind`, `created_at`, `id`) — sem
    isso o prefixo do prompt mudaria entre chamadas e nunca cachearia.
    """

    client_id: UUID
    version: int
    entries: tuple[GlossaryEntryPlain, ...]

    @property
    def is_empty(self) -> bool:
        """Cliente sem glossário — a qualificação segue exatamente como hoje."""
        return not self.entries


# ----------------------------------------------------------------------
# Contrato HTTP (BACK 06.3)
#
# Envelope da §7 do CLAUDE.md: sucesso `{data: ...}` / `{data: [...],
# pagination: {...}}`; erro `{error: {code, message, userMessage}}` (handler
# global). Os limites de tamanho vêm de `app.db.models` — uma cópia só, a mesma
# que a BACK 06.4 assume como teto do bloco de prompt.
# ----------------------------------------------------------------------


class GlossaryEntryResponse(BaseModel):
    """Uma entrada do glossário, já decifrada, como a API devolve.

    `decryptFailed=true` significa que o texto não decifrou e o campo veio como
    `[indecifrável]` — a tela mostra o estado em vez de uma célula vazia.
    """

    id: UUID
    kind: GlossaryEntryKind
    code: str | None = None
    name: str
    description: str | None = None
    decrypt_failed: bool = Field(default=False, alias="decryptFailed")

    model_config = ConfigDict(populate_by_name=True)


class GlossaryListPayload(BaseModel):
    """Conteúdo do envelope da listagem — inclui a VERSÃO do glossário.

    A versão viaja na listagem de propósito: é o mesmo número que invalida o
    bloco de prompt (BACK 06.4), então o front consegue mostrar/alinhar estado
    sem inventar um segundo contador.
    """

    entries: list[GlossaryEntryResponse]
    version: int = Field(ge=0)


class GlossaryListResponse(BaseModel):
    """Body de GET /api/v1/clients/{client_id}/glossary."""

    data: GlossaryListPayload
    pagination: PaginationMeta


class GlossaryEntryEnvelope(BaseModel):
    """Body de POST/PATCH — `{data: <entrada>}` (§7)."""

    data: GlossaryEntryResponse


class GlossaryDeletedPayload(BaseModel):
    """Conteúdo do envelope do DELETE: o que sobrou depois da remoção."""

    id: UUID
    deleted: bool
    version: int = Field(ge=0)


class GlossaryDeletedResponse(BaseModel):
    """Body de DELETE /api/v1/clients/{client_id}/glossary/{entry_id}."""

    data: GlossaryDeletedPayload


class _GlossaryEntryWrite(BaseModel):
    """Campos comuns de criação e edição, validados NO SERVIDOR.

    Os tetos não são cosméticos: são eles que impedem o bloco de system do
    glossário (BACK 06.4) de crescer sem limite e estourar o teto de tokens
    (guardrail S-2/R9). Vêm de `app.db.models` — não são redigitados aqui.

    `extra="forbid"`: campo desconhecido é erro, não é ignorado em silêncio
    (`client_id` no body, por exemplo, nunca decide tenant — quem decide é a
    rota).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: GlossaryEntryKind
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    code: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """`name` é obrigatório de verdade: só-espaços é vazio, não conteúdo.

        `min_length=1` sozinho aceitaria `"   "` — e uma entrada em branco
        entraria no bloco de prompt como ruído.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("O nome não pode ser vazio.")
        return stripped

    @field_validator("code", "description")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        """Normaliza antes de cifrar: sem espaço nas pontas, vazio vira `None`.

        Feito aqui e não no service porque o texto é CIFRADO na sequência —
        depois de cifrado não dá para normalizar sem decifrar de novo, e duas
        grafias do mesmo termo virariam duas entradas distintas no prompt.
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CreateGlossaryEntryRequest(_GlossaryEntryWrite):
    """Body de POST /api/v1/clients/{client_id}/glossary."""


class UpdateGlossaryEntryRequest(_GlossaryEntryWrite):
    """Body de PATCH /api/v1/clients/{client_id}/glossary/{entry_id}.

    Substituição COMPLETA dos campos textuais (não patch parcial): o texto é
    cifrado, então "mudar só a descrição" exigiria decifrar o resto para
    recompor — e a gaveta do front já envia o formulário inteiro.
    """
