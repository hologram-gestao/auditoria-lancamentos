"""Configuração do lançamento como Contas a Pagar por cliente (86e3n70q0, bloco B).

Dois campos, os dois CÓDIGO ou FLAG (nada de nome, §4.5):

- `omie_default_supplier_code` — tri-estado no PATCH, como `category_id`:
  omitido mantém, `null` tira o padrão, inteiro troca; zero ou negativo é 400;
- `omie_department_required` — omitido mantém, booleano troca.

Escrita do `edit_client` (admin); o gerente recebe 403, como no resto do PATCH.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db.models import Client, User, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
ADMIN_EMAIL = "admin-payables-config@hologram.com.br"
BASE = "/api/v1/clients"


async def _login_admin(client: AsyncClient, session: AsyncSession) -> User:
    admin = User(
        name="Admin Provisao",
        email=ADMIN_EMAIL,
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(admin)
    await session.flush()
    resp = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return admin


class TestPayablesConfigDoCliente:
    async def test_cliente_nasce_sem_padrao_e_sem_rateio_obrigatorio(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(BASE, json={"name": "Sem config"})
        assert criado.status_code == 201, criado.text
        body = criado.json()
        assert body["omie_default_supplier_code"] is None
        assert body["omie_department_required"] is False

    async def test_cliente_nasce_com_a_config_declarada(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(
            BASE,
            json={
                "name": "PCTAX",
                "omie_default_supplier_code": 3052086626,
                "omie_department_required": True,
            },
        )
        assert criado.status_code == 201, criado.text
        detalhe = await client_with_db.get(f"{BASE}/{criado.json()['id']}")
        assert detalhe.json()["omie_default_supplier_code"] == 3052086626
        assert detalhe.json()["omie_department_required"] is True

    async def test_patch_tri_estado_do_fornecedor_padrao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(BASE, json={"name": "Tri estado"})
        url = f"{BASE}/{criado.json()['id']}"

        troca = await client_with_db.patch(url, json={"omie_default_supplier_code": 77})
        assert troca.json()["omie_default_supplier_code"] == 77

        mantem = await client_with_db.patch(url, json={"omie_department_required": True})
        assert mantem.status_code == 200, mantem.text
        assert mantem.json()["omie_default_supplier_code"] == 77
        assert mantem.json()["omie_department_required"] is True

        limpa = await client_with_db.patch(url, json={"omie_default_supplier_code": None})
        assert limpa.json()["omie_default_supplier_code"] is None
        assert limpa.json()["omie_department_required"] is True

    @pytest.mark.parametrize("codigo", [0, -5])
    async def test_codigo_nao_positivo_e_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession, codigo: int
    ) -> None:
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(BASE, json={"name": f"Invalido {codigo}"})
        resp = await client_with_db.patch(
            f"{BASE}/{criado.json()['id']}", json={"omie_default_supplier_code": codigo}
        )
        assert resp.status_code == 400, resp.text

    async def test_o_banco_recusa_codigo_nao_positivo(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O CHECK é a defesa que sobra se um caminho futuro pular o schema."""
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(BASE, json={"name": "Check do banco"})
        client_id = criado.json()["id"]
        linha = (
            await db_session.execute(select(Client).where(Client.name == "Check do banco"))
        ).scalar_one()
        assert str(linha.id) == client_id
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text("UPDATE clients SET omie_default_supplier_code = 0 WHERE id = :id"),
                    {"id": linha.id},
                )
