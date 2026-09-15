"""Carteira compartilhada — N gerentes com ACESSO, UM responsável (86e390kz8).

O que este módulo prova, ponta a ponta pelas rotas:
    - dois gerentes veem o MESMO cliente (lista, histórico, favorito,
      notificação); ninguém vê cliente fora da carteira;
    - cliente com 2 gerentes aparece UMA vez na lista, com total e paginação
      certos, e o colaborador (não-responsável) o vê na própria lista;
    - trocar o responsável NÃO remove ninguém (o caso da Bruna, 14/09/2026);
    - remover o responsável sem substituto é recusado (409); remover um
      colaborador revoga o acesso no request seguinte;
    - a mesma pessoa não entra duas vezes (409); alvo inválido é 400; cliente
      ENCERRADO recusa escrita (409); papel de cliente e manager de sistema
      recebem 403 (admin-only pela matriz).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    Notification,
    NotificationType,
    ReconciliationSession,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Carteira#2026"
ADMIN_EMAIL = "carteira-admin@hologram.com.br"
RESPONSIBLE_EMAIL = "responsavel@hologram.com.br"
COLLABORATOR_EMAIL = "colaborador@hologram.com.br"
OUTSIDER_EMAIL = "fora-da-carteira@hologram.com.br"
TENANT_MANAGER_EMAIL = "gestor@cliente-x.com.br"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    name: str = "Gerente",
    active: bool = True,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    user = User(
        name=name,
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=active,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    responsible: User | None = None,
    collaborators: tuple[User, ...] = (),
    closed: bool = False,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("carteira-app-key", hex_key)
    ct_s, iv_s = encrypt("carteira-app-secret", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=not closed,
        created_by=creator.id,
        closed_at=datetime.now(UTC) if closed else None,
    )
    session.add(client)
    await session.flush()
    if responsible is not None:
        session.add(
            ClientAssignment(
                client_id=client.id,
                user_id=responsible.id,
                assigned_by=creator.id,
                is_primary=True,
            )
        )
    for collaborator in collaborators:
        session.add(
            ClientAssignment(client_id=client.id, user_id=collaborator.id, assigned_by=creator.id)
        )
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=_hex64(f"carteira-{uuid4().hex}"),
        status="reviewing",
        balance_start=Decimal("0.00"),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _login_as(client: AsyncClient, email: str) -> None:
    await client.post("/api/v1/auth/logout")
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _managers_url(client_id: UUID) -> str:
    return f"/api/v1/clients/{client_id}/managers"


async def _seed_shared_client(
    db_session: AsyncSession, *, closed: bool = False
) -> tuple[User, User, User, Client]:
    """admin + responsável + colaborador + cliente com os dois na carteira."""
    admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN, name="Admin")
    responsible = await _seed_user(
        db_session, email=RESPONSIBLE_EMAIL, role=UserRole.MANAGER, name="Bruna R."
    )
    collaborator = await _seed_user(
        db_session, email=COLLABORATOR_EMAIL, role=UserRole.MANAGER, name="Murilo C."
    )
    client = await _seed_client(
        db_session,
        name="Cliente Compartilhado",
        creator=admin,
        responsible=responsible,
        collaborators=(collaborator,),
        closed=closed,
    )
    return admin, responsible, collaborator, client


# ----------------------------------------------------------------------
# GET /clients/{id}/managers
# ----------------------------------------------------------------------


class TestListManagers:
    async def test_admin_lists_responsible_first_then_collaborators_by_name(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        responsible = await _seed_user(
            db_session, email=RESPONSIBLE_EMAIL, role=UserRole.MANAGER, name="Zulmira"
        )
        ana = await _seed_user(
            db_session, email="ana@hologram.com.br", role=UserRole.MANAGER, name="Ana"
        )
        beto = await _seed_user(
            db_session, email="beto@hologram.com.br", role=UserRole.MANAGER, name="Beto"
        )
        client = await _seed_client(
            db_session,
            name="Três gerentes",
            creator=admin,
            responsible=responsible,
            collaborators=(beto, ana),
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(_managers_url(client.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert [(m["name"], m["is_responsible"]) for m in data] == [
            ("Zulmira", True),
            ("Ana", False),
            ("Beto", False),
        ]
        assert data[0]["id"] == str(responsible.id)
        # Identidade ENXUTA (§3.15): só o que a tela precisa, nunca a linha de users.
        assert set(data[0]) == {"id", "name", "email", "is_responsible", "assigned_at"}
        assert "password_hash" not in resp.text

    async def test_system_manager_gets_403_even_when_in_portfolio(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, RESPONSIBLE_EMAIL)

        resp = await client_with_db.get(_managers_url(client.id))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    async def test_client_role_gets_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session)
        await _seed_user(
            db_session,
            email=TENANT_MANAGER_EMAIL,
            role=UserRole.CLIENT_MANAGER,
            scope=UserScope.CLIENT,
            client_id=client.id,
        )
        await _login_as(client_with_db, TENANT_MANAGER_EMAIL)

        resp = await client_with_db.get(_managers_url(client.id))
        assert resp.status_code == 403
        assert "Bruna" not in resp.text

    async def test_unknown_client_is_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(_managers_url(uuid4()))
        assert resp.status_code == 404

    async def test_closed_client_still_lists_who_had_access(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Leitura continua no encerrado (histórico retido); só a escrita é 409."""
        _, _, _, client = await _seed_shared_client(db_session, closed=True)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(_managers_url(client.id))
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 2


# ----------------------------------------------------------------------
# POST /clients/{id}/managers — conceder acesso
# ----------------------------------------------------------------------


class TestAddManager:
    async def test_admin_adds_collaborator_and_nobody_is_removed(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        responsible = await _seed_user(
            db_session, email=RESPONSIBLE_EMAIL, role=UserRole.MANAGER, name="Bruna R."
        )
        newcomer = await _seed_user(
            db_session, email=COLLABORATOR_EMAIL, role=UserRole.MANAGER, name="Murilo C."
        )
        client = await _seed_client(
            db_session, name="Hologram", creator=admin, responsible=responsible
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            _managers_url(client.id), json={"user_id": str(newcomer.id)}
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert {(m["id"], m["is_responsible"]) for m in data} == {
            (str(responsible.id), True),
            (str(newcomer.id), False),
        }

        # A lista de clientes reflete: responsável inalterado, contagem em 2.
        listing = await client_with_db.get("/api/v1/clients")
        row = next(c for c in listing.json()["data"] if c["id"] == str(client.id))
        assert row["responsible_manager"]["id"] == str(responsible.id)
        assert row["manager_count"] == 2

    async def test_new_collaborator_sees_the_client_everywhere(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        responsible = await _seed_user(db_session, email=RESPONSIBLE_EMAIL, role=UserRole.MANAGER)
        newcomer = await _seed_user(db_session, email=COLLABORATOR_EMAIL, role=UserRole.MANAGER)
        client = await _seed_client(
            db_session, name="Hologram", creator=admin, responsible=responsible
        )
        sess = await _seed_session(db_session, client=client, creator=admin)
        # Notificação endereçada ao colaborador: visível só se a carteira o inclui.
        db_session.add(
            Notification(
                user_id=newcomer.id,
                client_id=client.id,
                session_id=sess.id,
                tipo=NotificationType.PROCESSADA.value,
                omie_conta_id=42,
                reference_month=date(2026, 4, 1),
            )
        )
        await db_session.flush()

        # Antes: fora da carteira — nada.
        await _login_as(client_with_db, COLLABORATOR_EMAIL)
        assert (await client_with_db.get("/api/v1/clients")).json()["pagination"]["total"] == 0
        assert (
            await client_with_db.get(f"/api/v1/clients/{client.id}/reconciliations")
        ).status_code == 403
        assert (await client_with_db.get("/api/v1/notifications")).json()["data"] == []

        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            _managers_url(client.id), json={"user_id": str(newcomer.id)}
        )
        assert resp.status_code == 201, resp.text

        # Depois: o cliente aparece UMA vez, o histórico abre, o favorito funciona,
        # e a notificação dele passa a ser visível.
        await _login_as(client_with_db, COLLABORATOR_EMAIL)
        listing = await client_with_db.get("/api/v1/clients")
        assert [c["id"] for c in listing.json()["data"]] == [str(client.id)]
        assert listing.json()["pagination"]["total"] == 1
        history = await client_with_db.get(f"/api/v1/clients/{client.id}/reconciliations")
        assert history.status_code == 200, history.text
        favorite = await client_with_db.put(f"/api/v1/clients/{client.id}/favorite")
        assert favorite.status_code == 200, favorite.text
        notifications = await client_with_db.get("/api/v1/notifications")
        assert len(notifications.json()["data"]) == 1

    async def test_adding_twice_is_409_and_creates_nothing(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, collaborator, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            _managers_url(client.id), json={"user_id": str(collaborator.id)}
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CONFLICT"
        listing = await client_with_db.get(_managers_url(client.id))
        assert len(listing.json()["data"]) == 2

    async def test_admin_target_is_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin, _, _, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(_managers_url(client.id), json={"user_id": str(admin.id)})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_inactive_manager_is_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session)
        inactive = await _seed_user(
            db_session, email="inativo@hologram.com.br", role=UserRole.MANAGER, active=False
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            _managers_url(client.id), json={"user_id": str(inactive.id)}
        )
        assert resp.status_code == 400

    async def test_unknown_user_is_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(_managers_url(client.id), json={"user_id": str(uuid4())})
        assert resp.status_code == 400

    async def test_closed_client_is_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session, closed=True)
        outsider = await _seed_user(db_session, email=OUTSIDER_EMAIL, role=UserRole.MANAGER)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            _managers_url(client.id), json={"user_id": str(outsider.id)}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"

    async def test_system_manager_cannot_add(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session)
        outsider = await _seed_user(db_session, email=OUTSIDER_EMAIL, role=UserRole.MANAGER)
        await _login_as(client_with_db, RESPONSIBLE_EMAIL)

        resp = await client_with_db.post(
            _managers_url(client.id), json={"user_id": str(outsider.id)}
        )
        assert resp.status_code == 403


# ----------------------------------------------------------------------
# DELETE /clients/{id}/managers/{user_id} — remover acesso
# ----------------------------------------------------------------------


class TestRemoveManager:
    async def test_removing_collaborator_revokes_access_on_next_request(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, responsible, collaborator, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.delete(f"{_managers_url(client.id)}/{collaborator.id}")
        assert resp.status_code == 200, resp.text
        assert [m["id"] for m in resp.json()["data"]] == [str(responsible.id)]

        await _login_as(client_with_db, COLLABORATOR_EMAIL)
        assert (await client_with_db.get("/api/v1/clients")).json()["pagination"]["total"] == 0
        assert (
            await client_with_db.get(f"/api/v1/clients/{client.id}/reconciliations")
        ).status_code == 403
        assert (
            await client_with_db.put(f"/api/v1/clients/{client.id}/favorite")
        ).status_code == 403

    async def test_removing_the_responsible_without_substitute_is_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Cliente nunca fica órfão: define-se outro responsável antes."""
        _, responsible, _, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.delete(f"{_managers_url(client.id)}/{responsible.id}")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CONFLICT"
        assert "responsável" in resp.json()["error"]["userMessage"]

        listing = await client_with_db.get(_managers_url(client.id))
        assert {m["id"] for m in listing.json()["data"]} >= {str(responsible.id)}
        # E o responsável continua vendo o cliente.
        await _login_as(client_with_db, RESPONSIBLE_EMAIL)
        assert (await client_with_db.get("/api/v1/clients")).json()["pagination"]["total"] == 1

    async def test_removing_someone_without_access_is_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, client = await _seed_shared_client(db_session)
        outsider = await _seed_user(db_session, email=OUTSIDER_EMAIL, role=UserRole.MANAGER)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.delete(f"{_managers_url(client.id)}/{outsider.id}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_old_responsible_can_leave_after_handover(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, responsible, collaborator, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        handover = await client_with_db.patch(
            f"/api/v1/clients/{client.id}/assign", json={"user_id": str(collaborator.id)}
        )
        assert handover.status_code == 200, handover.text

        resp = await client_with_db.delete(f"{_managers_url(client.id)}/{responsible.id}")
        assert resp.status_code == 200, resp.text
        assert [(m["id"], m["is_responsible"]) for m in resp.json()["data"]] == [
            (str(collaborator.id), True)
        ]

    async def test_closed_client_is_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, collaborator, client = await _seed_shared_client(db_session, closed=True)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.delete(f"{_managers_url(client.id)}/{collaborator.id}")
        assert resp.status_code == 409


# ----------------------------------------------------------------------
# PATCH /clients/{id}/assign — definir o responsável
# ----------------------------------------------------------------------


class TestSetResponsible:
    async def test_promoting_collaborator_swaps_flags_and_removes_no_one(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, responsible, collaborator, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{client.id}/assign", json={"user_id": str(collaborator.id)}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["responsible_manager"]["id"] == str(collaborator.id)
        assert resp.json()["manager_count"] == 2

        listing = await client_with_db.get(_managers_url(client.id))
        assert [(m["id"], m["is_responsible"]) for m in listing.json()["data"]] == [
            (str(collaborator.id), True),
            (str(responsible.id), False),
        ]

        # O antigo responsável CONTINUA na carteira.
        await _login_as(client_with_db, RESPONSIBLE_EMAIL)
        rows = (await client_with_db.get("/api/v1/clients")).json()["data"]
        assert [c["id"] for c in rows] == [str(client.id)]

    async def test_assigning_an_outsider_adds_and_promotes_without_removing(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O caso da Bruna: tornar o Murilo responsável não pode tirar a Bruna."""
        _, responsible, collaborator, client = await _seed_shared_client(db_session)
        outsider = await _seed_user(
            db_session, email=OUTSIDER_EMAIL, role=UserRole.MANAGER, name="Novo Gerente"
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{client.id}/assign", json={"user_id": str(outsider.id)}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["responsible_manager"]["id"] == str(outsider.id)
        assert resp.json()["manager_count"] == 3

        listing = await client_with_db.get(_managers_url(client.id))
        assert {(m["id"], m["is_responsible"]) for m in listing.json()["data"]} == {
            (str(outsider.id), True),
            (str(responsible.id), False),
            (str(collaborator.id), False),
        }

    async def test_assigning_the_current_responsible_is_a_noop(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, responsible, _, client = await _seed_shared_client(db_session)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{client.id}/assign", json={"user_id": str(responsible.id)}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["responsible_manager"]["id"] == str(responsible.id)
        assert resp.json()["manager_count"] == 2

    async def test_closed_client_is_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, collaborator, client = await _seed_shared_client(db_session, closed=True)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{client.id}/assign", json={"user_id": str(collaborator.id)}
        )
        assert resp.status_code == 409


# ----------------------------------------------------------------------
# GET /clients — a listagem com carteira compartilhada
# ----------------------------------------------------------------------


class TestListingWithSharedPortfolio:
    async def test_client_with_two_managers_appears_once_and_pagination_adds_up(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O join de exibição é só do responsável: nada de linha repetida."""
        admin, responsible, collaborator, shared = await _seed_shared_client(db_session)
        await _seed_client(db_session, name="Só um gerente", creator=admin, responsible=responsible)
        await _seed_client(
            db_session, name="Outro gerente", creator=admin, responsible=collaborator
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        page1 = (await client_with_db.get("/api/v1/clients?page=1&pageSize=2")).json()
        page2 = (await client_with_db.get("/api/v1/clients?page=2&pageSize=2")).json()
        assert page1["pagination"]["total"] == 3
        assert page1["pagination"]["totalPages"] == 2
        ids = [c["id"] for c in page1["data"]] + [c["id"] for c in page2["data"]]
        assert len(ids) == 3
        assert len(set(ids)) == 3

        everything = (await client_with_db.get("/api/v1/clients")).json()["data"]
        row = next(c for c in everything if c["id"] == str(shared.id))
        assert row["responsible_manager"]["id"] == str(responsible.id)
        assert row["manager_count"] == 2

    async def test_collaborator_sees_the_client_with_the_responsible_name(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O filtro da carteira é por EXISTS — o colaborador não some da própria lista."""
        _, responsible, _, shared = await _seed_shared_client(db_session)
        await _login_as(client_with_db, COLLABORATOR_EMAIL)

        body = (await client_with_db.get("/api/v1/clients")).json()
        assert body["pagination"]["total"] == 1
        assert [c["id"] for c in body["data"]] == [str(shared.id)]
        assert body["data"][0]["responsible_manager"]["id"] == str(responsible.id)
        assert body["data"][0]["manager_count"] == 2

    async def test_manager_outside_the_portfolio_sees_nothing(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, shared = await _seed_shared_client(db_session)
        await _seed_user(db_session, email=OUTSIDER_EMAIL, role=UserRole.MANAGER)
        await _login_as(client_with_db, OUTSIDER_EMAIL)

        body = (await client_with_db.get("/api/v1/clients")).json()
        assert body["pagination"]["total"] == 0
        assert body["data"] == []
        assert (
            await client_with_db.get(f"/api/v1/clients/{shared.id}/reconciliations")
        ).status_code == 403

    async def test_manager_total_counts_each_client_once(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin, responsible, _, _ = await _seed_shared_client(db_session)
        await _seed_client(db_session, name="Só dela", creator=admin, responsible=responsible)
        await _login_as(client_with_db, RESPONSIBLE_EMAIL)

        body = (await client_with_db.get("/api/v1/clients?search=c")).json()
        assert body["pagination"]["total"] == len(body["data"])
        assert len({c["id"] for c in body["data"]}) == len(body["data"])
