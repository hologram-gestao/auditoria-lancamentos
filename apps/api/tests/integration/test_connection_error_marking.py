"""Marcar conexão como `erro` NÃO apaga a credencial (Sprint 9, BACK 09.2).

O defeito que este arquivo existe para impedir: "credencial recusada" virar
"credencial perdida". Se o `UPDATE` de estado levasse as colunas cifradas
junto, a mensagem ao usuário deixaria de ser "atualize a senha" e passaria a
ser "refaça a conexão" — e o ciphertext, que ainda decifra, morreria por nada.

A prova é byte a byte: o mesmo envelope antes e depois do flip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto_service import (
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    new_client_dek,
)
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientConnection,
    ConnectionStatus,
    ProviderType,
    User,
    UserRole,
)
from app.modules.client_connections.repository import ClientConnectionRepository
from app.modules.client_connections.schemas import ClientConnectionResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
CREDENTIALS_JSON = '{"app_key": "chave-secreta", "app_secret": "segredo-secreto"}'


async def _seed_client_with_connection(
    session: AsyncSession, *, email: str
) -> tuple[Client, ClientConnection]:
    """Cliente SEM credencial nas colunas antigas (o mundo da Sprint 9) + 1 conexão cifrada."""
    creator = User(
        name="Admin",
        email=email,
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(creator)
    await session.flush()

    settings = get_settings()
    client = Client(name="Sem origem", active=True, created_by=creator.id)
    session.add(client)
    await session.flush()

    cipher, dek_wrapped = await new_client_dek(client.id, settings=settings)
    client.dek_wrapped = dek_wrapped

    connection = ClientConnection(
        client_id=client.id,
        provider_type=ProviderType.OMIE.value,
        label="Omie",
        status=ConnectionStatus.ATIVA.value,
    )
    session.add(connection)
    # A pk entra no AAD, então precisa existir ANTES de cifrar (padrão da §4.1).
    await session.flush()
    envelope, iv = cipher.encrypt(
        CREDENTIALS_JSON, field_locator(AAD_CONNECTION_CREDENTIALS, connection.id)
    )
    connection.credentials_encrypted = envelope
    connection.credentials_iv = iv
    await session.flush()
    return client, connection


class TestMarcarErroPreservaCredencial:
    async def test_flip_de_estado_com_ciphertext_identico(self, db_session: AsyncSession) -> None:
        _, connection = await _seed_client_with_connection(
            db_session, email="conn-erro@hologram.com.br"
        )
        antes_ct = connection.credentials_encrypted
        antes_iv = connection.credentials_iv
        assert antes_ct is not None
        assert antes_iv is not None

        repo = ClientConnectionRepository(db_session)
        changed = await repo.mark_connection_error(
            connection.id, at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
        )
        assert changed is True
        await db_session.refresh(connection)

        assert connection.status == ConnectionStatus.ERRO.value
        assert connection.last_checked_at is not None
        # Byte a byte: nada foi apagado nem re-cifrado.
        assert connection.credentials_encrypted == antes_ct
        assert connection.credentials_iv == antes_iv

    async def test_credencial_continua_decifravel_depois_do_erro(
        self, db_session: AsyncSession
    ) -> None:
        """A prova de que o envelope preservado ainda SERVE — não só é igual."""
        from app.core.crypto_service import load_client_cipher

        client, connection = await _seed_client_with_connection(
            db_session, email="conn-erro2@hologram.com.br"
        )
        repo = ClientConnectionRepository(db_session)
        await repo.mark_connection_error(connection.id)
        await db_session.refresh(connection)

        cipher = await load_client_cipher(client, settings=get_settings())
        assert connection.credentials_encrypted is not None
        assert connection.credentials_iv is not None
        decifrado = cipher.decrypt(
            connection.credentials_encrypted,
            connection.credentials_iv,
            field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
        )
        assert decifrado == CREDENTIALS_JSON

    async def test_marcar_verificada_volta_para_ativa_sem_tocar_na_credencial(
        self, db_session: AsyncSession
    ) -> None:
        _, connection = await _seed_client_with_connection(
            db_session, email="conn-ok@hologram.com.br"
        )
        antes_ct = connection.credentials_encrypted
        repo = ClientConnectionRepository(db_session)

        await repo.mark_connection_error(connection.id)
        await db_session.refresh(connection)
        assert connection.status == ConnectionStatus.ERRO.value

        await repo.mark_connection_checked(connection.id)
        await db_session.refresh(connection)
        assert connection.status == ConnectionStatus.ATIVA.value
        assert connection.credentials_encrypted == antes_ct

    async def test_marcar_duas_vezes_e_convergente(self, db_session: AsyncSession) -> None:
        _, connection = await _seed_client_with_connection(
            db_session, email="conn-idem@hologram.com.br"
        )
        repo = ClientConnectionRepository(db_session)
        at = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

        await repo.mark_connection_error(connection.id, at=at)
        await db_session.refresh(connection)
        primeiro = (connection.status, connection.last_checked_at, connection.credentials_encrypted)

        await repo.mark_connection_error(connection.id, at=at)
        await db_session.refresh(connection)
        segundo = (connection.status, connection.last_checked_at, connection.credentials_encrypted)

        assert primeiro == segundo

    async def test_conexao_inexistente_nao_muda_nada(self, db_session: AsyncSession) -> None:
        repo = ClientConnectionRepository(db_session)
        assert await repo.mark_connection_error(uuid4()) is False


class TestListagemEResposta:
    async def test_lista_em_ordem_deterministica(self, db_session: AsyncSession) -> None:
        client, _ = await _seed_client_with_connection(
            db_session, email="conn-lista@hologram.com.br"
        )
        db_session.add(
            ClientConnection(
                client_id=client.id,
                provider_type=ProviderType.OMIE.value,
                label="Antes de tudo",
            )
        )
        await db_session.flush()

        repo = ClientConnectionRepository(db_session)
        conexoes = await repo.list_for_client(client.id)
        assert [c.label for c in conexoes] == ["Antes de tudo", "Omie"]

    async def test_resposta_traz_capacidades_e_nenhuma_credencial(
        self, db_session: AsyncSession
    ) -> None:
        _, connection = await _seed_client_with_connection(
            db_session, email="conn-resp@hologram.com.br"
        )
        payload = ClientConnectionResponse.from_connection(connection).model_dump_json()

        assert "verificar_credencial" in payload
        assert "escrever" in payload
        # Nem plaintext, nem ciphertext, nem IV.
        assert "chave-secreta" not in payload
        assert "segredo-secreto" not in payload
        assert connection.credentials_encrypted not in payload
        assert connection.credentials_iv not in payload
        assert "credentials" not in payload

    async def test_cliente_sem_conexao_devolve_lista_vazia(self, db_session: AsyncSession) -> None:
        creator = User(
            name="Admin",
            email="conn-vazio@hologram.com.br",
            password_hash=hash_password(PLAIN_PASSWORD),
            role=UserRole.ADMIN.value,
            active=True,
        )
        db_session.add(creator)
        await db_session.flush()
        client = Client(name="Cliente sem origem", active=True, created_by=creator.id)
        db_session.add(client)
        await db_session.flush()

        repo = ClientConnectionRepository(db_session)
        assert await repo.list_for_client(client.id) == []
        # E o `select` ainda é o do cliente certo, não "todas as conexões".
        total = (await db_session.execute(select(ClientConnection))).scalars().all()
        assert all(c.client_id != client.id for c in total)
