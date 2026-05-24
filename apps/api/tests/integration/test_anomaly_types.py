"""Testes de integração do CRUD de tipos de anomalia (S15 BACK 11.1).

Cenários cobertos:
    - GET legado (sem `?page`) → envelope `{data: [...]}` para retrocompat
      da tela de revisão (manager+admin).
    - GET paginado (`?page=1`) → envelope `{data, pagination}` para o admin UI.
    - `?include_inactive=true` — só admin enxerga inativos; manager filtra silently.
    - POST: happy path, 409 (code duplicado), 400 (code inválido + severity).
    - PATCH: happy path, 404, code é imutável (ignorado no body).
    - PATCH active=false não apaga anomalias existentes (catálogo é histórico).
    - DELETE: 204 quando órfão, 409 quando vinculado, RBAC, 404.
    - RBAC consistente: manager bloqueado em todas as mutações.

Os testes usam `db_session` para semear e `client_with_db` para chamar as
rotas — a mesma session é injetada no DB, então mudanças no DB aparecem
para o handler.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    ReconciliationAnomaly,
    ReconciliationSession,
    User,
    UserRole,
)
from app.modules.reconciliations.processing.anomalies import (
    ANOMALY_CODE_MISSING_IN_FILE,
    ANOMALY_CODE_MISSING_IN_OMIE,
    _AnomalyTypeMissingError,
    _load_anomaly_type_ids,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


ADMIN_EMAIL = "anomtypes-admin@hologram.com.br"
MANAGER_EMAIL = "anomtypes-mgr@hologram.com.br"
ADMIN_PLAIN = "Adm1n!Senh@Forte#"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole = UserRole.ADMIN,
    plain_text: str = ADMIN_PLAIN,
    active: bool = True,
) -> User:
    user = User(
        name="Test",
        email=email.lower(),
        password_hash=hash_password(plain_text),
        role=role.value,
        active=active,
    )
    session.add(user)
    await session.flush()
    return user


async def _login_as(client: AsyncClient, email: str, plain_text: str = ADMIN_PLAIN) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": plain_text},
    )
    assert resp.status_code == 200, resp.text


async def _seed_anomaly_type(
    session: AsyncSession,
    *,
    code: str,
    name: str = "Test Type",
    description: str = "Descrição de teste",
    severity: AnomalySeverity = AnomalySeverity.MODERATE,
    active: bool = True,
) -> AnomalyType:
    at = AnomalyType(
        code=code,
        name=name,
        description=description,
        severity=severity.value,
        active=active,
    )
    session.add(at)
    await session.flush()
    return at


# ----------------------------------------------------------------------
# RBAC base — não autenticado em rotas de mutação
# ----------------------------------------------------------------------


class TestAnomalyTypesRBAC:
    async def test_unauthenticated_get_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 401

    async def test_unauthenticated_post_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "test_x",
                "name": "X",
                "description": "y",
                "severity": "info",
            },
        )
        assert resp.status_code == 401

    async def test_manager_post_returns_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        await _login_as(client_with_db, MANAGER_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "blocked",
                "name": "X",
                "description": "y",
                "severity": "info",
            },
        )
        assert resp.status_code == 403

    async def test_manager_patch_returns_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        at = await _seed_anomaly_type(db_session, code="mgr_patch_block")
        await _login_as(client_with_db, MANAGER_EMAIL)
        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{at.id}",
            json={"name": "blocked"},
        )
        assert resp.status_code == 403

    async def test_manager_delete_returns_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        at = await _seed_anomaly_type(db_session, code="mgr_delete_block")
        await _login_as(client_with_db, MANAGER_EMAIL)
        resp = await client_with_db.delete(f"/api/v1/anomaly-types/{at.id}")
        assert resp.status_code == 403


# ----------------------------------------------------------------------
# GET /anomaly-types
# ----------------------------------------------------------------------


class TestListAnomalyTypes:
    async def test_legacy_envelope_no_page_param(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Retrocompat — sem `?page`, envelope é `{data: [...]}` sem `pagination`.

        Esse é o formato que a tela de revisão consome (auto-unwrapped pelo
        wrapper do front).
        """
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _seed_anomaly_type(db_session, code="legacy_x")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "pagination" not in body
        codes = {item["code"] for item in body["data"]}
        assert "legacy_x" in codes

    async def test_manager_sees_only_active_by_default(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        await _seed_anomaly_type(db_session, code="active_one", active=True)
        await _seed_anomaly_type(db_session, code="inactive_one", active=False)
        await _login_as(client_with_db, MANAGER_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 200
        codes = {item["code"] for item in resp.json()["data"]}
        assert "active_one" in codes
        assert "inactive_one" not in codes

    async def test_manager_include_inactive_silently_filtered(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Manager passando `?include_inactive=true` NÃO recebe inativos — sem 403,
        apenas filtrado em silêncio (a rota é compartilhada com a tela de
        revisão, não pode 4xx em chamadas legítimas).
        """
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        await _seed_anomaly_type(db_session, code="mgr_active", active=True)
        await _seed_anomaly_type(db_session, code="mgr_inactive", active=False)
        await _login_as(client_with_db, MANAGER_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types?include_inactive=true")
        assert resp.status_code == 200
        codes = {item["code"] for item in resp.json()["data"]}
        assert "mgr_inactive" not in codes

    async def test_admin_include_inactive_returns_all(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _seed_anomaly_type(db_session, code="adm_active", active=True)
        await _seed_anomaly_type(db_session, code="adm_inactive", active=False)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types?include_inactive=true")
        assert resp.status_code == 200
        codes = {item["code"] for item in resp.json()["data"]}
        assert "adm_active" in codes
        assert "adm_inactive" in codes

    async def test_paginated_envelope_when_page_param_present(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        for i in range(5):
            await _seed_anomaly_type(db_session, code=f"pag_{i}")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types?page=1&pageSize=2")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "pagination" in body
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["pageSize"] == 2
        assert body["pagination"]["total"] >= 5
        assert body["pagination"]["totalPages"] >= 3
        assert len(body["data"]) == 2

    async def test_pagination_invalid_page_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.get("/api/v1/anomaly-types?page=0")
        assert resp.status_code == 400

    async def test_pagination_page_size_above_max_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.get("/api/v1/anomaly-types?page=1&pageSize=101")
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# POST /anomaly-types
# ----------------------------------------------------------------------


class TestCreateAnomalyType:
    async def test_admin_creates_happy_path(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "custom_check",
                "name": "Custom Check",
                "description": "Tipo custom criado pelo admin.",
                "severity": "info",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["code"] == "custom_check"
        assert body["severity"] == "info"
        assert body["active"] is True

    async def test_duplicate_code_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _seed_anomaly_type(db_session, code="already_here")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "already_here",
                "name": "Dup",
                "description": "x",
                "severity": "moderate",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "CONFLICT"
        assert "código" in body["error"]["userMessage"]

    async def test_invalid_code_uppercase_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "Upper_Case",
                "name": "X",
                "description": "x",
                "severity": "info",
            },
        )
        assert resp.status_code == 400

    async def test_invalid_code_with_dash_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "kebab-case",
                "name": "X",
                "description": "x",
                "severity": "info",
            },
        )
        assert resp.status_code == 400

    async def test_invalid_severity_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "valid_code",
                "name": "X",
                "description": "x",
                "severity": "urgent",  # não é critical/moderate/info
            },
        )
        assert resp.status_code == 400

    async def test_code_too_long_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/anomaly-types",
            json={
                "code": "a" * 51,
                "name": "X",
                "description": "x",
                "severity": "info",
            },
        )
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# PATCH /anomaly-types/{id}
# ----------------------------------------------------------------------


class TestUpdateAnomalyType:
    async def test_admin_updates_name_only(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="upd_name", name="Old Name")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{at.id}",
            json={"name": "New Name"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "New Name"
        assert body["code"] == "upd_name"  # code não mudou

    async def test_admin_deactivates(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="to_deactivate", active=True)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{at.id}",
            json={"active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_admin_updates_severity(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="sev_upd", severity=AnomalySeverity.INFO)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{at.id}",
            json={"severity": "critical"},
        )
        assert resp.status_code == 200
        assert resp.json()["severity"] == "critical"

    async def test_code_in_body_is_ignored_immutable(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`code` é IMUTÁVEL — Pydantic ignora keys extras silentemente,
        mantendo o `code` original."""
        await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="original_code")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{at.id}",
            json={"name": "Renamed", "code": "hacked_code"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "original_code"
        assert body["name"] == "Renamed"

    async def test_invalid_severity_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="sev_invalid_upd")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{at.id}",
            json={"severity": "urgent"},
        )
        assert resp.status_code == 400

    async def test_404_when_id_missing(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)

        ghost = "00000000-0000-0000-0000-000000000000"
        resp = await client_with_db.patch(
            f"/api/v1/anomaly-types/{ghost}",
            json={"name": "x"},
        )
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# DELETE /anomaly-types/{id}
# ----------------------------------------------------------------------


class TestDeleteAnomalyType:
    async def test_admin_deletes_orphan_returns_204(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="orphan_to_delete")
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.delete(f"/api/v1/anomaly-types/{at.id}")
        assert resp.status_code == 204

        gone = (
            await db_session.execute(select(AnomalyType).where(AnomalyType.id == at.id))
        ).scalar_one_or_none()
        assert gone is None

    async def test_in_use_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Não permite hard-delete de tipo referenciado por anomalias —
        orientação ao admin é desativar via PATCH."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL)
        at = await _seed_anomaly_type(db_session, code="in_use_block")

        # Seed mínimo de uma anomalia vinculada — precisa de Client + Session.
        client = Client(
            name="C",
            omie_app_key_encrypted="0" * 32,
            omie_app_key_iv="0" * 24,
            omie_app_secret_encrypted="0" * 32,
            omie_app_secret_iv="0" * 24,
            active=True,
            created_by=admin.id,
        )
        db_session.add(client)
        await db_session.flush()
        sess = ReconciliationSession(
            client_id=client.id,
            created_by=admin.id,
            omie_conta_id=1,
            reference_month=date(2026, 4, 1),
            date_tolerance_days=3,
            file_hash="a" * 64,
            status="reviewing",
            balance_start=Decimal("0.00"),
        )
        db_session.add(sess)
        await db_session.flush()
        anomaly = ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=at.id,
            detected_by=AnomalyDetectedBy.MANUAL.value,
        )
        db_session.add(anomaly)
        await db_session.flush()

        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.delete(f"/api/v1/anomaly-types/{at.id}")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error"]["code"] == "CONFLICT"
        assert "desative" in body["error"]["userMessage"]

    async def test_404_when_id_missing(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        ghost = "00000000-0000-0000-0000-000000000000"
        resp = await client_with_db.delete(f"/api/v1/anomaly-types/{ghost}")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Worker — tipo desativado não cria anomalias novas (BACK 11.1 #3)
# ----------------------------------------------------------------------


class TestWorkerSkipsInactiveTypes:
    async def test_load_anomaly_type_ids_skips_inactive(self, db_session: AsyncSession) -> None:
        """`_load_anomaly_type_ids` retorna dict só com tipos ativos. Quando
        o admin desativa `missing_in_omie`, conciliações novas não criam
        anomalias desse tipo (a chave some do dict).
        """
        await _seed_anomaly_type(db_session, code=ANOMALY_CODE_MISSING_IN_OMIE, active=False)
        await _seed_anomaly_type(db_session, code=ANOMALY_CODE_MISSING_IN_FILE, active=True)

        by_code = await _load_anomaly_type_ids(db_session)
        assert ANOMALY_CODE_MISSING_IN_OMIE not in by_code
        assert ANOMALY_CODE_MISSING_IN_FILE in by_code

    async def test_load_anomaly_type_ids_raises_when_code_absent(
        self, db_session: AsyncSession
    ) -> None:
        """Se o code NÃO existe no DB (seed não rodou), falha alto — distinguir
        de tipo apenas desativado."""
        # Seed só um — o outro está faltando totalmente.
        await _seed_anomaly_type(db_session, code=ANOMALY_CODE_MISSING_IN_FILE, active=True)

        with pytest.raises(_AnomalyTypeMissingError):
            await _load_anomaly_type_ids(db_session)
