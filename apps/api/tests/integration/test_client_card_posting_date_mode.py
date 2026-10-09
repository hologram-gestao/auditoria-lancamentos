"""Processo de lançamento do cartão por cliente (86e3n70p0) — criação, leitura, PATCH.

O modo é DECLARADO (nunca inferido): `purchase_date` é o default de todo cliente
(o processo de sempre), `invoice_due_date` é o da Prospecta. A escrita é a do
`edit_client` (admin); o que estes testes travam é o contrato do campo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import Client, User, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
ADMIN_EMAIL = "admin-card-mode@hologram.com.br"
BASE = "/api/v1/clients"


async def _login_admin(client: AsyncClient, session: AsyncSession) -> User:
    admin = User(
        name="Admin Cartão",
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


class TestCardPostingDateModeDoCliente:
    async def test_cliente_nasce_na_data_da_compra(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)

        criado = await client_with_db.post(BASE, json={"name": "Sem modo declarado"})

        assert criado.status_code == 201, criado.text
        assert criado.json()["card_posting_date_mode"] == "purchase_date"

    async def test_cliente_nasce_no_vencimento_quando_declarado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)

        criado = await client_with_db.post(
            BASE, json={"name": "Prospecta", "card_posting_date_mode": "invoice_due_date"}
        )

        assert criado.status_code == 201, criado.text
        body = criado.json()
        assert body["card_posting_date_mode"] == "invoice_due_date"
        linha = (
            await db_session.execute(select(Client).where(Client.name == "Prospecta"))
        ).scalar_one()
        assert linha.card_posting_date_mode == "invoice_due_date"

        detalhe = await client_with_db.get(f"{BASE}/{body['id']}")
        assert detalhe.status_code == 200, detalhe.text
        assert detalhe.json()["card_posting_date_mode"] == "invoice_due_date"

    async def test_patch_troca_e_omitido_mantem(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(BASE, json={"name": "Troca de processo"})
        url = f"{BASE}/{criado.json()['id']}"

        troca = await client_with_db.patch(url, json={"card_posting_date_mode": "invoice_due_date"})
        assert troca.status_code == 200, troca.text
        assert troca.json()["card_posting_date_mode"] == "invoice_due_date"

        mantem = await client_with_db.patch(url, json={"name": "Renomeado"})
        assert mantem.status_code == 200, mantem.text
        assert mantem.json()["card_posting_date_mode"] == "invoice_due_date"

        volta = await client_with_db.patch(url, json={"card_posting_date_mode": "purchase_date"})
        assert volta.json()["card_posting_date_mode"] == "purchase_date"

    async def test_valor_fora_do_vocabulario_e_400_e_nada_muda(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        criado = await client_with_db.post(BASE, json={"name": "Vocabulário fechado"})
        url = f"{BASE}/{criado.json()['id']}"

        ruim = await client_with_db.patch(url, json={"card_posting_date_mode": "data_da_fatura"})
        assert ruim.status_code == 400, ruim.text
        assert ruim.json()["error"]["code"] == "VALIDATION_ERROR"

        ruim_post = await client_with_db.post(
            BASE, json={"name": "Outro", "card_posting_date_mode": "vencimento"}
        )
        assert ruim_post.status_code == 400, ruim_post.text

        detalhe = await client_with_db.get(url)
        assert detalhe.json()["card_posting_date_mode"] == "purchase_date"
