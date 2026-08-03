"""Integração — modelo de dados do glossário por tenant (Sprint 6, BACK 06.2).

Cobre os critérios de aceite da task:

    - As TRÊS formas do PRD (categoria com código/nome + descrição, fornecedor
      típico, regra de auditoria em texto) cabem na estrutura.
    - Os campos textuais estão CIFRADOS na tabela: nada identificável do cliente
      final em claro, e o ciphertext de um cliente NÃO decifra no outro (o AAD
      amarra ao par cliente+linha).
    - `clients.glossary_version` muda em criação, edição **E remoção**.
    - Leitura/escrita filtram por `client_id`: forjar o tenant não devolve
      entrada alheia (nem para usuário de cliente, nem por PK).
    - Ordem determinística da leitura consumida pela BACK 06.4.
    - Entrada indecifrável vira `[indecifrável]` + `decrypt_failed=True`, nunca
      célula silenciosamente vazia.

O round-trip da migration (`upgrade → downgrade → upgrade`) está em
`test_migrations.py::TestGlossarioRoundTrip`, junto das demais.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser
from app.core.config import get_settings
from app.core.crypto import ClientCipher, encrypt
from app.core.crypto_service import (
    AAD_GLOSSARY_NAME,
    field_locator,
    provision_client_cipher,
)
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientGlossaryEntry,
    GlossaryEntryKind,
    User,
    UserRole,
    UserScope,
)
from app.modules.glossary.repository import ClientGlossaryRepository
from app.modules.glossary.schemas import UNDECIPHERABLE
from app.modules.glossary.service import (
    apply_entry_edit,
    build_entry,
    load_glossary_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

#: Textos identificáveis do cliente final — é exatamente isso que o §4.5 proíbe
#: persistir em claro, e o que o teste procura na tabela.
CATEGORIA_NOME = "Taxas bancárias"
CATEGORIA_CODIGO = "3.1.02"
CATEGORIA_USO = "Tarifas e encargos cobrados pelo banco, nunca juros de empréstimo."
FORNECEDOR_NOME = "Moinho Prado Ltda"
REGRA_TEXTO = "IOF nunca é classificado como juros."


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="Glossario Admin",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
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


async def _tenant(session: AsyncSession, *, name: str, email: str) -> tuple[Client, ClientCipher]:
    """Cliente + `ClientCipher` com DEK provisionada (envelope v1)."""
    creator = await _seed_user(session, email=email)
    client = await _seed_client(session, name=name, creator=creator)
    cipher = await provision_client_cipher(client, settings=get_settings())
    await session.flush()
    return client, cipher


def _client_user(client_id: UUID, *, role: UserRole = UserRole.CLIENT_MANAGER) -> CurrentUser:
    """Usuário `scope='client'` — o `scoped_by_tenant` força o tenant DELE."""
    return CurrentUser(
        id=str(uuid4()),
        email="operador@cliente.com.br",
        name="Operador",
        role=role.value,
        scope=UserScope.CLIENT.value,
        client_id=client_id,
    )


def _admin_user() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="admin@hologram.com.br",
        name="Admin",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
    )


async def _seed_three_kinds(
    session: AsyncSession, *, client: Client, cipher: ClientCipher
) -> Sequence[ClientGlossaryEntry]:
    repo = ClientGlossaryRepository(session)
    entries = [
        build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.CATEGORIA,
            name=CATEGORIA_NOME,
            code=CATEGORIA_CODIGO,
            description=CATEGORIA_USO,
            cipher=cipher,
        ),
        build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.FORNECEDOR,
            name=FORNECEDOR_NOME,
            code=None,
            description=None,
            cipher=cipher,
        ),
        build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.REGRA,
            name=REGRA_TEXTO,
            code=None,
            description=None,
            cipher=cipher,
        ),
    ]
    for entry in entries:
        await repo.add(entry)
    await repo.bump_version(client_id=client.id)
    return entries


class TestEstruturaDasTresFormas:
    async def test_categoria_fornecedor_e_regra_cabem_na_estrutura(
        self, db_session: AsyncSession
    ) -> None:
        client, cipher = await _tenant(db_session, name="Austral", email="g1@hologram.com.br")
        await _seed_three_kinds(db_session, client=client, cipher=cipher)

        snapshot = await load_glossary_snapshot(db_session, client_id=client.id, cipher=cipher)

        by_kind = {e.kind: e for e in snapshot.entries}
        assert set(by_kind) == set(GlossaryEntryKind)
        categoria = by_kind[GlossaryEntryKind.CATEGORIA]
        assert (categoria.code, categoria.name, categoria.description) == (
            CATEGORIA_CODIGO,
            CATEGORIA_NOME,
            CATEGORIA_USO,
        )
        assert by_kind[GlossaryEntryKind.FORNECEDOR].name == FORNECEDOR_NOME
        assert by_kind[GlossaryEntryKind.REGRA].name == REGRA_TEXTO
        assert all(not e.decrypt_failed for e in snapshot.entries)

    async def test_ordem_da_leitura_e_deterministica(self, db_session: AsyncSession) -> None:
        """Condição do cache-hit do prefixo na BACK 06.4."""
        client, cipher = await _tenant(db_session, name="Ordem", email="g2@hologram.com.br")
        await _seed_three_kinds(db_session, client=client, cipher=cipher)

        first = await load_glossary_snapshot(db_session, client_id=client.id, cipher=cipher)
        second = await load_glossary_snapshot(db_session, client_id=client.id, cipher=cipher)

        assert [e.id for e in first.entries] == [e.id for e in second.entries]
        assert [e.kind for e in first.entries] == sorted(e.kind for e in first.entries)


class TestCriptografiaDosCamposTextuais:
    async def test_nada_identificavel_em_claro_na_tabela(self, db_session: AsyncSession) -> None:
        """CLAUDE.md §4.5: nome de categoria/fornecedor e regra NÃO ficam em claro."""
        client, cipher = await _tenant(db_session, name="Cripto", email="g3@hologram.com.br")
        await _seed_three_kinds(db_session, client=client, cipher=cipher)

        rows = await db_session.execute(
            text(
                "SELECT kind, code_encrypted, name_encrypted, description_encrypted "
                "FROM client_glossary_entries WHERE client_id = :cid"
            ),
            {"cid": str(client.id)},
        )
        blob = " ".join(str(value) for row in rows.all() for value in row)

        for plaintext in (
            CATEGORIA_NOME,
            CATEGORIA_CODIGO,
            CATEGORIA_USO,
            FORNECEDOR_NOME,
            REGRA_TEXTO,
        ):
            assert plaintext not in blob, f"{plaintext!r} vazou em claro na tabela"
        # `kind` é o único texto em claro — enum do sistema, não dado do cliente.
        assert "categoria" in blob

    async def test_iv_novo_a_cada_operacao(self, db_session: AsyncSession) -> None:
        """Mesmo texto, duas entradas → IV e ciphertext diferentes (§4.2)."""
        client, cipher = await _tenant(db_session, name="IV", email="g4@hologram.com.br")
        repo = ClientGlossaryRepository(db_session)
        a = build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.REGRA,
            name=REGRA_TEXTO,
            code=None,
            description=None,
            cipher=cipher,
        )
        b = build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.REGRA,
            name=REGRA_TEXTO,
            code=None,
            description=None,
            cipher=cipher,
        )
        await repo.add(a)
        await repo.add(b)

        assert a.name_iv != b.name_iv
        assert a.name_encrypted != b.name_encrypted

    async def test_ciphertext_de_um_cliente_nao_decifra_no_outro(
        self, db_session: AsyncSession
    ) -> None:
        """O AAD amarra o ciphertext ao par (cliente, linha) — R1 da S3."""
        austral, austral_cipher = await _tenant(
            db_session, name="Austral", email="g5a@hologram.com.br"
        )
        _fulana, fulana_cipher = await _tenant(
            db_session, name="Fulana", email="g5b@hologram.com.br"
        )
        repo = ClientGlossaryRepository(db_session)
        entry = build_entry(
            client_id=austral.id,
            kind=GlossaryEntryKind.FORNECEDOR,
            name=FORNECEDOR_NOME,
            code=None,
            description=None,
            cipher=austral_cipher,
        )
        await repo.add(entry)

        # O cipher da Fulana sobre a linha do Austral: falha, não devolve texto.
        with pytest.raises(Exception):  # noqa: B017, PT011 — qualquer falha serve; o proibido é decifrar
            fulana_cipher.decrypt(
                entry.name_encrypted,
                entry.name_iv,
                field_locator(AAD_GLOSSARY_NAME, entry.id),
            )

    async def test_entrada_indecifravel_vira_placeholder_e_nao_celula_vazia(
        self, db_session: AsyncSession
    ) -> None:
        """CLAUDE.md §4.1 — falha de decrypt é VISÍVEL, com log `glossary_decrypt_failed`."""
        client, cipher = await _tenant(db_session, name="Corrompido", email="g6@hologram.com.br")
        repo = ClientGlossaryRepository(db_session)
        entry = build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.REGRA,
            name=REGRA_TEXTO,
            code=None,
            description=None,
            cipher=cipher,
        )
        await repo.add(entry)
        # Corrompe o ciphertext (simula chave trocada / payload danificado).
        entry.name_encrypted = "v1:k1:deadbeef"
        await db_session.flush()

        snapshot = await load_glossary_snapshot(db_session, client_id=client.id, cipher=cipher)

        assert len(snapshot.entries) == 1
        assert snapshot.entries[0].name == UNDECIPHERABLE
        assert snapshot.entries[0].decrypt_failed is True


class TestMarcadorDeVersao:
    async def test_versao_muda_na_criacao_edicao_e_remocao(self, db_session: AsyncSession) -> None:
        """Os TRÊS casos — o delete é o que `MAX(updated_at)` não pegaria."""
        client, cipher = await _tenant(db_session, name="Versao", email="g7@hologram.com.br")
        repo = ClientGlossaryRepository(db_session)
        assert await repo.get_version(client_id=client.id) == 0

        entry = build_entry(
            client_id=client.id,
            kind=GlossaryEntryKind.CATEGORIA,
            name=CATEGORIA_NOME,
            code=CATEGORIA_CODIGO,
            description=None,
            cipher=cipher,
        )
        await repo.add(entry)
        after_create = await repo.bump_version(client_id=client.id)

        apply_entry_edit(
            entry,
            kind=GlossaryEntryKind.CATEGORIA,
            name="Taxas e tarifas",
            code=CATEGORIA_CODIGO,
            description=None,
            cipher=cipher,
        )
        await db_session.flush()
        after_edit = await repo.bump_version(client_id=client.id)

        await repo.soft_delete(entry)
        after_delete = await repo.bump_version(client_id=client.id)

        assert (after_create, after_edit, after_delete) == (1, 2, 3)
        # E o snapshot enxerga a versão nova + a entrada removida some.
        snapshot = await load_glossary_snapshot(db_session, client_id=client.id, cipher=cipher)
        assert snapshot.version == 3
        assert snapshot.is_empty

    async def test_versao_de_um_tenant_nao_mexe_na_do_outro(self, db_session: AsyncSession) -> None:
        austral, _ = await _tenant(db_session, name="Austral", email="g8a@hologram.com.br")
        fulana, _ = await _tenant(db_session, name="Fulana", email="g8b@hologram.com.br")
        repo = ClientGlossaryRepository(db_session)

        await repo.bump_version(client_id=austral.id)
        await repo.bump_version(client_id=austral.id)

        assert await repo.get_version(client_id=austral.id) == 2
        assert await repo.get_version(client_id=fulana.id) == 0


class TestIsolamentoPorTenant:
    async def test_snapshot_de_um_tenant_nunca_traz_entrada_do_outro(
        self, db_session: AsyncSession
    ) -> None:
        austral, austral_cipher = await _tenant(
            db_session, name="Austral", email="g9a@hologram.com.br"
        )
        fulana, fulana_cipher = await _tenant(
            db_session, name="Fulana", email="g9b@hologram.com.br"
        )
        await _seed_three_kinds(db_session, client=austral, cipher=austral_cipher)

        snapshot = await load_glossary_snapshot(
            db_session, client_id=fulana.id, cipher=fulana_cipher
        )

        assert snapshot.is_empty

    async def test_usuario_de_cliente_forjando_client_id_nao_le_o_outro_tenant(
        self, db_session: AsyncSession
    ) -> None:
        """`scoped_by_tenant` força o tenant da LINHA por cima do alvo pedido."""
        austral, austral_cipher = await _tenant(
            db_session, name="Austral", email="g10a@hologram.com.br"
        )
        fulana, _ = await _tenant(db_session, name="Fulana", email="g10b@hologram.com.br")
        await _seed_three_kinds(db_session, client=austral, cipher=austral_cipher)
        atacante = _client_user(fulana.id)  # usuário DA Fulana...

        repo = ClientGlossaryRepository(db_session)
        # ...pedindo explicitamente o glossário do Austral.
        entries = await repo.list_all_active(client_id=austral.id, user=atacante)
        page, total = await repo.list_page(
            client_id=austral.id, user=atacante, page=1, page_size=20
        )

        assert entries == []
        assert (page, total) == ([], 0)

    async def test_detalhe_por_pk_de_outro_tenant_devolve_none(
        self, db_session: AsyncSession
    ) -> None:
        """404 na rota, nunca o dado — mesmo conhecendo o UUID da entrada."""
        austral, austral_cipher = await _tenant(
            db_session, name="Austral", email="g11a@hologram.com.br"
        )
        fulana, _ = await _tenant(db_session, name="Fulana", email="g11b@hologram.com.br")
        alvo = (await _seed_three_kinds(db_session, client=austral, cipher=austral_cipher))[0]

        repo = ClientGlossaryRepository(db_session)
        found = await repo.get_active(
            entry_id=alvo.id, client_id=austral.id, user=_client_user(fulana.id)
        )

        assert found is None

    async def test_admin_le_o_tenant_alvo_e_nada_alem_dele(self, db_session: AsyncSession) -> None:
        """`scoped_by_tenant` é no-op para `system`; o filtro por `client_id` não é."""
        austral, austral_cipher = await _tenant(
            db_session, name="Austral", email="g12a@hologram.com.br"
        )
        fulana, fulana_cipher = await _tenant(
            db_session, name="Fulana", email="g12b@hologram.com.br"
        )
        await _seed_three_kinds(db_session, client=austral, cipher=austral_cipher)
        await _seed_three_kinds(db_session, client=fulana, cipher=fulana_cipher)

        repo = ClientGlossaryRepository(db_session)
        do_austral = await repo.list_all_active(client_id=austral.id, user=_admin_user())

        assert len(do_austral) == 3
        assert {e.client_id for e in do_austral} == {austral.id}


class TestSoftDelete:
    async def test_remocao_e_logica_e_some_da_listagem(self, db_session: AsyncSession) -> None:
        client, cipher = await _tenant(db_session, name="SoftDel", email="g13@hologram.com.br")
        repo = ClientGlossaryRepository(db_session)
        entries = list(await _seed_three_kinds(db_session, client=client, cipher=cipher))

        await repo.soft_delete(entries[0])

        assert await repo.count_active(client_id=client.id) == 2
        # A linha continua na tabela (DELETE físico é proibido).
        row = await db_session.scalar(
            select(ClientGlossaryEntry).where(ClientGlossaryEntry.id == entries[0].id)
        )
        assert row is not None
        assert row.deleted_at is not None
