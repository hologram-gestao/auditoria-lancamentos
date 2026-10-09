"""Prévia dos alvos do demonstrativo a partir da ORIGEM do cliente (86e3n70pn, bloco B).

O problema que isto resolve: organização nova nasce com os cinco destinos e ZERO
alvos (`seed_default_destinations`). A herança do `demonstrativo_contabil` casa o
`dre_code` de cada categoria do plano de contas sincronizado (S10) com o código de
um alvo do catálogo; sem alvo nenhum, "Iniciar de-para" herda zero e o escritório
não entende por quê (reunião com o Murilo, 08/10/2026).

**O que esta prévia faz:** lê os `dre_code` DISTINTOS das categorias ATIVAS do plano
sincronizado do cliente, resolve o nome de cada conta de demonstrativo pelo MESMO
acessor da tela do plano de contas (`ChartOfAccountsSyncService.resolve_names`, o
mapa `dre`, que é SEPARADO do de categorias porque os dois códigos colidem) e diz,
para cada uma, se o catálogo do destino já tem o alvo.

**O que ela NÃO faz:** gravar. É uma leitura; quem cria os alvos é o lote que já
existe (`POST /mapping-destinations/{id}/targets`), chamado pela tela depois que a
pessoa CONFIRMA. É dado do Omie virando configuração da ORGANIZAÇÃO, e isso só
acontece por decisão explícita de quem tem `manage_mapping_catalog`, nunca
automaticamente: o catálogo é de todos os clientes da organização, e um cliente não
pode reescrevê-lo sozinho (§4.9, ADR-074-BE).

**§4.5:** o nome da conta de demonstrativo só passa pela memória até a resposta; o
que persiste é o que a pessoa confirmar no catálogo da organização, que é
configuração do escritório e fica em claro (como todo `mapping_targets.name`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.db.models import INHERITING_DESTINATION_TYPE
from app.db.models.mapping_catalog import MAX_TARGET_CODE_CHARS, MAX_TARGET_NAME_CHARS
from app.modules.client_mapping.service import (
    INHERIT_STATE_NO_CHART,
    INHERIT_STATE_NOT_INHERITING,
    INHERIT_STATE_OK,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.db.models import Client
    from app.modules.client_chart_of_accounts.service import ChartOfAccountsSyncService
    from app.modules.client_mapping.repository import ClientMappingRepository
    from app.modules.client_mapping.service import ClientMappingDecisionService
    from app.modules.mapping_catalog.repository import MappingCatalogRepository


@dataclass(frozen=True, slots=True)
class OriginTargetCandidate:
    """Uma conta de demonstrativo declarada pela origem do cliente."""

    code: str
    #: `None` = a origem não respondeu (ou não traz nome para o código).
    name: str | None
    #: Quantas categorias ativas do cliente apontam para esta conta.
    categories: int
    #: O catálogo do destino já tem um alvo com este código (ativo ou não).
    exists: bool
    #: Situação do alvo existente; `None` quando não existe.
    active: bool | None
    #: Pode entrar no lote: não existe, tem nome e cabe nas colunas do catálogo.
    creatable: bool


@dataclass(frozen=True, slots=True)
class OriginTargetsPreview:
    state: str
    destination_id: UUID | None = None
    candidates: list[OriginTargetCandidate] = field(default_factory=list)
    #: `False` quando há conta sem nome resolvido (origem fora do ar, fail-soft).
    names_resolved: bool = True


def _fits_catalog(code: str, name: str) -> bool:
    """O mesmo limite do `MappingTargetCreate`: código sem espaço, colunas de tamanho fixo.

    Código longo é RECUSADO, nunca truncado (§7 Backend): a conta fica fora do lote e a
    tela diz por quê, em vez de o lote inteiro voltar 400.
    """
    return (
        0 < len(code) <= MAX_TARGET_CODE_CHARS
        and not any(ch.isspace() for ch in code)
        and 0 < len(name) <= MAX_TARGET_NAME_CHARS
    )


class OriginTargetsPreviewService:
    """Monta a prévia. Não decide permissão nem tenant: isso é da rota."""

    def __init__(
        self,
        *,
        decisions: ClientMappingDecisionService,
        repository: ClientMappingRepository,
        catalog: MappingCatalogRepository,
        names: ChartOfAccountsSyncService,
    ) -> None:
        self._decisions = decisions
        self._repo = repository
        self._catalog = catalog
        self._names = names

    async def preview(self, client: Client, destination_type: str) -> OriginTargetsPreview:
        # O destino da organização DO CLIENTE (409 nomeando o tipo se não existir).
        destination = await self._decisions.resolve_destination(client, destination_type)
        if destination.destination_type != INHERITING_DESTINATION_TYPE:
            # Só o demonstrativo herda da origem (R7): nos outros não há o que propor.
            return OriginTargetsPreview(
                state=INHERIT_STATE_NOT_INHERITING, destination_id=destination.id
            )

        chart = await self._repo.active_chart(client.id)
        if not chart:
            return OriginTargetsPreview(state=INHERIT_STATE_NO_CHART, destination_id=destination.id)

        per_code: dict[str, int] = {}
        for row in chart:
            if row.dre_code:
                per_code[row.dre_code] = per_code.get(row.dre_code, 0) + 1
        if not per_code:
            return OriginTargetsPreview(state=INHERIT_STATE_OK, destination_id=destination.id)

        existing = await self._catalog.get_targets_by_codes(destination.id, per_code)
        # Só vai à origem quando há conta sem alvo: o nome só importa para criar.
        missing_any = any(code not in existing for code in per_code)
        dre_names = (await self._names.resolve_names(client)).dre if missing_any else {}

        candidates: list[OriginTargetCandidate] = []
        names_resolved = True
        for code in sorted(per_code):
            target = existing.get(code)
            if target is not None:
                candidates.append(
                    OriginTargetCandidate(
                        code=code,
                        name=target.name,
                        categories=per_code[code],
                        exists=True,
                        active=target.active,
                        creatable=False,
                    )
                )
                continue
            raw = dre_names.get(code)
            name = " ".join(raw.split()) if raw else None
            if name is None:
                names_resolved = False
            candidates.append(
                OriginTargetCandidate(
                    code=code,
                    name=name,
                    categories=per_code[code],
                    exists=False,
                    active=None,
                    creatable=name is not None and _fits_catalog(code, name),
                )
            )
        return OriginTargetsPreview(
            state=INHERIT_STATE_OK,
            destination_id=destination.id,
            candidates=candidates,
            names_resolved=names_resolved,
        )
