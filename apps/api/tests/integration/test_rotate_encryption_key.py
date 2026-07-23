"""Integração do backfill/rotação de chave (Sprint 3, BACK 03.4).

Prova o furo real fechado: linhas legadas **bare** (chave global, sem AAD) são
re-cifradas para `v1:<key_id>:` + AAD + DEK-por-cliente, cobrindo todos os campos
do CLAUDE.md §4. Cobre os critérios de aceite:
    - Nenhuma linha bare permanece; `clients.dek_wrapped` sem nulos.
    - Round-trip pós-backfill via o ClientCipher do cliente.
    - Negativo (isolamento): a DEK de A não decifra credencial de B.
    - Negativo (AAD): ciphertext de A colado numa leitura de B falha.
    - `chave_rotacionada` emitido (sem PII).
    - Idempotente: 2º run converte 0.

Isolamento: usa UMA conexão com transação externa + `async_sessionmaker` em
`create_savepoint`, então os `commit()` por lote da rotação viram savepoints e o
`rollback()` final desfaz tudo (mesma estratégia do fixture `db_session`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import structlog
from scripts.rotate_encryption_key import run_rotation
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import ClientCipher, CryptoError, encrypt
from app.core.crypto_service import (
    AAD_ANOMALY_CONTEXT,
    AAD_ANOMALY_RESOLUTION_NOTE,
    AAD_CLIENT_APP_KEY,
    AAD_FILE_ENTRY_DESCRIPTION,
    AAD_FILE_ENTRY_USER_NOTE,
    AAD_OMIE_ENTRY_USER_NOTE,
    field_locator,
    load_client_cipher,
)
from app.core.kms import get_kms_client
from app.core.security import hash_password
from app.db.models import (
    AnomalyType,
    Client,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


@pytest.mark.integration
class TestRotationBackfill:
    async def test_backfill_converts_all_bare_rows(self, db_engine: AsyncEngine) -> None:
        settings = get_settings()
        global_key = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
        kms = get_kms_client(settings)

        async with db_engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

            # ---------- seed legado (bare) ----------
            async with factory() as s:
                admin = User(
                    name="Rot Admin",
                    email="rot-admin@hologram.com.br",
                    password_hash=hash_password("Senh@Forte#1"),
                    role=UserRole.ADMIN.value,
                    active=True,
                )
                s.add(admin)
                await s.flush()

                atype = AnomalyType(
                    code="rot_test_type",
                    name="Rot Test",
                    description="tipo p/ teste de rotação",
                    severity="low",
                    active=True,
                )
                s.add(atype)
                await s.flush()

                ck_a, ivk_a = encrypt("app-key-A", global_key)
                cs_a, ivs_a = encrypt("app-secret-A", global_key)
                client_a = Client(
                    name="Cliente A",
                    omie_app_key_encrypted=ck_a,
                    omie_app_key_iv=ivk_a,
                    omie_app_secret_encrypted=cs_a,
                    omie_app_secret_iv=ivs_a,
                    created_by=admin.id,
                    dek_wrapped=None,
                )
                ck_b, ivk_b = encrypt("app-key-B", global_key)
                cs_b, ivs_b = encrypt("app-secret-B", global_key)
                client_b = Client(
                    name="Cliente B",
                    omie_app_key_encrypted=ck_b,
                    omie_app_key_iv=ivk_b,
                    omie_app_secret_encrypted=cs_b,
                    omie_app_secret_iv=ivs_b,
                    created_by=admin.id,
                    dek_wrapped=None,
                )
                s.add_all([client_a, client_b])
                await s.flush()

                sess = ReconciliationSession(
                    client_id=client_a.id,
                    created_by=admin.id,
                    omie_conta_id=1,
                    reference_month=date(2026, 4, 1),
                    file_hash="a" * 64,
                    status="reviewing",
                    balance_start=Decimal("0.00"),
                )
                s.add(sess)
                await s.flush()

                d_ct, d_iv = encrypt("descrição secreta", global_key)
                n_ct, n_iv = encrypt("nota do file entry", global_key)
                fe = ReconciliationFileEntry(
                    session_id=sess.id,
                    transaction_date=date(2026, 4, 5),
                    description_encrypted=d_ct,
                    description_iv=d_iv,
                    amount=Decimal("-10.00"),
                    situation="sem_omie",
                    user_note_encrypted=n_ct,
                    user_note_iv=n_iv,
                )
                on_ct, on_iv = encrypt("nota omie", global_key)
                oe = ReconciliationOmieEntry(
                    session_id=sess.id,
                    omie_lancamento_id=999,
                    transaction_date=date(2026, 4, 5),
                    omie_status="Atrasado",
                    user_note_encrypted=on_ct,
                    user_note_iv=on_iv,
                )
                c_ct, c_iv = encrypt("contexto da anomalia", global_key)
                r_ct, r_iv = encrypt("resolução detalhada", global_key)
                an = ReconciliationAnomaly(
                    session_id=sess.id,
                    anomaly_type_id=atype.id,
                    detected_by="ai",
                    context_encrypted=c_ct,
                    context_iv=c_iv,
                    resolved=True,
                    resolution_note_encrypted=r_ct,
                    resolution_note_iv=r_iv,
                )
                s.add_all([fe, oe, an])
                await s.commit()
                a_id, b_id = client_a.id, client_b.id
                fe_id, oe_id, an_id = fe.id, oe.id, an.id

            # ---------- roda a rotação ----------
            with structlog.testing.capture_logs() as logs:
                stats = await run_rotation(session_factory=factory, settings=settings, batch_size=2)

            # `>=` e não `==`: a suíte compartilha o mesmo Postgres (testcontainers)
            # e outros testes (ex.: test_reconciliation_job) deixam clientes/linhas
            # committados; a rotação processa todos, mas nossas conversões vivem no
            # savepoint da conexão isolada e o rollback final as desfaz. Os invariantes
            # que travamos abaixo são sobre AS NOSSAS linhas (determinísticos).
            assert stats.deks_provisioned >= 2  # nossos 2 clientes eram legados
            # nossos: creds A(2)+B(2) + fe(desc+note=2) + oe(note=1) + anomaly(ctx+res=2) = 9
            assert stats.fields_converted >= 9
            assert stats.clients_afetados >= 2
            rot = [entry for entry in logs if entry.get("event") == "chave_rotacionada"]
            assert len(rot) == 1
            assert rot[0]["clientes_afetados"] >= 2
            assert "duracao_s" in rot[0]

            # ---------- verificação pós-backfill ----------
            async with factory() as s:
                a = await s.get(Client, a_id)
                b = await s.get(Client, b_id)
                assert a is not None
                assert b is not None
                assert a.dek_wrapped is not None
                assert b.dek_wrapped is not None
                fe = await s.get(ReconciliationFileEntry, fe_id)
                oe = await s.get(ReconciliationOmieEntry, oe_id)
                an = await s.get(ReconciliationAnomaly, an_id)
                assert fe is not None
                assert oe is not None
                assert an is not None

                # Nenhuma linha bare permanece.
                for value in (
                    a.omie_app_key_encrypted,
                    a.omie_app_secret_encrypted,
                    b.omie_app_key_encrypted,
                    b.omie_app_secret_encrypted,
                    fe.description_encrypted,
                    fe.user_note_encrypted,
                    oe.user_note_encrypted,
                    an.context_encrypted,
                    an.resolution_note_encrypted,
                ):
                    assert value is not None
                    assert value.startswith("v1:")
                    assert not ClientCipher.is_legacy(value)

                # Round-trip via o cipher do cliente A.
                cipher_a = await load_client_cipher(a, settings=settings)
                assert (
                    cipher_a.decrypt(
                        a.omie_app_key_encrypted,
                        a.omie_app_key_iv,
                        field_locator(AAD_CLIENT_APP_KEY, a.id),
                    )
                    == "app-key-A"
                )
                assert (
                    cipher_a.decrypt(
                        fe.description_encrypted,
                        fe.description_iv,
                        field_locator(AAD_FILE_ENTRY_DESCRIPTION, fe.id),
                    )
                    == "descrição secreta"
                )
                assert (
                    cipher_a.decrypt(
                        fe.user_note_encrypted,
                        fe.user_note_iv,
                        field_locator(AAD_FILE_ENTRY_USER_NOTE, fe.id),
                    )
                    == "nota do file entry"
                )
                assert (
                    cipher_a.decrypt(
                        oe.user_note_encrypted,
                        oe.user_note_iv,
                        field_locator(AAD_OMIE_ENTRY_USER_NOTE, oe.id),
                    )
                    == "nota omie"
                )
                assert (
                    cipher_a.decrypt(
                        an.context_encrypted,
                        an.context_iv,
                        field_locator(AAD_ANOMALY_CONTEXT, an.id),
                    )
                    == "contexto da anomalia"
                )
                assert (
                    cipher_a.decrypt(
                        an.resolution_note_encrypted,
                        an.resolution_note_iv,
                        field_locator(AAD_ANOMALY_RESOLUTION_NOTE, an.id),
                    )
                    == "resolução detalhada"
                )

                # Negativo (isolamento): a DEK de A não decifra credencial de B.
                dek_a = await kms.unwrap_dek(a.dek_wrapped)
                attacker = ClientCipher(
                    client_id=str(b.id),
                    dek=dek_a,
                    key_id=settings.KEK_KEY_ID,
                    legacy_hex_key=global_key,
                )
                with pytest.raises(CryptoError):
                    attacker.decrypt(
                        b.omie_app_key_encrypted,
                        b.omie_app_key_iv,
                        field_locator(AAD_CLIENT_APP_KEY, b.id),
                    )

                # Negativo (AAD/isolamento): ciphertext de A lido no contexto de B falha.
                cipher_b = await load_client_cipher(b, settings=settings)
                with pytest.raises(CryptoError):
                    cipher_b.decrypt(
                        a.omie_app_key_encrypted,
                        a.omie_app_key_iv,
                        field_locator(AAD_CLIENT_APP_KEY, b.id),
                    )

            # ---------- idempotência: 2º run converte 0 ----------
            stats2 = await run_rotation(session_factory=factory, settings=settings)
            assert stats2.fields_converted == 0
            assert stats2.clients_afetados == 0
            assert stats2.deks_provisioned == 0

            await outer.rollback()
