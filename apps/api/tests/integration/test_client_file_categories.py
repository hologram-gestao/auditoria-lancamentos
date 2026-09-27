"""O registry de categorias do arquivo contra o banco (Sprint 14 / BACK 14.4 — R4).

O que este módulo afirma:
  - `resolve_codes` com um rótulo novo cria 1 linha e devolve um código que não
    contém nem deriva do rótulo; o MESMO rótulo de novo devolve o MESMO código sem
    criar linha; dois rótulos distintos geram códigos sem relação com o texto;
  - `Aluguel`, `aluguel` e `Aluguél` são TRÊS categorias (nenhum caminho funde);
  - o rótulo persiste só cifrado (o `SELECT` não contém o texto); decifrar com a DEK
    de OUTRO cliente falha; falha de decifragem devolve `[indecifrável]` +
    `categoryNameResolved=false` no de-para, e o caplog só tem IDs;
  - `UNIQUE(client_id, code)` provada no banco;
  - com um movimento `(arquivo, code)` na base, `GET /clients/{id}/mapping/{tipo}`
    lista a linha com `sourceType='arquivo'`, `sem_decisao`, nome = grafia original
    e sem herança; a importação de planilha aceita a chave `(arquivo, code)`;
  - cross-tenant: o mesmo rótulo em dois clientes gera códigos diferentes e o
    registry de A nunca carrega linha de B;
  - encerramento purga (`close_client_purge`); exclusão definitiva leva por CASCADE.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.crypto import CryptoError
from app.core.crypto_service import AAD_FILE_CATEGORY_LABEL, field_locator, load_client_cipher
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientFileCategory,
    ClientMovement,
    MappingDestination,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_file_categories.registry import FileCategoryRegistry
from app.modules.client_mapping.portability import EXPORT_COLUMNS
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Categorias#1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SECRET_LABEL = "Aluguel Sala Comercial Rua das Flores"


async def _user(db: AsyncSession, *, role: UserRole) -> User:
    user = User(
        name="Categorias",
        email=f"fc-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    db.add(user)
    await db.flush()
    return user


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


class World:
    admin: User
    client_a: Client
    client_b: Client
    destination: MappingDestination


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.client_a = Client(name="Cliente A sem ERP", active=True, created_by=w.admin.id)
    w.client_b = Client(name="Cliente B sem ERP", active=True, created_by=w.admin.id)
    db_session.add_all([w.client_a, w.client_b])
    await db_session.flush()
    w.destination = (
        await db_session.execute(
            select(MappingDestination).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == "demonstrativo_contabil",
            )
        )
    ).scalar_one()
    return w


def _registry(db: AsyncSession) -> FileCategoryRegistry:
    return FileCategoryRegistry(db, settings=get_settings())


async def _rows(db: AsyncSession, client_id: Any) -> list[ClientFileCategory]:
    stmt = (
        select(ClientFileCategory)
        .where(ClientFileCategory.client_id == client_id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


def _mov(db: AsyncSession, client: Client, code: str, mid: str = "deadbeef:1") -> None:
    db.add(
        ClientMovement(
            client_id=client.id,
            source_type="arquivo",
            source_movement_id=mid,
            competence=date(2026, 8, 1),
            movement_date=date(2026, 8, 10),
            amount=Decimal("-100.00"),
            category_code=code,
        )
    )


class TestResolveCodes:
    async def test_rotulo_novo_cria_uma_linha_com_codigo_sem_relacao_com_o_texto(
        self, db_session: AsyncSession, world: World
    ) -> None:
        codes = await _registry(db_session).resolve_codes(world.client_a, {"Aluguel", "Energia"})
        assert set(codes) == {"Aluguel", "Energia"}
        assert len(set(codes.values())) == 2
        for label, code in codes.items():
            assert code.startswith("arq-")
            assert label.lower() not in code.lower()
        rows = await _rows(db_session, world.client_a.id)
        assert len(rows) == 2
        # A DEK nasceu com a primeira categoria (cliente sem conexão no seed).
        await db_session.refresh(world.client_a)
        assert world.client_a.dek_wrapped is not None

    async def test_o_mesmo_rotulo_devolve_o_mesmo_codigo_sem_criar_linha(
        self, db_session: AsyncSession, world: World
    ) -> None:
        registry = _registry(db_session)
        primeiro = await registry.resolve_codes(world.client_a, {"Aluguel"})
        segundo = await registry.resolve_codes(world.client_a, {"Aluguel"})
        assert segundo == primeiro
        assert len(await _rows(db_session, world.client_a.id)) == 1

    async def test_grafias_diferentes_sao_tres_categorias(
        self, db_session: AsyncSession, world: World
    ) -> None:
        """Caso negativo do R4: nenhum caminho funde grafias."""
        codes = await _registry(db_session).resolve_codes(
            world.client_a, {"Aluguel", "aluguel", "Aluguél"}
        )
        assert len(set(codes.values())) == 3
        assert len(await _rows(db_session, world.client_a.id)) == 3

    async def test_corrigir_o_acento_no_mes_seguinte_nao_apaga_a_primeira(
        self, db_session: AsyncSession, world: World
    ) -> None:
        registry = _registry(db_session)
        julho = await registry.resolve_codes(world.client_a, {"Aluguél"})
        agosto = await registry.resolve_codes(world.client_a, {"Aluguel"})
        assert julho["Aluguél"] != agosto["Aluguel"]
        # A primeira continua lá, com o MESMO código — a decisão de julho sobrevive.
        de_novo = await registry.resolve_codes(world.client_a, {"Aluguél"})
        assert de_novo == julho

    async def test_vazio_nao_toca_o_banco_nem_a_dek(
        self, db_session: AsyncSession, world: World
    ) -> None:
        assert await _registry(db_session).resolve_codes(world.client_a, set()) == {}
        await db_session.refresh(world.client_a)
        assert world.client_a.dek_wrapped is None


class TestRotuloSoCifrado:
    async def test_select_nao_contem_o_texto_e_decifra_de_volta_com_a_dek_certa(
        self, db_session: AsyncSession, world: World
    ) -> None:
        codes = await _registry(db_session).resolve_codes(world.client_a, {SECRET_LABEL})
        (row,) = await _rows(db_session, world.client_a.id)
        assert SECRET_LABEL not in row.label_encrypted
        assert row.label_encrypted.startswith("v1:")
        assert row.code == codes[SECRET_LABEL]

        await db_session.refresh(world.client_a)
        cipher = await load_client_cipher(world.client_a, settings=get_settings())
        assert (
            cipher.decrypt(
                row.label_encrypted, row.label_iv, field_locator(AAD_FILE_CATEGORY_LABEL, row.id)
            )
            == SECRET_LABEL
        )

    async def test_a_dek_de_outro_cliente_nao_decifra(
        self, db_session: AsyncSession, world: World
    ) -> None:
        registry = _registry(db_session)
        await registry.resolve_codes(world.client_a, {SECRET_LABEL})
        await registry.resolve_codes(world.client_b, {"Outra"})
        (row_a,) = await _rows(db_session, world.client_a.id)
        await db_session.refresh(world.client_b)
        cipher_b = await load_client_cipher(world.client_b, settings=get_settings())
        with pytest.raises(CryptoError):
            cipher_b.decrypt(
                row_a.label_encrypted,
                row_a.label_iv,
                field_locator(AAD_FILE_CATEGORY_LABEL, row_a.id),
            )

    async def test_falha_de_decifragem_e_indecifravel_com_log_so_de_ids(
        self, db_session: AsyncSession, world: World, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry = _registry(db_session)
        codes = await registry.resolve_codes(world.client_a, {SECRET_LABEL})
        (row,) = await _rows(db_session, world.client_a.id)
        # Ciphertext adulterado: a tag do GCM recusa.
        row.label_encrypted = row.label_encrypted[:-8] + "00000000"
        await db_session.flush()

        with caplog.at_level(logging.WARNING):
            resolved = await registry.resolve_names(world.client_a)
        code = codes[SECRET_LABEL]
        assert resolved.names[code] == "[indecifrável]"
        assert code in resolved.failed
        assert "file_category_decrypt_failed" in caplog.text
        assert str(row.id) in caplog.text
        assert SECRET_LABEL not in caplog.text
        assert "Aluguel" not in caplog.text

    async def test_unique_por_cliente_e_codigo_e_do_banco(
        self, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add(
            ClientFileCategory(
                client_id=world.client_a.id,
                code="arq-000000000001",
                label_encrypted="v1:k1:00",
                label_iv="0" * 24,
            )
        )
        await db_session.flush()
        db_session.add(
            ClientFileCategory(
                client_id=world.client_a.id,
                code="arq-000000000001",
                label_encrypted="v1:k1:11",
                label_iv="1" * 24,
            )
        )
        with pytest.raises(IntegrityError) as exc:
            await db_session.flush()
        assert "uq_client_file_categories_client_id_code" in str(exc.value)
        await db_session.rollback()


class TestCrossTenant:
    async def test_o_mesmo_rotulo_em_dois_clientes_gera_codigos_diferentes(
        self, db_session: AsyncSession, world: World
    ) -> None:
        registry = _registry(db_session)
        a = await registry.resolve_codes(world.client_a, {"Aluguel"})
        b = await registry.resolve_codes(world.client_b, {"Aluguel"})
        assert a["Aluguel"] != b["Aluguel"]
        # O registry de A nunca carrega a linha de B: resolver de novo em A não
        # "encontra" a categoria de B.
        assert (await registry.resolve_names(world.client_a)).names == {a["Aluguel"]: "Aluguel"}
        assert (await registry.resolve_names(world.client_b)).names == {b["Aluguel"]: "Aluguel"}


class TestDeParaListaAsCategoriasDoArquivo:
    async def test_linha_arquivo_sem_decisao_com_o_rotulo_e_sem_heranca(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        codes = await _registry(db_session).resolve_codes(world.client_a, {"Aluguél "})
        code = codes["Aluguél "]
        _mov(db_session, world.client_a, code)
        await db_session.flush()

        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(
            f"/api/v1/clients/{world.client_a.id}/mapping/demonstrativo_contabil"
        )
        assert resp.status_code == 200, resp.text
        (item,) = resp.json()["data"]
        assert item["sourceType"] == "arquivo"
        assert item["categoryCode"] == code
        assert item["situation"] == "sem_decisao"
        assert item["categoryName"] == "Aluguél "  # grafia original, sem normalizar
        assert item["categoryNameResolved"] is True
        assert item["originDreCode"] is None

        # "Iniciar de-para" (herança) não cria nada para arquivo: só o plano de contas herda.
        inherit = await client_with_db.post(
            f"/api/v1/clients/{world.client_a.id}/mapping/demonstrativo_contabil/inherit",
            json={},
        )
        assert inherit.status_code == 200, inherit.text
        assert inherit.json()["data"]["created"] == 0

    async def test_indecifravel_no_de_para(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        codes = await _registry(db_session).resolve_codes(world.client_a, {"Aluguel"})
        code = codes["Aluguel"]
        (row,) = await _rows(db_session, world.client_a.id)
        row.label_encrypted = row.label_encrypted[:-8] + "00000000"
        _mov(db_session, world.client_a, code)
        await db_session.flush()

        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(
            f"/api/v1/clients/{world.client_a.id}/mapping/demonstrativo_contabil"
        )
        assert resp.status_code == 200, resp.text
        (item,) = resp.json()["data"]
        assert item["categoryName"] == "[indecifrável]"
        assert item["categoryNameResolved"] is False

    async def test_importacao_de_planilha_aceita_a_chave_arquivo_codigo(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        codes = await _registry(db_session).resolve_codes(world.client_a, {"Aluguel"})
        code = codes["Aluguel"]
        _mov(db_session, world.client_a, code)
        await db_session.flush()

        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.append(list(EXPORT_COLUMNS))
        ws.append(
            [
                "arquivo",
                code,
                "Aluguel",
                "demonstrativo_contabil",
                "nao_mapear",
                None,
                None,
                None,
                None,
            ]
        )
        buf = io.BytesIO()
        wb.save(buf)

        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            f"/api/v1/clients/{world.client_a.id}/mapping/demonstrativo_contabil/import/preview",
            files={"file": ("de-para.xlsx", buf.getvalue(), XLSX)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["created"] == 1
        assert data["rejected"] == []


class TestEncerramentoEExclusao:
    async def test_encerrar_purga_as_categorias_do_arquivo(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        registry = _registry(db_session)
        await registry.resolve_codes(world.client_a, {"Aluguel"})
        await registry.resolve_codes(world.client_b, {"Aluguel"})

        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(f"/api/v1/clients/{world.client_a.id}/close")
        assert resp.status_code == 204, resp.text

        for client, esperado in ((world.client_a, 0), (world.client_b, 1)):
            total = (
                await db_session.execute(
                    select(func.count(ClientFileCategory.id)).where(
                        ClientFileCategory.client_id == client.id
                    )
                )
            ).scalar_one()
            assert total == esperado, client.name

    async def test_excluir_leva_por_cascade(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _registry(db_session).resolve_codes(world.client_a, {"Aluguel"})
        await _login(client_with_db, world.admin)
        resp = await client_with_db.delete(f"/api/v1/clients/{world.client_a.id}")
        assert resp.status_code == 204, resp.text
        total = (
            await db_session.execute(
                select(func.count(ClientFileCategory.id)).where(
                    ClientFileCategory.client_id == world.client_a.id
                )
            )
        ).scalar_one()
        assert total == 0
