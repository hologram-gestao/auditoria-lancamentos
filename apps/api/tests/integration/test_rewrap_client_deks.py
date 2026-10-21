"""Integração do reembrulho das DEKs para a KEK nova (86e3r2egr), contra Postgres.

Duas KEKs locais com o mesmo `key_id`: a ORIGEM (`OMIE_ENCRYPTION_KEY_SOURCE` simulada) e
o ALVO (a `OMIE_ENCRYPTION_KEY` do serviço, como no Cloud Run Job o alvo é a
`KEK_KMS_KEY_NAME` do serviço). Cobre os critérios de aceite:
    - `--dry-run` deixa `clients.dek_wrapped` byte a byte igual e não emite evento;
    - a execução troca só o blob, o MESMO campo cifrado de antes decifra com o alvo, e
      `deks_reembrulhadas` vai para `usage_events` só com contagens;
    - a segunda rodada conta zero pendentes e zero reembrulhadas;
    - `--verify` decifra um campo real de cada cliente pelos três caminhos de escopo
      (`clients.id`, coluna `client_id`, `session_id` → sessão) e conta o cliente sem campo;
    - o UPDATE condicional não toca linha cujo blob mudou;
    - nenhum material de chave no log.

Isolamento: uma conexão com transação externa e `async_sessionmaker` em
`create_savepoint`; os commits por cliente viram savepoints e o `rollback()` final desfaz
tudo (mesma estratégia do `test_rotate_encryption_key.py`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import structlog
from scripts.rewrap_client_deks import (
    PostgresDekStore,
    RewrapKeys,
    RewrapMode,
    run_rewrap,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import ClientCipher
from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    AAD_FILE_ENTRY_DESCRIPTION,
    AAD_GLOSSARY_NAME,
    field_locator,
    load_client_cipher,
)
from app.core.kms import LocalKmsClient, generate_dek
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientGlossaryEntry,
    ReconciliationFileEntry,
    ReconciliationSession,
    UsageEvent,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.integration

_SOURCE_HEX = "5e" * 32


def _cipher(client_id: UUID, dek: bytes, legacy_hex: str) -> ClientCipher:
    return ClientCipher(
        client_id=str(client_id),
        dek=dek,
        key_id=get_settings().KEK_KEY_ID,
        legacy_hex_key=legacy_hex,
    )


async def _blobs(factory: async_sessionmaker[AsyncSession], ids: list[UUID]) -> dict[UUID, bytes]:
    async with factory() as s:
        rows = (
            await s.execute(select(Client.id, Client.dek_wrapped).where(Client.id.in_(ids)))
        ).all()
    return {row[0]: bytes(row[1]) for row in rows}


async def _event_count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(UsageEvent)
                    .where(UsageEvent.event == "deks_reembrulhadas")
                )
            ).scalar_one()
        )


def _outcomes(logs: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(e["client_id"]): str(e["outcome"]) for e in logs if e.get("event") == "rewrap_client"
    }


@pytest.mark.integration
class TestRewrapClientDeks:
    async def test_dry_run_execucao_segunda_rodada_e_verify(self, db_engine: AsyncEngine) -> None:
        settings = get_settings()
        key_id = settings.KEK_KEY_ID
        legacy_hex = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
        source = LocalKmsClient(master_hex_key=_SOURCE_HEX, key_id=key_id)
        target = LocalKmsClient(master_hex_key=legacy_hex, key_id=key_id)
        keys = RewrapKeys(
            source=source,
            target=target,
            source_name="local (origem)",
            target_name="local (alvo)",
        )

        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            store = PostgresDekStore(factory)

            # ---------- seed: 4 clientes com DEK embrulhada pela KEK ANTIGA ----------
            deks = {name: generate_dek() for name in ("cred", "session", "glossary", "empty")}
            ids = {name: uuid4() for name in deks}
            async with factory() as s:
                admin = User(
                    name="Rewrap Admin",
                    email=f"rewrap-{uuid4().hex[:8]}@hologram.com.br",
                    password_hash=hash_password("Senh@Forte#1"),
                    role=UserRole.ADMIN.value,
                    active=True,
                )
                s.add(admin)
                await s.flush()

                cred_ct, cred_iv = _cipher(ids["cred"], deks["cred"], legacy_hex).encrypt(
                    "app-key-cred", field_locator(AAD_CLIENT_APP_KEY, ids["cred"])
                )
                for name, dek in deks.items():
                    s.add(
                        Client(
                            id=ids[name],
                            name=f"Rewrap {name}",
                            created_by=admin.id,
                            dek_wrapped=await source.wrap_dek(dek),
                            omie_app_key_encrypted=cred_ct if name == "cred" else None,
                            omie_app_key_iv=cred_iv if name == "cred" else None,
                        )
                    )
                await s.flush()

                sess = ReconciliationSession(
                    client_id=ids["session"],
                    created_by=admin.id,
                    omie_conta_id=1,
                    reference_month=date(2026, 9, 1),
                    file_hash="d" * 64,
                    status="reviewing",
                    balance_start=Decimal("0.00"),
                )
                s.add(sess)
                await s.flush()
                fe_id = uuid4()
                fe_ct, fe_iv = _cipher(ids["session"], deks["session"], legacy_hex).encrypt(
                    "descrição da linha", field_locator(AAD_FILE_ENTRY_DESCRIPTION, fe_id)
                )
                s.add(
                    ReconciliationFileEntry(
                        id=fe_id,
                        session_id=sess.id,
                        transaction_date=date(2026, 9, 5),
                        description_encrypted=fe_ct,
                        description_iv=fe_iv,
                        amount=Decimal("-10.00"),
                        situation="sem_omie",
                    )
                )
                gl_id = uuid4()
                gl_ct, gl_iv = _cipher(ids["glossary"], deks["glossary"], legacy_hex).encrypt(
                    "Categoria do glossário", field_locator(AAD_GLOSSARY_NAME, gl_id)
                )
                s.add(
                    ClientGlossaryEntry(
                        id=gl_id,
                        client_id=ids["glossary"],
                        kind="categoria",
                        name_encrypted=gl_ct,
                        name_iv=gl_iv,
                    )
                )
                await s.commit()

            our_ids = list(ids.values())
            original = await _blobs(factory, our_ids)
            events_before = await _event_count(factory)

            # ---------- --dry-run: classifica, prova e NÃO grava ----------
            with structlog.testing.capture_logs() as dry_logs:
                dry = await run_rewrap(
                    store=store, settings=settings, keys=keys, mode=RewrapMode.DRY_RUN
                )
            assert dry.pending >= 4
            assert dry.rewrapped == 0
            assert await _blobs(factory, our_ids) == original  # byte a byte
            assert await _event_count(factory) == events_before
            assert {_outcomes(dry_logs)[str(i)] for i in our_ids} == {"pending"}

            # ---------- --verify ANTES: a KEK nova não abre nenhuma ----------
            with structlog.testing.capture_logs() as pre_logs:
                await run_rewrap(store=store, settings=settings, keys=keys, mode=RewrapMode.VERIFY)
            assert {_outcomes(pre_logs)[str(i)] for i in our_ids} == {"verify_failed"}

            # ---------- execução ----------
            with structlog.testing.capture_logs() as run_logs:
                first = await run_rewrap(
                    store=store, settings=settings, keys=keys, mode=RewrapMode.EXECUTE
                )
            assert first.rewrapped >= 4
            assert {_outcomes(run_logs)[str(i)] for i in our_ids} == {"rewrapped"}
            after = await _blobs(factory, our_ids)
            for name, client_id in ids.items():
                assert after[client_id] != original[client_id]
                assert await target.unwrap_dek(after[client_id]) == deks[name]

            assert await _event_count(factory) == events_before + 1
            async with factory() as s:
                event = (
                    await s.execute(
                        select(UsageEvent)
                        .where(UsageEvent.event == "deks_reembrulhadas")
                        .order_by(UsageEvent.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one()
            assert set(event.props) == {"ja_reembrulhadas", "reembrulhadas", "falhas", "duracao_s"}
            assert event.props["reembrulhadas"] == first.rewrapped
            assert event.session_id is None

            # O MESMO campo cifrado antes decifra com a DEK aberta pela KEK nova.
            async with factory() as s:
                cred_client = await s.get(Client, ids["cred"])
                assert cred_client is not None
                cipher = await load_client_cipher(cred_client, settings=settings, kms=target)
                assert (
                    cipher.decrypt(cred_ct, cred_iv, field_locator(AAD_CLIENT_APP_KEY, ids["cred"]))
                    == "app-key-cred"
                )

            # ---------- segunda rodada: zero pendentes ----------
            with structlog.testing.capture_logs() as second_logs:
                second = await run_rewrap(
                    store=store, settings=settings, keys=keys, mode=RewrapMode.EXECUTE
                )
            assert second.rewrapped == 0
            assert second.pending == 0
            assert second.already >= 4
            assert {_outcomes(second_logs)[str(i)] for i in our_ids} == {"already"}
            assert await _blobs(factory, our_ids) == after

            # ---------- --verify DEPOIS: os três caminhos de escopo ----------
            with structlog.testing.capture_logs() as verify_logs:
                await run_rewrap(store=store, settings=settings, keys=keys, mode=RewrapMode.VERIFY)
            outcomes = _outcomes(verify_logs)
            assert outcomes[str(ids["cred"])] == "verified"
            assert outcomes[str(ids["session"])] == "verified"
            assert outcomes[str(ids["glossary"])] == "verified"
            assert outcomes[str(ids["empty"])] == "no_field"

            # ---------- UPDATE condicional: blob que mudou não é tocado ----------
            assert (
                await store.swap_blob(
                    ids["empty"], old_blob=original[ids["empty"]], new_blob=b"nao-deve-gravar"
                )
                == 0
            )
            assert (await _blobs(factory, [ids["empty"]]))[ids["empty"]] == after[ids["empty"]]

            # ---------- nada de material de chave no log ----------
            secrets = [b.hex() for b in original.values()] + [b.hex() for b in after.values()]
            secrets += [d.hex() for d in deks.values()] + [_SOURCE_HEX, legacy_hex]
            for entry in dry_logs + pre_logs + run_logs + second_logs + verify_logs:
                rendered = repr(entry)
                assert not any(isinstance(v, bytes | bytearray) for v in entry.values())
                for secret in secrets:
                    assert secret not in rendered

            await outer.rollback()
