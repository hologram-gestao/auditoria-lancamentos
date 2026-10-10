"""Regras dos layouts de exportação (Sprint 13, BACK 13.2 — R1).

- **Por organização.** O observador lê e escreve só os da própria (a plataforma, todos).
  O layout NASCE na organização decidida pela LINHA do ator
  (`resolve_organization_for_creation`); layout por PK fora do alcance = 404.
- **Versionado e imutável.** Alterar = gravar a versão N+1 (sob `FOR UPDATE` do layout,
  com a UNIQUE como rede); nunca `UPDATE` de definição. A versão anterior segue
  consultável.
- **Validar ANTES de gravar.** A definição passa por `parse_definition` (422 nomeando o
  campo) antes de qualquer escrita — nada é gravado numa definição recusada.
- **Organização suspensa não recebe escrita** (409), como o catálogo do de-para.
- **Excluir só o que nunca gerou arquivo** (86e3nuuub). A geração aponta para a VERSÃO
  com RESTRICT e o download regenera por ela: layout usado é 409 `LAYOUT_EM_USO` com a
  contagem; a corrida cai na FK e vira o MESMO 409. Arquivar o usado é outra task.
- **Resposta depois do commit.** As escritas commitam no serviço antes de devolver: a
  tela que relê logo em seguida vê o que acabou de gravar (a correção geral 86e3fxqqa não
  é desta sprint; ADR-091-BE).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authz import (
    CurrentUser,
    resolve_organization_filter,
    resolve_organization_for_creation,
)
from app.core.exceptions import (
    ConflictError,
    ExportLayoutInUseError,
    ExportLayoutNameAlreadyExistsError,
    NotFoundError,
    OrganizationInactiveError,
)
from app.core.logging import get_logger
from app.db.models import ExportLayout, ExportLayoutVersion
from app.modules.export_layouts.definition import TEMPLATES, LayoutDefinition, parse_definition
from app.modules.export_layouts.schemas import (
    ExportLayoutDetail,
    ExportLayoutItem,
    ExportLayoutTemplateItem,
    ExportLayoutVersionItem,
)
from app.modules.reconciliations.service import author_for_viewer

if TYPE_CHECKING:
    from typing import Any

    from app.modules.export_layouts.repository import ExportLayoutRepository

log = get_logger(__name__)

_LAYOUT_NOT_FOUND = "Layout não encontrado."


class ExportLayoutService:
    def __init__(self, repository: ExportLayoutRepository) -> None:
        self._repo = repository

    # ------------------------------ LEITURA ---------------------------

    @staticmethod
    def list_templates() -> list[ExportLayoutTemplateItem]:
        """Os modelos declarados no CÓDIGO (hoje, o Domínio). Sem dado escopável."""
        return [ExportLayoutTemplateItem.build(t) for t in TEMPLATES.values()]

    async def list_layouts(
        self, *, viewer: CurrentUser, requested_organization_id: UUID | None = None
    ) -> list[ExportLayoutItem]:
        organization_id = resolve_organization_filter(viewer, requested_organization_id)
        rows = await self._repo.list_layouts(viewer=viewer, organization_id=organization_id)
        return [ExportLayoutItem.build(r.layout, latest_version=r.latest_version) for r in rows]

    async def get_layout(self, layout_id: UUID, *, viewer: CurrentUser) -> ExportLayoutDetail:
        layout = await self._repo.get_layout(layout_id, viewer=viewer)
        if layout is None:
            raise NotFoundError(_LAYOUT_NOT_FOUND)
        return await self._detail(layout, viewer=viewer)

    # ------------------------------ ESCRITA ---------------------------

    async def create_layout(
        self,
        *,
        actor: CurrentUser,
        name: str,
        target_system: str,
        definition_raw: dict[str, Any],
        requested_organization_id: UUID | None = None,
    ) -> ExportLayoutDetail:
        """Layout novo com a versão 1. Definição recusada → 422 e NADA gravado."""
        definition = parse_definition(definition_raw)
        organization_id = await resolve_organization_for_creation(
            actor,
            requested_organization_id,
            get_organization=self._repo.get_organization,
            subject="o layout",
        )
        await self._ensure_organization_active(organization_id)
        return await self._insert(
            actor=actor,
            organization_id=organization_id,
            name=name,
            target_system=target_system,
            definition=definition,
        )

    async def create_from_template(
        self,
        *,
        actor: CurrentUser,
        template_key: str,
        name: str | None = None,
        requested_organization_id: UUID | None = None,
    ) -> ExportLayoutDetail:
        """O layout da organização a partir de um modelo do código, numa ação só."""
        template = TEMPLATES.get(template_key)
        if template is None:
            raise NotFoundError(f"Modelo de layout inexistente: {template_key!r}.")
        organization_id = await resolve_organization_for_creation(
            actor,
            requested_organization_id,
            get_organization=self._repo.get_organization,
            subject="o layout",
        )
        await self._ensure_organization_active(organization_id)
        # A definição do modelo passa pela MESMA validação de qualquer outra: o modelo
        # não tem atalho, e um modelo quebrado falha aqui, não no arquivo.
        definition = parse_definition(template.definition.to_json())
        return await self._insert(
            actor=actor,
            organization_id=organization_id,
            name=name or template.name,
            target_system=template.target_system,
            definition=definition,
        )

    async def create_version(
        self, layout_id: UUID, *, actor: CurrentUser, definition_raw: dict[str, Any]
    ) -> ExportLayoutDetail:
        """Versão N+1. A anterior fica intacta; definição recusada → 422, nada gravado."""
        definition = parse_definition(definition_raw)
        layout = await self._repo.lock_layout(layout_id, viewer=actor)
        if layout is None:
            raise NotFoundError(_LAYOUT_NOT_FOUND)
        await self._ensure_organization_active(layout.organization_id)
        version_number = await self._repo.latest_version(layout.id) + 1
        version = ExportLayoutVersion(
            layout_id=layout.id,
            version=version_number,
            definition=definition.to_json(),
            author_id=UUID(actor.id),
        )
        if not await self._repo.insert_version(version):
            raise ConflictError(
                f"Versão {version_number} do layout {layout.id} já gravada por outra requisição.",
                user_message="Outra versão deste layout acabou de ser gravada. Recarregue e tente de novo.",
            )
        await self._repo.commit()
        log.info(
            "export_layout_version_created",
            layout_id=str(layout.id),
            organization_id=str(layout.organization_id),
            version=version_number,
        )
        return await self._detail(layout, viewer=actor)

    async def delete_layout(self, layout_id: UUID, *, actor: CurrentUser) -> None:
        """Apaga o layout e as versões dele, só se NENHUMA versão gerou arquivo.

        O `FOR UPDATE` do layout serializa com `create_version` (a versão nova não
        nasce no meio da exclusão); a geração não trava o layout, e a FK RESTRICT da
        geração para a versão é a rede da corrida.
        """
        layout = await self._repo.lock_layout(layout_id, viewer=actor)
        if layout is None:
            raise NotFoundError(_LAYOUT_NOT_FOUND)
        await self._ensure_organization_active(layout.organization_id)
        generations = await self._repo.count_generations(layout.id)
        if generations > 0:
            raise self._in_use(layout.id, generations)
        if not await self._repo.delete_layout(layout):
            # Corrida: a geração que a FK barrou já está gravada, e a recontagem a
            # enxerga (READ COMMITTED). O piso de 1 é o que a FK já provou.
            generations = max(await self._repo.count_generations(layout.id), 1)
            raise self._in_use(layout.id, generations)
        await self._repo.commit()
        log.info(
            "export_layout_deleted",
            layout_id=str(layout_id),
            organization_id=str(layout.organization_id),
        )

    # ------------------------------ internals -------------------------

    @staticmethod
    def _in_use(layout_id: UUID, generations: int) -> ExportLayoutInUseError:
        files = "arquivo contábil" if generations == 1 else "arquivos contábeis"
        return ExportLayoutInUseError(
            f"Layout {layout_id} referenciado por {generations} geração(ões).",
            user_message=(
                f"Este layout já gerou {generations} {files} e por isso não pode ser "
                "excluído: o download de cada arquivo é refeito a partir dele."
            ),
        )

    async def _insert(
        self,
        *,
        actor: CurrentUser,
        organization_id: UUID,
        name: str,
        target_system: str,
        definition: LayoutDefinition,
    ) -> ExportLayoutDetail:
        layout = ExportLayout(
            organization_id=organization_id,
            name=name,
            target_system=target_system,
            created_by=UUID(actor.id),
        )
        version = ExportLayoutVersion(
            version=1, definition=definition.to_json(), author_id=UUID(actor.id)
        )
        if not await self._repo.insert_layout(layout, version):
            raise ExportLayoutNameAlreadyExistsError(
                f"Layout com este nome já existe na organização {organization_id}."
            )
        await self._repo.commit()
        log.info(
            "export_layout_created",
            layout_id=str(layout.id),
            organization_id=str(organization_id),
        )
        return await self._detail(layout, viewer=actor)

    async def _detail(self, layout: ExportLayout, *, viewer: CurrentUser) -> ExportLayoutDetail:
        versions = await self._repo.list_versions(layout.id)
        item = ExportLayoutItem.build(layout, latest_version=versions[0].version.version)
        return ExportLayoutDetail(
            **item.model_dump(),
            versions=[
                ExportLayoutVersionItem.build(row.version, author_for_viewer(row.author, viewer))
                for row in versions
            ],
        )

    async def _ensure_organization_active(self, organization_id: UUID) -> None:
        organization = await self._repo.get_organization(organization_id)
        if organization is None or not organization.active:
            raise OrganizationInactiveError(
                f"Organização {organization_id} suspensa: layouts só-leitura."
            )
