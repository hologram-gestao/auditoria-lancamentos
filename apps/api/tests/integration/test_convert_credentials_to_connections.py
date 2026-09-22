"""Conversão das credenciais existentes para `client_connections` (S9, BACK 09.5 — R2).

O critério que reprova a sprint sozinho é **"nenhuma credencial já gravada pode
ficar ilegível"**. Por isso o seed destes testes é diferente de todos os outros:
o ciphertext é escrito **direto na linha, ANTES**, nos dois formatos que a base
real tem —

    - **bare legado**: cifrado com a chave global, SEM AAD (pré-Sprint 3);
    - **`v1:`**: DEK do cliente + AAD com o locator ANTIGO.

Cliente criado pela API não serve aqui: ele já nasce no caminho novo, e o teste
ficaria verde exatamente no cenário que não interessa.

Isolamento: UMA conexão com transação externa + `async_sessionmaker` em
`create_savepoint`, então os `commit()` por lote do script viram savepoints e o
`rollback()` final desfaz tudo (mesma estratégia de
`test_rotate_encryption_key.py`).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from scripts.convert_credentials_to_connections import run_conversion, run_verify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import ClientCipher, encrypt
from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    load_client_cipher,
    new_client_dek,
)
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientConnection,
    ConnectionStatus,
    OmieAccountCache,
    User,
    UserRole,
)
from app.modules.client_connections.legacy_fallback import (
    count_pending_conversion,
    effective_fallback_enabled,
    is_synthetic,
    legacy_credentials,
    resolve_origin_connections,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.integration

BARE_KEY = "chave-bare-legada"
BARE_SECRET = "segredo-bare-legado"
V1_KEY = "chave-v1-envelope"
V1_SECRET = "segredo-v1-envelope"


async def _admin(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="Conv Admin",
        email=email,
        password_hash=hash_password("Senh@Forte#1"),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_bare_client(session: AsyncSession, *, creator: User, name: str) -> Client:
    """Cliente PRÉ-Sprint 3: ciphertext bare (chave global, sem AAD), sem DEK."""
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(BARE_KEY, hex_key)
    ct_s, iv_s = encrypt(BARE_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    # A premissa do teste: é bare MESMO.
    assert ClientCipher.is_legacy(client.omie_app_key_encrypted)
    assert client.dek_wrapped is None
    return client


async def _seed_v1_client(session: AsyncSession, *, creator: User, name: str) -> Client:
    """Cliente pós-Sprint 3: envelope `v1:` com DEK e AAD do locator ANTIGO."""
    client_id = uuid4()
    cipher, dek_wrapped = await new_client_dek(client_id, settings=get_settings())
    ct_k, iv_k = cipher.encrypt(V1_KEY, field_locator(AAD_CLIENT_APP_KEY, client_id))
    ct_s, iv_s = cipher.encrypt(V1_SECRET, field_locator(AAD_CLIENT_APP_SECRET, client_id))
    client = Client(
        id=client_id,
        name=name,
        dek_wrapped=dek_wrapped,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    assert not ClientCipher.is_legacy(client.omie_app_key_encrypted)
    return client


async def _decrypt_connection(session: AsyncSession, client: Client) -> dict[str, str]:
    """Decifra a credencial da CONEXÃO, com o locator novo."""
    connection = (
        await session.execute(
            select(ClientConnection).where(ClientConnection.client_id == client.id)
        )
    ).scalar_one()
    cipher = await load_client_cipher(client, settings=get_settings())
    assert connection.credentials_encrypted is not None
    assert connection.credentials_iv is not None
    plaintext: str = cipher.decrypt(
        connection.credentials_encrypted,
        connection.credentials_iv,
        field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
    )
    return dict(json.loads(plaintext))


class TestConversao:
    async def test_bare_e_v1_convertem_e_as_duas_decifram_na_conexao(
        self, db_engine: AsyncEngine
    ) -> None:
        """O teste central do R2: credencial gravada ANTES continua legível."""
        settings = get_settings()
        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as s:
                admin = await _admin(s, email="conv1@hologram.com.br")
                bare = await _seed_bare_client(s, creator=admin, name="Legado bare")
                v1 = await _seed_v1_client(s, creator=admin, name="Legado v1")
                await s.commit()

            stats = await run_conversion(session_factory=factory, settings=settings)
            assert stats.converted == 2
            assert stats.failed_client_ids == []
            # O bare não tinha DEK: a conversão provisiona.
            assert stats.deks_provisioned == 1

            async with factory() as s:
                bare_db = await s.get(Client, bare.id)
                v1_db = await s.get(Client, v1.id)
                assert bare_db is not None
                assert v1_db is not None
                assert await _decrypt_connection(s, bare_db) == {
                    "app_key": BARE_KEY,
                    "app_secret": BARE_SECRET,
                }
                assert await _decrypt_connection(s, v1_db) == {
                    "app_key": V1_KEY,
                    "app_secret": V1_SECRET,
                }
                # As colunas ANTIGAS ficam intactas — salvaguarda de rollback.
                assert bare_db.omie_app_key_encrypted is not None
                assert v1_db.omie_app_key_encrypted is not None
            await outer.rollback()

    async def test_encerrado_nunca_gera_conexao(self, db_engine: AsyncEngine) -> None:
        """Credencial `''` e DEK destruída — não há o que converter (§4.12)."""
        from datetime import UTC, datetime

        settings = get_settings()
        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as s:
                admin = await _admin(s, email="conv2@hologram.com.br")
                encerrado = Client(
                    name="Cliente encerrado",
                    omie_app_key_encrypted="",
                    omie_app_key_iv="",
                    omie_app_secret_encrypted="",
                    omie_app_secret_iv="",
                    active=False,
                    closed_at=datetime.now(UTC),
                    created_by=admin.id,
                )
                s.add(encerrado)
                await s.commit()

            stats = await run_conversion(session_factory=factory, settings=settings)
            assert stats.converted == 0
            assert stats.skipped_closed == 1

            async with factory() as s:
                assert (await s.execute(select(ClientConnection))).scalars().all() == []
            await outer.rollback()

    async def test_segundo_run_converte_zero(self, db_engine: AsyncEngine) -> None:
        """Idempotente: a seleção exclui quem já tem conexão omie."""
        settings = get_settings()
        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as s:
                admin = await _admin(s, email="conv3@hologram.com.br")
                await _seed_bare_client(s, creator=admin, name="Legado")
                await s.commit()

            primeiro = await run_conversion(session_factory=factory, settings=settings)
            segundo = await run_conversion(session_factory=factory, settings=settings)
            assert primeiro.converted == 1
            assert segundo.converted == 0

            async with factory() as s:
                conexoes = (await s.execute(select(ClientConnection))).scalars().all()
                assert len(conexoes) == 1
            await outer.rollback()

    async def test_interrupcao_e_retomada_nao_duplicam(self, db_engine: AsyncEngine) -> None:
        """Simula a interrupção com `batch_size=1`: o 1º run converte um lote,
        o 2º retoma de onde parou, e ninguém ganha conexão duas vezes."""
        settings = get_settings()
        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as s:
                admin = await _admin(s, email="conv4@hologram.com.br")
                for i in range(3):
                    await _seed_bare_client(s, creator=admin, name=f"Legado {i}")
                await s.commit()

            # Lote de 1: cada volta do `while` é um commit — o estado parcial
            # persiste exatamente como persistiria numa interrupção real.
            stats = await run_conversion(session_factory=factory, settings=settings, batch_size=1)
            assert stats.converted == 3

            async with factory() as s:
                conexoes = (await s.execute(select(ClientConnection))).scalars().all()
                assert len(conexoes) == 3
                assert len({c.client_id for c in conexoes}) == 3
                # E nenhuma ficou ilegível.
                for conexao in conexoes:
                    assert conexao.credentials_encrypted is not None
                    assert conexao.status == ConnectionStatus.ATIVA.value
            await outer.rollback()

    async def test_cache_de_contas_passa_a_apontar_para_a_conexao(
        self, db_engine: AsyncEngine
    ) -> None:
        settings = get_settings()
        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as s:
                admin = await _admin(s, email="conv5@hologram.com.br")
                client = await _seed_bare_client(s, creator=admin, name="Com cache")
                s.add(
                    OmieAccountCache(
                        client_id=client.id,
                        omie_conta_id=4214850,
                        name="Sicredi 91263-1",
                        bank_name="Sicredi",
                        account_type="CC",
                    )
                )
                await s.commit()

            stats = await run_conversion(session_factory=factory, settings=settings)
            assert stats.accounts_relinked == 1

            async with factory() as s:
                cache = (await s.execute(select(OmieAccountCache))).scalar_one()
                conexao = (await s.execute(select(ClientConnection))).scalar_one()
                assert cache.connection_id == conexao.id
            await outer.rollback()


class TestVerify:
    async def test_fail_antes_e_pass_depois(self, db_engine: AsyncEngine) -> None:
        settings = get_settings()
        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as s:
                admin = await _admin(s, email="conv6@hologram.com.br")
                await _seed_bare_client(s, creator=admin, name="Pendente")
                await s.commit()

            antes = await run_verify(session_factory=factory)
            assert antes.passed is False
            assert antes.pending == 1
            assert "FAIL" in antes.render()

            await run_conversion(session_factory=factory, settings=settings)

            depois = await run_verify(session_factory=factory)
            assert depois.passed is True
            assert "PASS" in depois.render()
            await outer.rollback()


class TestFallbackDeLeitura:
    async def test_cliente_nao_convertido_opera_com_o_fallback_ligado(
        self, db_session: AsyncSession
    ) -> None:
        """SEQUENCIAMENTO: com a conversão incompleta, o cliente existente NÃO
        recebe 409 — o fallback sintetiza a conexão em memória."""
        settings = get_settings()
        admin = await _admin(db_session, email="fb1@hologram.com.br")
        client = await _seed_bare_client(db_session, creator=admin, name="Não convertido")

        conexoes = await resolve_origin_connections(db_session, client, settings=settings)
        assert len(conexoes) == 1
        assert is_synthetic(conexoes[0])
        assert conexoes[0].status == ConnectionStatus.ATIVA.value
        # E a credencial legada decifra pelo locator ANTIGO.
        creds = await legacy_credentials(client, settings=settings)
        assert creds["app_key"].get_secret_value() == BARE_KEY

        # Nada foi gravado: a conexão sintetizada é só memória.
        gravadas = (
            (
                await db_session.execute(
                    select(ClientConnection).where(ClientConnection.client_id == client.id)
                )
            )
            .scalars()
            .all()
        )
        assert gravadas == []

    async def test_precedencia_a_conexao_vence_as_colunas_antigas(
        self, db_session: AsyncSession
    ) -> None:
        """PRECEDÊNCIA: com conexão real E colunas antigas preenchidas, a
        conexão vence — as colunas nem são olhadas."""
        settings = get_settings()
        admin = await _admin(db_session, email="fb2@hologram.com.br")
        client = await _seed_bare_client(db_session, creator=admin, name="Os dois mundos")
        real = ClientConnection(
            client_id=client.id,
            provider_type="omie",
            label="Omie real",
            status=ConnectionStatus.ATIVA.value,
        )
        db_session.add(real)
        await db_session.flush()

        conexoes = await resolve_origin_connections(db_session, client, settings=settings)
        assert len(conexoes) == 1
        assert conexoes[0].id == real.id
        assert not is_synthetic(conexoes[0])
        assert conexoes[0].label == "Omie real"

    async def test_cliente_da_janela_so_com_conexao_opera(self, db_session: AsyncSession) -> None:
        """O caso que a ORDEM da precedência protege: cliente criado na janela
        (09.4) tem conexão e colunas NULAS. Se o fallback viesse primeiro, ele
        não acharia nada e pararia de operar."""
        settings = get_settings()
        admin = await _admin(db_session, email="fb3@hologram.com.br")
        client = Client(name="Nasceu na janela", active=True, created_by=admin.id)
        db_session.add(client)
        await db_session.flush()
        db_session.add(
            ClientConnection(
                client_id=client.id,
                provider_type="omie",
                label="Omie",
                status=ConnectionStatus.ATIVA.value,
            )
        )
        await db_session.flush()

        conexoes = await resolve_origin_connections(db_session, client, settings=settings)
        assert len(conexoes) == 1
        assert not is_synthetic(conexoes[0])

    async def test_cliente_sem_origem_nenhuma_devolve_lista_vazia(
        self, db_session: AsyncSession
    ) -> None:
        """Sem conexão e sem colunas: quem chamou decide o 409 (taxonomia 09.2)."""
        settings = get_settings()
        admin = await _admin(db_session, email="fb4@hologram.com.br")
        client = Client(name="Sem nada", active=True, created_by=admin.id)
        db_session.add(client)
        await db_session.flush()

        assert await resolve_origin_connections(db_session, client, settings=settings) == []

    async def test_encerrado_nao_sintetiza(self, db_session: AsyncSession) -> None:
        from datetime import UTC, datetime

        settings = get_settings()
        admin = await _admin(db_session, email="fb5@hologram.com.br")
        client = Client(
            name="Encerrado",
            omie_app_key_encrypted="",
            omie_app_key_iv="",
            omie_app_secret_encrypted="",
            omie_app_secret_iv="",
            active=False,
            closed_at=datetime.now(UTC),
            created_by=admin.id,
        )
        db_session.add(client)
        await db_session.flush()

        assert await resolve_origin_connections(db_session, client, settings=settings) == []


class TestPromocaoSoPorVerificacao:
    async def test_flag_off_com_pendente_mantem_o_fallback_ligado_e_alerta(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Desligar a flag NÃO é promoção: com cliente por converter, o fallback
        segue ligado em memória e o alerta de PLANTÃO sai."""
        from app.core import alerting

        settings = get_settings()
        monkeypatch.setattr(settings, "LEGACY_CREDENTIALS_FALLBACK_ENABLED", False)
        enviados: list[alerting.Alert] = []

        async def _fake_send(alert: alerting.Alert, _settings: object) -> object:
            enviados.append(alert)
            return None

        monkeypatch.setattr(alerting, "send_alert", _fake_send)

        admin = await _admin(db_session, email="prom1@hologram.com.br")
        client = await _seed_bare_client(db_session, creator=admin, name="Pendente")

        assert await count_pending_conversion(db_session) == 1
        assert await effective_fallback_enabled(db_session, settings) is True
        assert [a.code for a in enviados] == [alerting.AlertCode.LEGACY_FALLBACK]
        # O alerta não carrega credencial nem nome de cliente (§4.7).
        assert BARE_KEY not in enviados[0].message
        assert "Pendente" not in enviados[0].message

        # E o cliente continua operando.
        conexoes = await resolve_origin_connections(db_session, client, settings=settings)
        assert len(conexoes) == 1

    async def test_flag_off_com_contagem_zerada_promove_de_verdade(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "LEGACY_CREDENTIALS_FALLBACK_ENABLED", False)

        assert await count_pending_conversion(db_session) == 0
        assert await effective_fallback_enabled(db_session, settings) is False

    async def test_promovido_ninguem_le_as_colunas_antigas(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Teste DIRIGIDO (não grep): com o fallback promovido, um cliente com
        conexão real e **lixo** nas colunas antigas opera normalmente — prova de
        que ninguém as lê."""
        settings = get_settings()
        monkeypatch.setattr(settings, "LEGACY_CREDENTIALS_FALLBACK_ENABLED", False)

        admin = await _admin(db_session, email="prom3@hologram.com.br")
        client = Client(
            name="Convertido com lixo nas colunas",
            omie_app_key_encrypted="LIXO-QUE-NAO-DECIFRA",
            omie_app_key_iv="000000000000000000000000",
            omie_app_secret_encrypted="LIXO-QUE-NAO-DECIFRA",
            omie_app_secret_iv="000000000000000000000000",
            active=True,
            created_by=admin.id,
        )
        db_session.add(client)
        await db_session.flush()
        real = ClientConnection(
            client_id=client.id,
            provider_type="omie",
            label="Omie",
            status=ConnectionStatus.ATIVA.value,
        )
        db_session.add(real)
        await db_session.flush()

        # `count_pending_conversion` não o conta: ele já tem conexão omie.
        assert await count_pending_conversion(db_session) == 0
        conexoes = await resolve_origin_connections(db_session, client, settings=settings)
        assert [c.id for c in conexoes] == [real.id]
