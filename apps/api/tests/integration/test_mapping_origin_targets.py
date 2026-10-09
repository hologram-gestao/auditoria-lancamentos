"""Prévia dos alvos do demonstrativo a partir da origem (86e3n70pn, bloco B).

O caso real (reunião com o Murilo, 08/10/2026): organização nova nasce com os cinco
destinos e ZERO alvos; "Iniciar de-para" herdava zero, porque a herança casa o
`dre_code` das categorias sincronizadas com o código de um alvo do catálogo.

O cenário de ponta a ponta prova o caminho que destrava o escritório: sincronizar o
plano de contas (resposta REAL da Omie), ler a prévia (que NÃO grava nada), criar os
faltantes pelo lote que já existe e herdar — agora encontrando os alvos.

O cross-tenant e o cross-org desta rota rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    Client,
    ClientAssignment,
    ClientChartOfAccount,
    MappingDestination,
    MappingTarget,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.main import app as fastapi_app
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@AlvosDaOrigem#1"
OMIE_CATEGORIAS_URL = "https://app.omie.com.br/api/v1/geral/categorias/"
_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "omie" / "listar_categorias.response.json"
)


def _fixture_items() -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    items = payload["categoria_cadastro"]
    assert isinstance(items, list)
    return items


def _expected_from_fixture() -> tuple[dict[str, str], int]:
    """As contas de demonstrativo das categorias ATIVAS da fixture: código → nome, e
    quantas categorias ativas apontam para alguma (o que a herança deve criar)."""
    names: dict[str, str] = {}
    with_dre = 0
    for item in _fixture_items():
        if item.get("conta_inativa") == "S":
            continue
        dre = item.get("dadosDRE") or {}
        code = (dre.get("codigoDRE") or "").strip()
        if not code:
            continue
        with_dre += 1
        names[code] = " ".join(str(dre.get("descricaoDRE") or "").split())
    return names, with_dre


def _omie_response(items: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "pagina": 1,
            "total_de_paginas": 1,
            "registros": len(items),
            "total_de_registros": len(items),
            "categoria_cadastro": items,
        },
    )


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    fastapi_app.state.omie_categorias_cache = OmieCategoriasCache()


async def _user(session: AsyncSession, *, role: UserRole, prefix: str) -> User:
    user = User(
        name=f"Alvos da origem {role.value}",
        email=f"{prefix}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    session.add(user)
    await session.flush()
    return user


class World:
    admin: User
    manager: User
    client: Client
    demonstrativo: MappingDestination


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN, prefix="ot-admin")
    w.manager = await _user(db_session, role=UserRole.MANAGER, prefix="ot-mgr")
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("origin-targets-key", hex_key)
    ct_secret, iv_secret = encrypt("origin-targets-secret", hex_key)
    w.client = Client(
        name="Cliente Alvos da Origem SEGREDO",
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=w.admin.id,
    )
    db_session.add(w.client)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    await db_session.flush()
    w.demonstrativo = (
        await db_session.execute(
            select(MappingDestination).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == "demonstrativo_contabil",
            )
        )
    ).scalar_one()
    return w


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _preview_url(w: World, destination_type: str = "demonstrativo_contabil") -> str:
    return f"/api/v1/clients/{w.client.id}/mapping/{destination_type}/origin-targets"


async def _targets_count(db: AsyncSession, destination: MappingDestination) -> int:
    return int(
        (
            await db.execute(
                select(func.count(MappingTarget.id)).where(
                    MappingTarget.destination_id == destination.id
                )
            )
        ).scalar_one()
    )


@pytest.mark.integration
class TestDaOrigemAteAHeranca:
    @respx.mock
    async def test_sincroniza_previa_cria_pelo_lote_e_a_heranca_encontra(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        w = world
        expected_names, expected_inherited = _expected_from_fixture()
        assert expected_names, "a fixture real precisa ter conta de demonstrativo"

        await _login(client_with_db, w.admin)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_fixture_items()))
        sync = await client_with_db.post(f"/api/v1/clients/{w.client.id}/chart-of-accounts/sync")
        assert sync.status_code == 200, sync.text

        # Antes: catálogo vazio, e a herança não encontra nada (o defeito da reunião).
        vazia = await client_with_db.post(
            f"/api/v1/clients/{w.client.id}/mapping/demonstrativo_contabil/inherit", json={}
        )
        assert vazia.status_code == 200, vazia.text
        assert vazia.json()["data"]["created"] == 0
        assert sorted(vazia.json()["data"]["missingTargetCodes"]) == sorted(expected_names)

        previa = await client_with_db.get(_preview_url(w))
        assert previa.status_code == 200, previa.text
        data = previa.json()["data"]
        assert data["state"] == "ok"
        assert data["destinationId"] == str(w.demonstrativo.id)
        assert data["namesResolved"] is True
        assert {c["code"]: c["name"] for c in data["candidates"]} == expected_names
        assert all(not c["exists"] and c["creatable"] for c in data["candidates"])
        assert sum(c["categories"] for c in data["candidates"]) == expected_inherited
        # Só LÊ: nada entrou no catálogo da organização.
        assert await _targets_count(db_session, w.demonstrativo) == 0

        lote = await client_with_db.post(
            f"/api/v1/mapping-destinations/{data['destinationId']}/targets",
            json={
                "targets": [
                    {"code": c["code"], "name": c["name"]}
                    for c in data["candidates"]
                    if c["creatable"]
                ]
            },
        )
        assert lote.status_code == 201, lote.text
        assert await _targets_count(db_session, w.demonstrativo) == len(expected_names)

        # A prévia seguinte vê tudo como existente — e não oferece nada para criar.
        depois = (await client_with_db.get(_preview_url(w))).json()["data"]
        assert all(c["exists"] and c["active"] and not c["creatable"] for c in depois["candidates"])

        herda = await client_with_db.post(
            f"/api/v1/clients/{w.client.id}/mapping/demonstrativo_contabil/inherit", json={}
        )
        assert herda.status_code == 200, herda.text
        assert herda.json()["data"]["state"] == "ok"
        assert herda.json()["data"]["created"] == expected_inherited
        assert herda.json()["data"]["missingTargetCodes"] == []


@pytest.mark.integration
class TestEstadosEPermissao:
    async def test_cliente_sem_plano_e_outro_destino(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        sem_plano = await client_with_db.get(_preview_url(world))
        assert sem_plano.status_code == 200, sem_plano.text
        assert sem_plano.json()["data"]["state"] == "sem_plano_de_contas"
        assert sem_plano.json()["data"]["candidates"] == []

        outro = await client_with_db.get(_preview_url(world, "fluxo_de_caixa"))
        assert outro.status_code == 200, outro.text
        assert outro.json()["data"]["state"] == "destino_sem_heranca"

    async def test_gerente_da_carteira_e_negado_com_trilha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O gerente ALCANÇA o cliente, mas não escreve no catálogo: 403 + `denied`."""
        await _login(client_with_db, world.manager)
        resp = await client_with_db.get(_preview_url(world))
        assert resp.status_code == 403, resp.text
        assert world.client.name not in resp.text
        denied = (
            await db_session.execute(
                select(func.count(AccessAudit.id)).where(
                    AccessAudit.action == "denied",
                    AccessAudit.client_id == world.client.id,
                    AccessAudit.user_id == world.manager.id,
                )
            )
        ).scalar_one()
        assert denied == 1

    @respx.mock
    async def test_alvo_existente_e_origem_fora_do_ar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Existente não é recriado; sem nome da origem, a conta não entra no lote."""
        w = world
        for category, dre in (("1.01.01", "R.01"), ("1.01.02", "R.01"), ("2.01.01", "D.01")):
            db_session.add(
                ClientChartOfAccount(client_id=w.client.id, category_code=category, dre_code=dre)
            )
        db_session.add(
            MappingTarget(
                destination_id=w.demonstrativo.id, code="R.01", name="Receita", active=False
            )
        )
        await db_session.flush()
        respx.post(OMIE_CATEGORIAS_URL).mock(
            return_value=httpx.Response(
                200, json={"faultcode": "SOAP-ENV:Client-101", "faultstring": "Erro"}
            )
        )

        await _login(client_with_db, w.admin)
        resp = await client_with_db.get(_preview_url(w))

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["namesResolved"] is False
        by_code = {c["code"]: c for c in data["candidates"]}
        assert by_code["R.01"] == {
            "code": "R.01",
            "name": "Receita",
            "categories": 2,
            "exists": True,
            "active": False,
            "creatable": False,
        }
        assert by_code["D.01"]["name"] is None
        assert by_code["D.01"]["exists"] is False
        assert by_code["D.01"]["creatable"] is False
