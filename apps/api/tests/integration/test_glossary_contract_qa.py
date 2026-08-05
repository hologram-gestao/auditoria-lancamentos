"""Integração (QA da Sprint 6) — as CHAVES do JSON do glossário, literalmente.

Por que este arquivo existe, se a BACK 06.3 já tem 22 testes de rota: nenhum
deles olha o **nome da chave no fio**. Eles leem `data["entries"][0]["name"]` —
o que passa igual se o campo mudar de alias — e o único campo do glossário com
alias é justamente o que sinaliza dado corrompido:

    `GlossaryEntryResponse.decrypt_failed = Field(..., alias="decryptFailed")`

O front lê `entry.decryptFailed` (`lib/contracts/index.ts`, `glossary-screen.tsx`).
Se o alias cair — `populate_by_name` removido, `response_model_by_alias=False`
num refactor, o campo redigitado sem `Field(...)` — a resposta passa a trazer
`decrypt_failed`, o front lê `undefined` (falsy) e a badge "Indecifrável"
**some sem erro nenhum**: a entrada corrompida vira uma linha aparentemente
normal com o texto `[indecifrável]`. É a "célula silenciosamente vazia" que o
CLAUDE.md §4.1 proíbe, na forma mais difícil de perceber — sem 500, sem log,
sem teste vermelho.

`schema.ts` regenerado protege o TIPO, não o comportamento em runtime: o gate de
contrato compara o arquivo gerado com a OpenAPI, e a OpenAPI é montada pelo mesmo
`by_alias` que a serialização — os dois regridem juntos, em silêncio.

Cobre os 4 verbos porque o envelope de cada um é diferente (`{data:{entries,
version}, pagination}` na lista, `{data:<entrada>}` no POST/PATCH,
`{data:{id,deleted,version}}` no DELETE) e o front desempacota conforme o número
de chaves (`rawFetch` só desembrulha quando `data` é a ÚNICA chave).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientGlossaryEntry,
    User,
    UserRole,
    UserScope,
)
from app.modules.glossary.schemas import UNDECIPHERABLE

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
GERENTE_EMAIL = "qa-gerente@austral.com.br"
ADMIN_EMAIL = "qa-admin-glossario@hologram.com.br"

#: Campos textuais do contrato, em snake_case no fio. Se um deles ganhar alias
#: camelCase no futuro, este teste fica vermelho e o front é ajustado JUNTO —
#: que é o ponto: contrato misto só é seguro se for verificado.
_SNAKE_FIELDS = ("id", "kind", "code", "name", "description")


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    user = User(
        name="QA Glossario",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, name: str, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("k", hex_key)
    ct_secret, iv_secret = encrypt("s", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


@pytest.fixture
async def tenant(db_session: AsyncSession) -> dict[str, Any]:
    admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
    client = await _seed_client(db_session, name="Austral QA Contrato", creator=admin)
    await _seed_user(
        db_session,
        email=GERENTE_EMAIL,
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=client.id,
    )
    return {"admin": admin, "client": client}


async def _login(http: AsyncClient, email: str) -> None:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD})
    assert resp.status_code == 200, resp.text


def _url(client_id: UUID, entry_id: UUID | None = None) -> str:
    base = f"/api/v1/clients/{client_id}/glossary"
    return base if entry_id is None else f"{base}/{entry_id}"


def _assert_entry_keys(entry: dict[str, Any]) -> None:
    """A entrada no fio tem EXATAMENTE as chaves que o front lê."""
    assert "decryptFailed" in entry, (
        "o front lê `entry.decryptFailed` (lib/contracts/index.ts); sem o alias "
        "camelCase ele recebe undefined e a badge 'Indecifrável' some em silêncio"
    )
    assert "decrypt_failed" not in entry, (
        "alias camelCase perdido: a resposta voltou a serializar pelo nome do campo"
    )
    for field in _SNAKE_FIELDS:
        assert field in entry, f"campo `{field}` sumiu do contrato da entrada"
    assert set(entry) == {*_SNAKE_FIELDS, "decryptFailed"}, (
        f"chaves inesperadas no contrato da entrada: {sorted(set(entry))}"
    )


class TestChavesDoContratoNoFio:
    async def test_post_e_patch_devolvem_a_entrada_com_decryptfailed_camelcase(
        self, client_with_db: AsyncClient, tenant: dict[str, Any]
    ) -> None:
        client: Client = tenant["client"]
        await _login(client_with_db, GERENTE_EMAIL)

        created = await client_with_db.post(
            _url(client.id),
            json={"kind": "categoria", "code": "3.1.02", "name": "Taxas bancarias"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert set(body) == {"data"}, "POST responde `{data}` (chave única — o front desembrulha)"
        _assert_entry_keys(body["data"])
        assert body["data"]["decryptFailed"] is False

        entry_id = UUID(body["data"]["id"])
        edited = await client_with_db.patch(
            _url(client.id, entry_id),
            json={"kind": "categoria", "code": "3.1.02", "name": "Taxas e tarifas"},
        )
        assert edited.status_code == 200, edited.text
        assert set(edited.json()) == {"data"}
        _assert_entry_keys(edited.json()["data"])

    async def test_lista_mantem_envelope_de_duas_chaves_e_o_alias(
        self, client_with_db: AsyncClient, tenant: dict[str, Any]
    ) -> None:
        """`{data:{entries,version}, pagination}` — duas chaves, sem auto-unwrap."""
        client: Client = tenant["client"]
        await _login(client_with_db, GERENTE_EMAIL)
        await client_with_db.post(
            _url(client.id), json={"kind": "regra", "name": "IOF nunca e juros."}
        )

        listed = await client_with_db.get(f"{_url(client.id)}?page=1&pageSize=20")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert set(body) == {"data", "pagination"}, (
            "envelope de DUAS chaves: `rawFetch` só desembrulha quando `data` é a única"
        )
        assert set(body["data"]) == {"entries", "version"}
        _assert_entry_keys(body["data"]["entries"][0])

    async def test_delete_devolve_id_deleted_e_versao_nova(
        self, client_with_db: AsyncClient, tenant: dict[str, Any]
    ) -> None:
        client: Client = tenant["client"]
        await _login(client_with_db, GERENTE_EMAIL)
        created = await client_with_db.post(
            _url(client.id), json={"kind": "fornecedor", "name": "Moinho Prado"}
        )
        entry_id = UUID(created.json()["data"]["id"])
        versao_antes = created.json()["data"]  # só para garantir que o POST veio íntegro
        assert versao_antes["name"] == "Moinho Prado"

        removed = await client_with_db.delete(_url(client.id, entry_id))
        assert removed.status_code == 200, removed.text
        assert set(removed.json()) == {"data"}
        payload = removed.json()["data"]
        assert set(payload) == {"id", "deleted", "version"}
        assert payload["deleted"] is True
        assert payload["version"] >= 1, (
            "a remoção também bump a versão (invalida o cache do prompt)"
        )


class TestEntradaCorrompidaChegaSinalizadaNaTela:
    async def test_decryptfailed_true_e_placeholder_no_fio(
        self, client_with_db: AsyncClient, db_session: AsyncSession, tenant: dict[str, Any]
    ) -> None:
        """O caminho HTTP completo do dado corrompido — o que a tela realmente recebe.

        A BACK 06.2 cobre isto no `load_glossary_snapshot` (camada de serviço).
        Aqui o que se prova é o **fio**: `decryptFailed: true` (camelCase) +
        `[indecifrável]` no campo. É a combinação que faz a badge aparecer; sem
        o alias, a linha volta como se estivesse íntegra.
        """
        client: Client = tenant["client"]
        await _login(client_with_db, GERENTE_EMAIL)
        created = await client_with_db.post(
            _url(client.id), json={"kind": "regra", "name": "Regra que vai corromper"}
        )
        entry_id = UUID(created.json()["data"]["id"])

        row = await db_session.scalar(
            select(ClientGlossaryEntry).where(ClientGlossaryEntry.id == entry_id)
        )
        assert row is not None
        row.name_encrypted = "v1:k1:deadbeef"
        await db_session.flush()

        listed = await client_with_db.get(_url(client.id))
        assert listed.status_code == 200, listed.text
        entry = listed.json()["data"]["entries"][0]
        _assert_entry_keys(entry)
        assert entry["decryptFailed"] is True
        assert entry["name"] == UNDECIPHERABLE, (
            "campo indecifrável vem com o placeholder, nunca vazio (CLAUDE.md §4.1)"
        )
