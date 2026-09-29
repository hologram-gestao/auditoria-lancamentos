"""Regras dos layouts de exportação sem banco (BACK 13.2).

O serviço roda sobre um repositório falso que CONTA as escritas: é o que prova, sem
Postgres, que definição recusada não grava nada, que alterar é versão N+1 (a anterior
fica), que a organização vem da LINHA do ator e que a resposta sai depois do commit. O
guard de organização é exercitado com uma sessão falsa: a negação do usuário de cliente
grava 1 linha `denied` (só IDs) e commita antes do 403.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.authz import CurrentUser, Permission
from app.core.dependencies import require_org_permission
from app.core.exceptions import (
    ConflictError,
    ExportLayoutDefinitionError,
    ExportLayoutNameAlreadyExistsError,
    ForbiddenError,
    NotFoundError,
    OrganizationInactiveError,
    OrganizationMismatchError,
    ValidationAppError,
)
from app.db.models import (
    AccessAudit,
    ExportLayout,
    ExportLayoutVersion,
    Organization,
    User,
    UserRole,
    UserScope,
)
from app.modules.export_layouts.definition import DOMINIO_TEMPLATE
from app.modules.export_layouts.repository import LayoutRow, VersionRow
from app.modules.export_layouts.service import ExportLayoutService

ORG_A = uuid4()
ORG_B = uuid4()


def _actor(
    *,
    role: UserRole = UserRole.ADMIN,
    scope: UserScope = UserScope.SYSTEM,
    organization_id: UUID | None = ORG_A,
    client_id: UUID | None = None,
) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="a@b.c",
        name="Ator",
        role=role.value,
        scope=scope.value,
        client_id=client_id,
        organization_id=organization_id,
    )


def _platform() -> CurrentUser:
    return _actor(role=UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM, organization_id=None)


def _definition(**over: Any) -> dict[str, Any]:
    raw = copy.deepcopy(DOMINIO_TEMPLATE.definition.to_json())
    raw.update(over)
    return raw


class _Repo:
    """Repositório falso: guarda layouts/versões e registra a ORDEM das escritas."""

    def __init__(self, *, active: bool = True, name_taken: bool = False) -> None:
        self.calls: list[str] = []
        self.layouts: dict[UUID, ExportLayout] = {}
        self.versions: list[ExportLayoutVersion] = []
        self.orgs = {
            ORG_A: Organization(id=ORG_A, name="A", active=active),
            ORG_B: Organization(id=ORG_B, name="B", active=True),
        }
        self.name_taken = name_taken
        self.author = User(
            name="Autor",
            email="autor@x.com",
            role=UserRole.ADMIN.value,
            scope=UserScope.SYSTEM.value,
            password_hash="x",
            active=True,
        )

    def _visible(self, layout: ExportLayout, viewer: CurrentUser) -> bool:
        return viewer.is_platform or layout.organization_id == viewer.organization_id

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        return self.orgs.get(organization_id)

    async def list_layouts(self, *, viewer: CurrentUser, organization_id: UUID | None) -> Any:
        return [
            LayoutRow(layout=layout, latest_version=await self.latest_version(layout.id))
            for layout in self.layouts.values()
            if self._visible(layout, viewer)
            and (organization_id is None or layout.organization_id == organization_id)
        ]

    async def get_layout(self, layout_id: UUID, *, viewer: CurrentUser) -> ExportLayout | None:
        layout = self.layouts.get(layout_id)
        return layout if layout is not None and self._visible(layout, viewer) else None

    async def lock_layout(self, layout_id: UUID, *, viewer: CurrentUser) -> ExportLayout | None:
        self.calls.append("lock")
        return await self.get_layout(layout_id, viewer=viewer)

    async def latest_version(self, layout_id: UUID) -> int:
        return max((v.version for v in self.versions if v.layout_id == layout_id), default=0)

    async def list_versions(self, layout_id: UUID) -> list[VersionRow]:
        rows = sorted(
            (v for v in self.versions if v.layout_id == layout_id), key=lambda v: -v.version
        )
        return [VersionRow(version=v, author=self.author) for v in rows]

    async def insert_layout(self, layout: ExportLayout, version: ExportLayoutVersion) -> bool:
        self.calls.append("insert_layout")
        if self.name_taken:
            return False
        layout.id = uuid4()
        layout.created_at = layout.updated_at = datetime.now(UTC)
        version.layout_id = layout.id
        version.created_at = datetime.now(UTC)
        self.layouts[layout.id] = layout
        self.versions.append(version)
        return True

    async def insert_version(self, version: ExportLayoutVersion) -> bool:
        self.calls.append("insert_version")
        version.created_at = datetime.now(UTC)
        self.versions.append(version)
        return True

    async def commit(self) -> None:
        self.calls.append("commit")


def _service(repo: _Repo) -> ExportLayoutService:
    return ExportLayoutService(repo)  # type: ignore[arg-type]


class TestCriar:
    async def test_admin_cria_na_propria_org_com_a_versao_1_e_commita_antes_de_responder(
        self,
    ) -> None:
        repo = _Repo()
        detail = await _service(repo).create_layout(
            actor=_actor(), name="Domínio", target_system="Domínio", definition_raw=_definition()
        )
        assert detail.organization_id == ORG_A
        assert detail.latest_version == 1
        assert [v.version for v in detail.versions] == [1]
        assert detail.versions[0].definition == DOMINIO_TEMPLATE.definition.to_json()
        assert repo.calls == ["insert_layout", "commit"]

    async def test_definicao_recusada_e_422_e_nada_e_gravado(self) -> None:
        repo = _Repo()
        raw = _definition()
        raw["columns"][1]["field"] = "descricao"
        with pytest.raises(ExportLayoutDefinitionError) as exc:
            await _service(repo).create_layout(
                actor=_actor(), name="X", target_system="Domínio", definition_raw=raw
            )
        assert exc.value.details == {"field": "columns[1].field"}
        assert repo.calls == []
        assert repo.layouts == {}

    async def test_codificacao_inexistente_e_422_e_nada_e_gravado(self) -> None:
        repo = _Repo()
        with pytest.raises(ExportLayoutDefinitionError) as exc:
            await _service(repo).create_layout(
                actor=_actor(),
                name="X",
                target_system="Domínio",
                definition_raw=_definition(encoding="latin-42"),
            )
        assert exc.value.details == {"field": "encoding"}
        assert repo.calls == []

    async def test_staff_com_outra_organizacao_no_payload_e_403(self) -> None:
        repo = _Repo()
        with pytest.raises(OrganizationMismatchError):
            await _service(repo).create_layout(
                actor=_actor(),
                name="X",
                target_system="Domínio",
                definition_raw=_definition(),
                requested_organization_id=ORG_B,
            )
        assert repo.calls == []

    async def test_plataforma_escolhe_e_a_escolha_e_obrigatoria(self) -> None:
        repo = _Repo()
        with pytest.raises(ValidationAppError):
            await _service(repo).create_layout(
                actor=_platform(), name="X", target_system="D", definition_raw=_definition()
            )
        with pytest.raises(NotFoundError):
            await _service(repo).create_layout(
                actor=_platform(),
                name="X",
                target_system="D",
                definition_raw=_definition(),
                requested_organization_id=uuid4(),
            )
        detail = await _service(repo).create_layout(
            actor=_platform(),
            name="X",
            target_system="D",
            definition_raw=_definition(),
            requested_organization_id=ORG_B,
        )
        assert detail.organization_id == ORG_B

    async def test_organizacao_suspensa_e_409(self) -> None:
        repo = _Repo(active=False)
        with pytest.raises(OrganizationInactiveError):
            await _service(repo).create_layout(
                actor=_actor(), name="X", target_system="D", definition_raw=_definition()
            )
        assert repo.calls == []

    async def test_nome_repetido_na_organizacao_e_409(self) -> None:
        repo = _Repo(name_taken=True)
        with pytest.raises(ExportLayoutNameAlreadyExistsError):
            await _service(repo).create_from_template(
                actor=_actor(), template_key="dominio_lancamentos_csv"
            )
        assert "commit" not in repo.calls


class TestModelo:
    async def test_a_partir_do_modelo_dominio_os_parametros_batem(self) -> None:
        repo = _Repo()
        detail = await _service(repo).create_from_template(
            actor=_actor(), template_key="dominio_lancamentos_csv"
        )
        assert detail.name == "Domínio: lançamentos contábeis (CSV)"
        assert detail.target_system == "Domínio"
        definition = detail.versions[0].definition
        assert [c["field"] for c in definition["columns"]] == [
            "data",
            "conta_debito",
            "conta_credito",
            "valor",
            "historico",
        ]
        assert definition["separator"] == ";"
        assert definition["hasHeader"] is False
        assert definition["encoding"] == "latin-1"
        assert definition["lineEnding"] == "crlf"
        assert definition["dateFormat"] == "dd/mm/aaaa"
        assert definition["amountFormat"] == {
            "prefix": "R$ ",
            "thousandsSeparator": ".",
            "decimalSeparator": ",",
            "decimalPlaces": 2,
        }

    async def test_modelo_inexistente_e_404(self) -> None:
        with pytest.raises(NotFoundError):
            await _service(_Repo()).create_from_template(actor=_actor(), template_key="sped")

    def test_lista_de_modelos(self) -> None:
        (item,) = ExportLayoutService.list_templates()
        assert item.key == "dominio_lancamentos_csv"
        assert item.definition == DOMINIO_TEMPLATE.definition.to_json()


class TestVersionar:
    async def test_alterar_grava_n_mais_1_e_preserva_a_anterior(self) -> None:
        repo = _Repo()
        service = _service(repo)
        created = await service.create_from_template(
            actor=_actor(), template_key="dominio_lancamentos_csv"
        )
        repo.calls.clear()
        nova = _definition(hasHeader=True)
        detail = await service.create_version(created.id, actor=_actor(), definition_raw=nova)
        assert repo.calls == ["lock", "insert_version", "commit"]
        assert detail.latest_version == 2
        assert [v.version for v in detail.versions] == [2, 1]
        assert detail.versions[0].definition["hasHeader"] is True
        assert detail.versions[1].definition == DOMINIO_TEMPLATE.definition.to_json()

    async def test_versao_invalida_nao_grava(self) -> None:
        repo = _Repo()
        service = _service(repo)
        created = await service.create_from_template(
            actor=_actor(), template_key="dominio_lancamentos_csv"
        )
        repo.calls.clear()
        raw = _definition()
        raw["amountFormat"]["decimalPlaces"] = 9
        with pytest.raises(ExportLayoutDefinitionError):
            await service.create_version(created.id, actor=_actor(), definition_raw=raw)
        assert repo.calls == []
        assert await repo.latest_version(created.id) == 1

    async def test_layout_de_outra_organizacao_e_404(self) -> None:
        repo = _Repo()
        service = _service(repo)
        created = await service.create_from_template(
            actor=_actor(), template_key="dominio_lancamentos_csv"
        )
        outsider = _actor(organization_id=ORG_B)
        with pytest.raises(NotFoundError):
            await service.get_layout(created.id, viewer=outsider)
        with pytest.raises(NotFoundError):
            await service.create_version(created.id, actor=outsider, definition_raw=_definition())
        assert await service.list_layouts(viewer=outsider) == []

    async def test_corrida_pela_mesma_versao_e_409(self) -> None:
        repo = _Repo()
        service = _service(repo)
        created = await service.create_from_template(
            actor=_actor(), template_key="dominio_lancamentos_csv"
        )

        async def _lost(version: ExportLayoutVersion) -> bool:
            return False

        repo.insert_version = _lost  # type: ignore[method-assign]
        with pytest.raises(ConflictError):
            await service.create_version(created.id, actor=_actor(), definition_raw=_definition())


class _Db:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:  # pragma: no cover — o caminho denied commita
        return None


class TestGuardDeOrganizacao:
    async def test_usuario_de_cliente_e_403_com_uma_linha_denied_so_de_ids(self) -> None:
        guard = require_org_permission(
            Permission.MANAGE_EXPORT_LAYOUTS, Permission.GENERATE_ACCOUNTING_FILE
        )
        tenant = uuid4()
        for role in (UserRole.CLIENT_MANAGER, UserRole.CLIENT_OPERATOR):
            db = _Db()
            user = _actor(role=role, scope=UserScope.CLIENT, client_id=tenant)
            with pytest.raises(ForbiddenError):
                await guard(user, db)  # type: ignore[arg-type]
            (row,) = db.added
            assert isinstance(row, AccessAudit)
            assert row.action == "denied"
            assert row.client_id == tenant
            assert row.actor_client_id == tenant
            assert row.user_scope == "client"
            assert row.actor_organization_id == ORG_A
            assert db.commits == 1

    async def test_gerente_le_mas_nao_escreve_e_a_negacao_de_staff_nao_grava_linha(self) -> None:
        read = require_org_permission(
            Permission.MANAGE_EXPORT_LAYOUTS, Permission.GENERATE_ACCOUNTING_FILE
        )
        write = require_org_permission(Permission.MANAGE_EXPORT_LAYOUTS)
        manager = _actor(role=UserRole.MANAGER)
        db = _Db()
        assert await read(manager, db) is manager  # type: ignore[arg-type]
        with pytest.raises(ForbiddenError):
            await write(manager, db)  # type: ignore[arg-type]
        assert db.added == []

    async def test_admin_e_plataforma_escrevem(self) -> None:
        write = require_org_permission(Permission.MANAGE_EXPORT_LAYOUTS)
        for user in (_actor(), _platform()):
            assert await write(user, _Db()) is user  # type: ignore[arg-type]
