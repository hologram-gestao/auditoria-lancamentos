"""Regras do catálogo do de-para, sem banco (BACK 12.3 — R1).

O repositório é um dublê em memória; o que está sob teste é a DECISÃO do serviço:
o validador de alvo (422 nomeando o código — reusado por 12.4 e 12.5), os 409 de
lote e de alvo em uso, a organização suspensa e o seed na criação de organização.
A integração (`test_mapping_catalog_endpoints.py`) prova o mesmo contra o banco.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.authz import CurrentUser
from app.core.exceptions import (
    MappingTargetCodeAlreadyExistsError,
    MappingTargetInUseError,
    MappingTargetNotFoundError,
    NotFoundError,
    OrganizationInactiveError,
)
from app.db.models import MappingDestination, MappingTarget, UserRole, UserScope
from app.modules.mapping_catalog.schemas import MappingTargetCreate
from app.modules.mapping_catalog.service import MappingCatalogService
from app.modules.organizations.service import OrganizationService

ORG = uuid4()


def _admin() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="admin@hologram.com.br",
        name="Admin",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=ORG,
    )


class _Repo:
    def __init__(self, *, org_active: bool = True, decisions_for_target: int = 0) -> None:
        self.destination = MappingDestination(
            id=uuid4(),
            organization_id=ORG,
            destination_type="demonstrativo_contabil",
            name="Demonstrativo",
            active=True,
        )
        self.targets: dict[str, MappingTarget] = {}
        self.org_active = org_active
        self.decisions_for_target = decisions_for_target
        self.deleted: list[MappingTarget] = []
        self.added: list[Any] = []

    def plant(self, code: str, *, active: bool = True) -> MappingTarget:
        target = MappingTarget(
            id=uuid4(),
            destination_id=self.destination.id,
            code=code,
            name=f"Alvo {code}",
            active=active,
        )
        self.targets[code] = target
        return target

    async def get_destination(self, destination_id: UUID, *, viewer: Any) -> Any:
        return self.destination if destination_id == self.destination.id else None

    async def get_organization(self, organization_id: UUID) -> Any:
        return SimpleNamespace(id=organization_id, active=self.org_active)

    async def get_targets_by_codes(self, destination_id: UUID, codes: Any) -> dict[str, Any]:
        return {c: self.targets[c] for c in set(codes) if c in self.targets}

    async def get_target(self, destination_id: UUID, target_id: UUID) -> Any:
        return next((t for t in self.targets.values() if t.id == target_id), None)

    async def count_decisions_for_target(self, target_id: UUID) -> int:
        return self.decisions_for_target

    async def add_targets(self, targets: list[MappingTarget]) -> None:
        # O flush real preenche a PK (default Python) — o dublê faz o mesmo.
        for target in targets:
            target.id = target.id or uuid4()
        self.added.extend(targets)

    async def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete_target(self, target: MappingTarget) -> None:
        self.deleted.append(target)

    async def list_targets(self, *_a: Any, **_k: Any) -> tuple[list[Any], int]:
        return [], len(self.targets)


def _service(repo: _Repo) -> MappingCatalogService:
    return MappingCatalogService(repo)  # type: ignore[arg-type]


def _t(code: str) -> MappingTargetCreate:
    return MappingTargetCreate(code=code, name=f"Nome {code}")


class TestValidadorDeAlvo:
    async def test_alvo_existente_e_ativo_passa(self) -> None:
        repo = _Repo()
        alvo = repo.plant("1.01")
        assert await _service(repo).require_target(repo.destination, "1.01") is alvo

    async def test_inexistente_e_422_nomeando_o_codigo(self) -> None:
        repo = _Repo()
        repo.plant("1.01")
        with pytest.raises(MappingTargetNotFoundError) as exc:
            await _service(repo).require_targets(repo.destination, ["1.01", "9.99"])
        assert exc.value.status_code == 422
        assert exc.value.code.value == "ALVO_INEXISTENTE"
        assert "9.99" in exc.value.user_message
        assert exc.value.details == {"targetCodes": "9.99"}

    async def test_desativado_tambem_nao_recebe_decisao(self) -> None:
        repo = _Repo()
        repo.plant("1.01", active=False)
        with pytest.raises(MappingTargetNotFoundError) as exc:
            await _service(repo).require_target(repo.destination, "1.01")
        assert "1.01" in exc.value.user_message


class TestLoteDeAlvos:
    async def test_lote_valido_cria_todos(self) -> None:
        repo = _Repo()
        criados = await _service(repo).create_targets(
            repo.destination.id, actor=_admin(), targets=[_t("1.01"), _t("1.02")]
        )
        assert [c.code for c in criados] == ["1.01", "1.02"]

    async def test_codigo_repetido_no_lote_recusa_tudo(self) -> None:
        repo = _Repo()
        with pytest.raises(MappingTargetCodeAlreadyExistsError) as exc:
            await _service(repo).create_targets(
                repo.destination.id, actor=_admin(), targets=[_t("1.01"), _t("1.01")]
            )
        assert "1.01" in exc.value.user_message
        assert repo.added == [], "atômico: nada gravado"

    async def test_codigo_ja_existente_no_destino_recusa_tudo(self) -> None:
        repo = _Repo()
        repo.plant("1.01")
        with pytest.raises(MappingTargetCodeAlreadyExistsError):
            await _service(repo).create_targets(
                repo.destination.id, actor=_admin(), targets=[_t("1.02"), _t("1.01")]
            )
        assert repo.added == []


class TestApagarEDesativar:
    async def test_alvo_referenciado_nao_se_apaga(self) -> None:
        repo = _Repo(decisions_for_target=3)
        alvo = repo.plant("1.01")
        with pytest.raises(MappingTargetInUseError) as exc:
            await _service(repo).delete_target(repo.destination.id, alvo.id, actor=_admin())
        assert exc.value.status_code == 409
        assert repo.deleted == []

    async def test_alvo_referenciado_pode_ser_desativado(self) -> None:
        repo = _Repo(decisions_for_target=3)
        alvo = repo.plant("1.01")
        item = await _service(repo).update_target(
            repo.destination.id, alvo.id, actor=_admin(), active=False
        )
        assert item.active is False

    async def test_alvo_sem_decisao_se_apaga(self) -> None:
        repo = _Repo()
        alvo = repo.plant("1.01")
        await _service(repo).delete_target(repo.destination.id, alvo.id, actor=_admin())
        assert repo.deleted == [alvo]


class TestOrganizacaoEAlcance:
    async def test_escrita_em_organizacao_suspensa_e_409(self) -> None:
        repo = _Repo(org_active=False)
        with pytest.raises(OrganizationInactiveError) as exc:
            await _service(repo).create_targets(
                repo.destination.id, actor=_admin(), targets=[_t("1.01")]
            )
        assert exc.value.status_code == 409

    async def test_destino_fora_do_alcance_e_404(self) -> None:
        repo = _Repo()
        with pytest.raises(NotFoundError):
            await _service(repo).list_targets(uuid4(), viewer=_admin(), page=1, page_size=20)


class TestSeedNaCriacaoDeOrganizacao:
    async def test_organizacao_nova_nasce_com_os_cinco_destinos(self) -> None:
        seeded: list[UUID] = []

        class _OrgRepo:
            async def get_by_name_ci(self, _name: str) -> None:
                return None

            async def add(self, organization: Any) -> None:
                # O flush + refresh reais preenchem PK e carimbos.
                organization.id = uuid4()
                organization.created_at = organization.updated_at = datetime.now(UTC)

        class _Catalog:
            async def seed_default_destinations(self, organization_id: UUID) -> None:
                seeded.append(organization_id)

        service = OrganizationService(
            _OrgRepo(),  # type: ignore[arg-type]
            mapping_catalog=_Catalog(),  # type: ignore[arg-type]
        )
        item = await service.create_organization(name="Escritório Novo")
        assert seeded == [item.id]
