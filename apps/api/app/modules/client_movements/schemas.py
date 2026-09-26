"""Contrato HTTP da base de movimentos (Sprint 12, BACK 12.2).

Só estado e contagens — a base em si não tem tela de listagem nesta sprint (fora
de escopo no PRD). Quem a consome é a prévia do de-para (BACK 12.6).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.modules.client_movements.competence import COMPETENCE_PATTERN, format_competence

if TYPE_CHECKING:
    from app.modules.client_movements.repository import MovementSyncState
    from app.modules.client_movements.service import MovementSyncResult


class MovementsSyncRequest(BaseModel):
    """Corpo de `POST /movements/sync`: a competência a trazer da origem.

    Formato inválido (`2026-13`, `06/2026`, ausente) é validação de FORMA: 400
    `VALIDATION_ERROR` genérico do handler global, nunca 422 (§4.8).
    """

    competence: str = Field(
        pattern=COMPETENCE_PATTERN,
        description="Competência a sincronizar, `YYYY-MM` (ex.: `2026-06`).",
    )

    model_config = ConfigDict(extra="forbid")


class MovementsSyncStateResponse(BaseModel):
    """O estado da base de UMA competência — os dois relógios e o "nunca".

    `neverSynced` é CAMPO, não zero (ADR-067-BE): competência sem movimento algum
    e competência que ninguém consultou são respostas diferentes, e a tela não pode
    decidir isso contando linhas.
    """

    competence: str = Field(description="A competência consultada, `YYYY-MM`.")
    never_synced: bool = Field(
        alias="neverSynced",
        description=(
            "`true` = esta competência NUNCA foi sincronizada com sucesso. A prévia "
            "do de-para recusa com 409 orientando a sincronizar."
        ),
    )
    synced_at: datetime | None = Field(
        default=None,
        alias="syncedAt",
        description="Última sincronização ÍNTEGRA da competência. `null` = nunca houve.",
    )
    sync_failed_at: datetime | None = Field(
        default=None,
        alias="syncFailedAt",
        description=(
            "Última tentativa que FALHOU, se a mais recente falhou. Com `syncedAt` "
            "preenchido, a base continua sendo a da última sincronização íntegra."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, competence: date, state: MovementSyncState) -> MovementsSyncStateResponse:
        return cls(
            competence=format_competence(competence),
            never_synced=state.nunca_sincronizada,
            synced_at=state.synced_at,
            sync_failed_at=state.sync_failed_at,
        )


class MovementsSyncStateEnvelope(BaseModel):
    """Envelope `{data: ...}` de `GET /movements/sync-state`."""

    data: MovementsSyncStateResponse


class MovementsSyncResponse(BaseModel):
    """O que uma sincronização fez, em contagens — e o estado resultante.

    Devolver o `state` junto evita que a tela dispare uma segunda requisição só
    para redesenhar "sincronizada em".
    """

    movimentos: int = Field(ge=0, description="Movimentos da competência na origem.")
    sem_categoria: int = Field(
        ge=0,
        alias="semCategoria",
        description="Deles, quantos vieram SEM código de categoria (subconjunto).",
    )
    contas: int = Field(ge=0, description="Contas lidas.")
    ausentes: int = Field(
        ge=0,
        description=(
            "Quantos estavam na base e saíram da origem nesta passada — marcados "
            "`ausente_na_origem`, nunca apagados."
        ),
    )
    state: MovementsSyncStateResponse

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(cls, result: MovementSyncResult, state: MovementSyncState) -> MovementsSyncResponse:
        return cls(
            movimentos=result.movimentos,
            sem_categoria=result.sem_categoria,
            contas=result.contas,
            ausentes=result.ausentes,
            state=MovementsSyncStateResponse.build(result.competence, state),
        )


class MovementsSyncEnvelope(BaseModel):
    """Envelope `{data: ...}` de `POST /movements/sync`."""

    data: MovementsSyncResponse
