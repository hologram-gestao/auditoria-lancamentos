"""Cadastro de cliente SEM origem (Sprint 9, BACK 09.4 — R4).

O que a Sprint 9 existe para tornar possível: o cliente é entidade plena com
**nenhuma** conexão. Cobre:

    - POST sem credencial → 201, cadastro completo, 4 colunas antigas e
      `dek_wrapped` NULL (o teste lê a LINHA, não só a resposta).
    - POST com credencial → conexão `omie` ativa criada pelo MESMO serviço da
      09.3, e as 4 colunas antigas **continuam NULAS** (a credencial mora na
      conexão agora).
    - POST com credencial inválida → 4xx e NENHUMA linha nas duas tabelas.
    - `cliente_criado` emitido nos dois ramos, com as chaves do PRD.
    - GET do detalhe de cliente sem origem → 200, contas vazias, `synced_at`
      nulo e **nenhuma chamada ao provedor** (respx sem rota registrada).
    - `origin_status` na lista e no detalhe, nos três estados.

⚠️ **S-1 é risco ASSUMIDO pelo PRD**, não defeito desta task: "cliente sem
conexão tem valor operacional real". Quem testa isso é a leitura D+30 do evento
— estes testes provam que o cadastro funciona e que o evento sai.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import select

from app.db.models import Client, ClientConnection, ConnectionStatus, UsageEvent, User, UserRole
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX
from app.modules.clients.schemas import OriginStatus

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
ADMIN_EMAIL = "admin-criar@hologram.com.br"
MANAGER_EMAIL = "manager-criar@hologram.com.br"

DEMO_CREDENTIALS = {
    "omie_app_key": f"{FAKE_DEMO_KEY_PREFIX}KEY_9",
    "omie_app_secret": f"{FAKE_DEMO_KEY_PREFIX}SECRET_9",
}
_OMIE_AUTH_FAULT = {
    "faultstring": "SOAP-ENV:Client-101: App Key inválido",
    "faultcode": "SOAP-ENV:Client-101",
}


def _omie_url(module: str, endpoint: str) -> str:
    return f"https://app.omie.com.br/api/v1/{module}/{endpoint}/"


@pytest.fixture(autouse=True)
def _no_mock_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.omie import mock_client

    for attr in (
        "_DELAY_LISTAR_CLIENTES_SECONDS",
        "_DELAY_LISTAR_CONTAS_SECONDS",
        "_DELAY_LISTAR_EXTRATO_SECONDS",
        "_DELAY_LISTAR_TITULOS_SECONDS",
    ):
        monkeypatch.setattr(mock_client, attr, 0.0)


async def _seed_staff(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Staff",
        email=email,
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _login_as(client: AsyncClient, email: str) -> int:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    return resp.status_code


async def _clients(session: AsyncSession) -> list[Client]:
    return list((await session.execute(select(Client))).scalars().all())


async def _connections(session: AsyncSession) -> list[ClientConnection]:
    return list((await session.execute(select(ClientConnection))).scalars().all())


async def _events(session: AsyncSession, name: str) -> list[UsageEvent]:
    rows = await session.execute(select(UsageEvent).where(UsageEvent.event == name))
    return list(rows.scalars().all())


class TestCadastroSemOrigem:
    async def test_201_com_cadastro_completo_e_colunas_antigas_nulas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        resp = await client_with_db.post("/api/v1/clients", json={"name": "Padaria sem sistema"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Padaria sem sistema"
        assert body["active"] is True
        assert body["organization"]["id"]
        assert body["origin_status"] == OriginStatus.SEM_ORIGEM.value

        # A LINHA, não só a resposta: nada de credencial, nada de DEK.
        client = (await _clients(db_session))[0]
        assert client.omie_app_key_encrypted is None
        assert client.omie_app_key_iv is None
        assert client.omie_app_secret_encrypted is None
        assert client.omie_app_secret_iv is None
        assert client.dek_wrapped is None
        assert await _connections(db_session) == []

    async def test_com_categoria_e_responsavel_continua_funcionando(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """ "Pleno" é literal: o cadastro sem origem tem os mesmos campos do com."""
        await _seed_staff(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        assert await _login_as(client_with_db, MANAGER_EMAIL) == 200

        resp = await client_with_db.post("/api/v1/clients", json={"name": "Cliente do gerente"})
        assert resp.status_code == 201, resp.text
        # Gerente que cria vira o RESPONSÁVEL (86e390kz8) — inalterado.
        assert resp.json()["responsible_manager"]["email"] == MANAGER_EMAIL

    async def test_evento_cliente_criado_com_tem_conexao_false(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post("/api/v1/clients", json={"name": "Sem sistema"})
        assert resp.status_code == 201

        eventos = await _events(db_session, "cliente_criado")
        assert len(eventos) == 1
        props = eventos[0].props
        assert props["tem_conexao"] is False
        assert props["tipo_conexao"] is None
        assert props["client_id"] == resp.json()["id"]
        assert props["organization_id"] == resp.json()["organization"]["id"]
        # Só IDs e enums — nome do cliente NÃO entra (§4.7).
        assert "Sem sistema" not in str(props)

    async def test_dois_cadastros_geram_duas_linhas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Sem dedup: colapsar os dois subcontaria a métrica da sprint."""
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (
            await client_with_db.post("/api/v1/clients", json={"name": "Um"})
        ).status_code == 201
        assert (
            await client_with_db.post("/api/v1/clients", json={"name": "Dois"})
        ).status_code == 201

        assert len(await _events(db_session, "cliente_criado")) == 2


class TestCadastroComOrigem:
    async def test_cria_conexao_ativa_e_deixa_as_colunas_antigas_nulas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        resp = await client_with_db.post(
            "/api/v1/clients", json={"name": "Padaria com Omie", **DEMO_CREDENTIALS}
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["origin_status"] == OriginStatus.ATIVA.value

        client = (await _clients(db_session))[0]
        # A credencial mora na CONEXÃO — as colunas antigas continuam nulas.
        assert client.omie_app_key_encrypted is None
        assert client.omie_app_secret_encrypted is None
        # E a DEK foi provisionada pelo serviço de conexão.
        assert client.dek_wrapped is not None

        conexoes = await _connections(db_session)
        assert len(conexoes) == 1
        assert conexoes[0].provider_type == "omie"
        assert conexoes[0].label == "Omie"
        assert conexoes[0].status == ConnectionStatus.ATIVA.value
        assert conexoes[0].credentials_encrypted is not None

    async def test_evento_com_tem_conexao_true_e_tipo(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O ramo "com origem" também emite — é o denominador da leitura."""
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (
            await client_with_db.post(
                "/api/v1/clients", json={"name": "Com Omie", **DEMO_CREDENTIALS}
            )
        ).status_code == 201

        eventos = await _events(db_session, "cliente_criado")
        assert len(eventos) == 1
        assert eventos[0].props["tem_conexao"] is True
        assert eventos[0].props["tipo_conexao"] == "omie"

    @respx.mock
    async def test_credencial_invalida_nao_cria_cliente_nem_conexao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A transação inteira desfaz — o cliente recém-criado some junto."""
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_OMIE_AUTH_FAULT)
        )
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        resp = await client_with_db.post(
            "/api/v1/clients",
            json={
                "name": "Nunca deveria existir",
                "omie_app_key": "chave-recusada",
                "omie_app_secret": "segredo-recusado",
            },
        )
        assert resp.status_code >= 400, resp.text
        assert await _clients(db_session) == []
        assert await _connections(db_session) == []
        assert await _events(db_session, "cliente_criado") == []

    async def test_so_uma_das_credenciais_e_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            "/api/v1/clients", json={"name": "Meia credencial", "omie_app_key": "so-a-chave"}
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert await _clients(db_session) == []

    async def test_nome_ausente_e_400_sem_criar(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post("/api/v1/clients", json={})
        # Convenção da casa: 400 `VALIDATION_ERROR` genérico; o campo que faltou
        # vai só para o log sanitizado, nunca para a resposta (86e2rtxcm).
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert await _clients(db_session) == []


class TestDetalheDeClienteSemOrigem:
    async def test_200_sem_contas_e_sem_tocar_no_provedor(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A exceção deliberada do R6: o detalhe responde 200 SEMPRE.

        Sem `@respx.mock` e sem rota registrada: qualquer chamada HTTP real
        falharia o teste. É a prova de que o detalhe não chama `get_or_sync`.
        """
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criado = await client_with_db.post("/api/v1/clients", json={"name": "Sem origem"})
        client_id = criado.json()["id"]

        resp = await client_with_db.get(f"/api/v1/clients/{client_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["origin_status"] == OriginStatus.SEM_ORIGEM.value
        assert body["accounts"] == []
        assert body["accounts_synced_at"] is None
        assert body["connections"] == []

    async def test_com_conexao_em_erro_o_status_e_erro(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criado = await client_with_db.post(
            "/api/v1/clients", json={"name": "Com Omie", **DEMO_CREDENTIALS}
        )
        client_id = UUID(criado.json()["id"])

        conexao = (await _connections(db_session))[0]
        conexao.status = ConnectionStatus.ERRO.value
        await db_session.flush()

        resp = await client_with_db.get(f"/api/v1/clients/{client_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["origin_status"] == OriginStatus.ERRO.value
        # Conexão em erro não é capaz → o detalhe não bate no provedor.
        assert body["accounts"] == []
        assert body["accounts_synced_at"] is None
        # Mas a conexão APARECE, com o estado, para a tela poder oferecer o
        # "reconectar".
        assert len(body["connections"]) == 1
        assert body["connections"][0]["status"] == "erro"

    async def test_conexao_no_detalhe_nao_traz_credencial(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criado = await client_with_db.post(
            "/api/v1/clients", json={"name": "Com Omie", **DEMO_CREDENTIALS}
        )
        conexao = (await _connections(db_session))[0]

        resp = await client_with_db.get(f"/api/v1/clients/{criado.json()['id']}")
        corpo = resp.text
        assert DEMO_CREDENTIALS["omie_app_key"] not in corpo
        assert DEMO_CREDENTIALS["omie_app_secret"] not in corpo
        assert conexao.credentials_encrypted not in corpo
        assert conexao.credentials_iv not in corpo

    async def test_sync_manual_sem_origem_e_409_acionavel(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Ao contrário do detalhe, o sync é o PEDIDO do usuário: 409, não
        "sincronizado, zero contas"."""
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criado = await client_with_db.post("/api/v1/clients", json={"name": "Sem origem"})

        resp = await client_with_db.patch(f"/api/v1/clients/{criado.json()['id']}/sync-accounts")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "SEM_CONEXAO"


class TestOriginStatusNaLista:
    async def test_tres_estados_aparecem_na_listagem(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A lista do parceiro precisa mostrar quem está sem origem — e sem N+1
        (as contagens vêm em subquery escalar na query que já existia)."""
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        sem = await client_with_db.post("/api/v1/clients", json={"name": "Sem origem"})
        com = await client_with_db.post(
            "/api/v1/clients", json={"name": "Com Omie", **DEMO_CREDENTIALS}
        )
        erro = await client_with_db.post(
            "/api/v1/clients", json={"name": "Com erro", **DEMO_CREDENTIALS}
        )
        assert {sem.status_code, com.status_code, erro.status_code} == {201}

        erro_id = UUID(erro.json()["id"])
        conexao = next(c for c in await _connections(db_session) if c.client_id == erro_id)
        conexao.status = ConnectionStatus.ERRO.value
        await db_session.flush()

        resp = await client_with_db.get("/api/v1/clients?page=1&pageSize=50")
        assert resp.status_code == 200, resp.text
        por_nome = {c["name"]: c["origin_status"] for c in resp.json()["data"]}
        assert por_nome["Sem origem"] == OriginStatus.SEM_ORIGEM.value
        assert por_nome["Com Omie"] == OriginStatus.ATIVA.value
        assert por_nome["Com erro"] == OriginStatus.ERRO.value

    async def test_cliente_com_duas_conexoes_aparece_uma_vez_so(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Subquery escalar e não JOIN: com join, este cliente sairia duplicado."""
        await _seed_staff(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criado = await client_with_db.post(
            "/api/v1/clients", json={"name": "Duas contas", **DEMO_CREDENTIALS}
        )
        client_id = criado.json()["id"]
        segunda = await client_with_db.post(
            f"/api/v1/clients/{client_id}/connections",
            json={
                "provider_type": "omie",
                "label": "Omie filial",
                "credentials": {
                    "app_key": DEMO_CREDENTIALS["omie_app_key"],
                    "app_secret": DEMO_CREDENTIALS["omie_app_secret"],
                },
            },
        )
        assert segunda.status_code == 201, segunda.text

        resp = await client_with_db.get("/api/v1/clients?page=1&pageSize=50")
        nomes = [c["name"] for c in resp.json()["data"]]
        assert nomes.count("Duas contas") == 1
        assert resp.json()["pagination"]["total"] == 1
