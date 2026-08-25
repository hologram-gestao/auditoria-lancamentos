"""Testes de integração da Tela de Revisão (S11 BACK 9.1, 9.3-9.10).

Cobre os 10 endpoints novos. Quando Docker não está disponível, todos os
testes que tocam DB são marcados SKIPPED via fixture `db_session` —
mesmo padrão dos outros arquivos de integração.

Estrutura:
    - Helpers (seed user / client / session / file entry / omie entry /
      anomaly_type / anomaly).
    - Classes por endpoint, agrupando happy + RBAC + erro.
    - Stubbing do `OmieClient` quando preciso (BACK 9.4 chama listar_extrato).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.crypto_service import (
    AAD_FILE_ENTRY_USER_NOTE,
    AAD_OMIE_ENTRY_USER_NOTE,
    field_locator,
    load_client_cipher,
)
from app.core.search_index import compute_search_hmac
from app.core.security import hash_password
from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    ClientAssignment,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


ADMIN_EMAIL = "review-admin@hologram.com.br"
MANAGER_A_EMAIL = "review-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "review-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

FAKE_APP_KEY = "test-app-key-review"
FAKE_APP_SECRET = "test-app-secret-review"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="T",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession, *, creator: User, manager: User | None = None
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(FAKE_APP_KEY, hex_key)
    ct_s, iv_s = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name="Cliente Review",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    if manager is not None:
        session.add(
            ClientAssignment(client_id=client.id, user_id=manager.id, assigned_by=creator.id)
        )
        await session.flush()
    return client


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    status: str = "reviewing",
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=3,
        file_hash=_hex64(f"review-{uuid4().hex}"),
        status=status,
        balance_start=Decimal("0.00"),
        processed_at=datetime.now(UTC),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_file_entry(
    session: AsyncSession,
    *,
    reconciliation: ReconciliationSession,
    description: str,
    amount: Decimal,
    situation: str = "sem_omie",
    omie_lancamento_id: int | None = None,
    tx_date: date = date(2026, 4, 10),
    skip_search_hmac: bool = False,
) -> ReconciliationFileEntry:
    """Insere file_entry criptografando description e gravando blind index.

    Por default popula `description_search_hmac` (S16) para refletir o
    caminho de criação real. Testes específicos do path "sessão pré-S16"
    passam `skip_search_hmac=True` para deixar a coluna NULL.
    """
    settings = get_settings()
    hex_key = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description, hex_key)
    if skip_search_hmac:
        search_hmac: str | None = None
    else:
        hex_blind_key = settings.SEARCH_BLIND_INDEX_KEY.get_secret_value()
        search_hmac = compute_search_hmac(description, hex_blind_key)
    entry = ReconciliationFileEntry(
        session_id=reconciliation.id,
        transaction_date=tx_date,
        description_encrypted=ct,
        description_iv=iv,
        description_search_hmac=search_hmac,
        amount=amount,
        situation=situation,
        omie_lancamento_id=omie_lancamento_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _seed_omie_entry(
    session: AsyncSession,
    *,
    reconciliation: ReconciliationSession,
    omie_lancamento_id: int,
    omie_status: str = "Atrasado",
    tx_date: date = date(2026, 4, 20),
) -> ReconciliationOmieEntry:
    entry = ReconciliationOmieEntry(
        session_id=reconciliation.id,
        omie_lancamento_id=omie_lancamento_id,
        transaction_date=tx_date,
        omie_status=omie_status,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _seed_anomaly_types(session: AsyncSession) -> dict[str, AnomalyType]:
    """Insere os 2 AnomalyTypes mais usados pelos testes."""
    types: dict[str, AnomalyType] = {}
    seeds = [
        (
            "missing_in_omie",
            "Movimentação sem lançamento no Omie",
            AnomalySeverity.CRITICAL.value,
            "Falta no Omie.",
        ),
        (
            "wrong_account",
            "Lançamento possivelmente na conta errada",
            AnomalySeverity.MODERATE.value,
            "Suspeita.",
        ),
    ]
    for code, name, severity, descr in seeds:
        existing = (
            await session.execute(select(AnomalyType).where(AnomalyType.code == code))
        ).scalar_one_or_none()
        if existing is not None:
            types[code] = existing
            continue
        atype = AnomalyType(code=code, name=name, description=descr, severity=severity, active=True)
        session.add(atype)
        await session.flush()
        types[code] = atype
    return types


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# BACK 9.1 — GET /file-entries
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestListFileEntries:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        sid = uuid4()
        resp = await client_with_db.get(f"/api/v1/reconciliations/{sid}/file-entries")
        assert resp.status_code == 401

    async def test_admin_lists_with_decrypted_descriptions(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Padaria",
            amount=Decimal("-1250.00"),
        )
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Recebimento Cielo",
            amount=Decimal("999.99"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        descriptions = sorted(item["description"] for item in body["data"])
        assert descriptions == ["Pagamento Padaria", "Recebimento Cielo"]
        assert body["pagination"]["total"] == 2

    async def test_filter_search_uses_blind_index(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S16: filtro `search` casa via blind index (SQL), com acento/case
        normalizados. As linhas seedadas com `_seed_file_entry` já incluem
        `description_search_hmac`.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Padaria",
            amount=Decimal("-1.00"),
        )
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Recebimento Cielo",
            amount=Decimal("2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Caso happy: token completo bate.
        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "padaria"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["description"] == "Pagamento Padaria"

        # Insensível a case + acento.
        resp_upper = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "PADARIA"},
        )
        assert resp_upper.json()["pagination"]["total"] == 1

    async def test_filter_search_token_below_min_length_returns_empty(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Termo de busca com apenas tokens < 3 chars devolve 0 — não vai ao DB.

        Comportamento UX consistente: "buscar por 'de'" não faz sentido como
        índice; UI pode evoluir para sinalizar isso ao usuário.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento de boleto",
            amount=Decimal("-1.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "de"},
        )
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] == 0

    async def test_filter_search_skips_legacy_rows_without_hmac(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S16: linhas pré-migration (`description_search_hmac IS NULL`) ficam
        fora do filtro `search`. LIKE contra NULL é NULL → falsy em WHERE.
        Listagem sem `search` continua trazendo a linha normalmente.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # Linha "legada" — sem o HMAC populado.
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Antigo Padaria",
            amount=Decimal("-1.00"),
            skip_search_hmac=True,
        )
        # Linha nova — com HMAC.
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Novo Padaria",
            amount=Decimal("-2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Sem search: ambas aparecem.
        resp_all = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
        )
        assert resp_all.json()["pagination"]["total"] == 2

        # Com search: só a linha nova.
        resp_search = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "padaria"},
        )
        assert resp_search.status_code == 200
        body = resp_search.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["description"] == "Pagamento Novo Padaria"

    async def test_filter_type_credit_only(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session, reconciliation=sess, description="Crédito", amount=Decimal("5.00")
        )
        await _seed_file_entry(
            db_session, reconciliation=sess, description="Débito", amount=Decimal("-5.00")
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"type": "credit"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["amount"] == "5.00"

    async def test_filter_only_suspect_e_server_side_e_a_paginacao_bate(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """86e2n4pf1 — o filtro espelha o BADGE: qualificação NÃO resolvida.

        4 linhas: com flag pendente (entra), com flag pendente já julgado
        improcedente (ENTRA — o veredito da S6 não muda o badge, então não
        muda o filtro), com flag resolvido (sai), sem flag (sai).
        `pagination.total` tem de refletir o filtro — era a mentira original:
        tabela vazia com rodapé "1-20 de 78".
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)

        e_pendente = await _seed_file_entry(
            db_session, reconciliation=sess, description="Flag pendente", amount=Decimal("-1.00")
        )
        e_improcedente = await _seed_file_entry(
            db_session, reconciliation=sess, description="Improcedente", amount=Decimal("-2.00")
        )
        e_resolvido = await _seed_file_entry(
            db_session, reconciliation=sess, description="Flag resolvido", amount=Decimal("-3.00")
        )
        await _seed_file_entry(
            db_session, reconciliation=sess, description="Sem flag", amount=Decimal("-4.00")
        )

        # Get-or-create, como o `_seed_anomaly_types` deste arquivo: os testes
        # de qualificação semeiam o MESMO code e a tabela sobrevive entre
        # arquivos — inserir às cegas quebra com UniqueViolation na ordem do
        # CI (foi exatamente o que aconteceu no run 32406669914).
        suspeita = (
            await db_session.execute(
                select(AnomalyType).where(AnomalyType.code == "qualificacao_suspeita")
            )
        ).scalar_one_or_none()
        if suspeita is None:
            suspeita = AnomalyType(
                code="qualificacao_suspeita",
                name="Categoria suspeita",
                description="Camada 1",
                severity=AnomalySeverity.MODERATE.value,
                active=True,
            )
            db_session.add(suspeita)
            await db_session.flush()
        db_session.add_all(
            [
                ReconciliationAnomaly(
                    session_id=sess.id,
                    anomaly_type_id=suspeita.id,
                    file_entry_id=e_pendente.id,
                    detected_by="ai",
                    resolved=False,
                ),
                ReconciliationAnomaly(
                    session_id=sess.id,
                    anomaly_type_id=suspeita.id,
                    file_entry_id=e_improcedente.id,
                    detected_by="ai",
                    resolved=False,
                    review_verdict="improcedente",
                ),
                ReconciliationAnomaly(
                    session_id=sess.id,
                    anomaly_type_id=suspeita.id,
                    file_entry_id=e_resolvido.id,
                    detected_by="ai",
                    resolved=True,
                ),
            ]
        )
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        # Ligado: só as 2 com flag pendente — e o TOTAL diz 2, não 4.
        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"onlySuspect": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 2
        descricoes = {e["description"] for e in body["data"]}
        assert descricoes == {"Flag pendente", "Improcedente"}

        # Combinado com outro filtro server-side (type=debit continua valendo).
        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"onlySuspect": "true", "type": "debit"},
        )
        assert resp.json()["pagination"]["total"] == 2

        # Desligado: a listagem completa volta.
        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert resp.json()["pagination"]["total"] == 4

    async def test_manager_outside_portfolio_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli = await _seed_client(db_session, creator=admin, manager=mgr_a)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# BACK 9.3 — PATCH /file-entries/{id}
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateFileEntry:
    async def test_admin_updates_situation_and_note(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Lançamento X",
            amount=Decimal("-100.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"situation": "ignorado", "user_note": "Não relacionado"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["situation"] == "ignorado"
        assert body["user_note"] == "Não relacionado"

        # Persistido no envelope v1 (DEK-por-cliente + AAD) — a nota é cifrada
        # com a DEK provisionada no PATCH; decifra via o ClientCipher do cliente.
        await db_session.refresh(entry)
        await db_session.refresh(cli)
        cipher = await load_client_cipher(cli, settings=get_settings())
        assert entry.user_note_encrypted is not None
        assert entry.user_note_iv is not None
        assert cipher.decrypt(
            entry.user_note_encrypted,
            entry.user_note_iv,
            field_locator(AAD_FILE_ENTRY_USER_NOTE, entry.id),
        ) == ("Não relacionado")

    async def test_trocar_omie_id_duplicate_in_session_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry_a = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70001,
            situation="conciliado",
        )
        entry_b = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="B",
            amount=Decimal("-2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Tenta vincular entry_b ao mesmo Omie ID que entry_a já usa
        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry_b.id}",
            json={"omie_lancamento_id": 70001},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

        # entry_a manteve o vínculo
        await db_session.refresh(entry_a)
        assert entry_a.omie_lancamento_id == 70001

    async def test_trocar_omie_id_idempotent_same_value(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70002,
            situation="conciliado",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        for _ in range(2):
            resp = await client_with_db.patch(
                f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
                json={"omie_lancamento_id": 70002},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["omie_lancamento_id"] == 70002

    async def test_clear_omie_id_via_null(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70003,
            situation="conciliado",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"omie_lancamento_id": None},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["omie_lancamento_id"] is None
        assert body["situation"] == "sem_omie"

    # ------------------------------------------------------------------
    # Apagar anotação (86e2n4peu)
    # ------------------------------------------------------------------
    # A resposta HTTP NÃO basta como prova: `user_note: null` no corpo também
    # é o que sai quando a decifragem falha (`_decrypt_pair` engole o erro).
    # Por isso todo teste de limpeza confere as COLUNAS no banco.

    async def _note_columns(
        self, db_session: AsyncSession, entry: ReconciliationFileEntry
    ) -> tuple[str | None, str | None]:
        await db_session.refresh(entry)
        return entry.user_note_encrypted, entry.user_note_iv

    async def test_clear_note_with_null_actually_deletes_it(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`null` apaga de verdade — era o caso que respondia 200 sem apagar nada."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Lançamento com nota",
            amount=Decimal("-10.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}"

        gravou = await client_with_db.patch(url, json={"user_note": "Conferir com o Galhardo"})
        assert gravou.status_code == 200, gravou.text
        ct, iv = await self._note_columns(db_session, entry)
        assert ct is not None
        assert iv is not None

        apagou = await client_with_db.patch(url, json={"user_note": None})
        assert apagou.status_code == 200, apagou.text
        assert apagou.json()["data"]["user_note"] is None
        assert await self._note_columns(db_session, entry) == (None, None)

        # E some também de quem recarrega a tela depois.
        relido = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert relido.status_code == 200, relido.text
        assert relido.json()["data"][0]["user_note"] is None

    async def test_clear_note_with_empty_string_still_works(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Contrato antigo (string vazia limpa) continua valendo — nada quebra."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session, reconciliation=sess, description="X", amount=Decimal("-1.00")
        )
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}"

        await client_with_db.patch(url, json={"user_note": "nota"})
        resp = await client_with_db.patch(url, json={"user_note": ""})
        assert resp.status_code == 200, resp.text
        assert await self._note_columns(db_session, entry) == (None, None)

    async def test_blank_note_is_treated_as_clear_not_stored(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Só espaços = apagar. Antes ia cifrado para o banco e voltava na tela."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session, reconciliation=sess, description="X", amount=Decimal("-1.00")
        )
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}"

        await client_with_db.patch(url, json={"user_note": "nota"})
        resp = await client_with_db.patch(url, json={"user_note": "   \n  "})
        assert resp.status_code == 200, resp.text
        assert await self._note_columns(db_session, entry) == (None, None)

    async def test_omitting_note_preserves_it(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A outra metade do contrato: PATCH sem a chave NÃO pode apagar a nota."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session, reconciliation=sess, description="X", amount=Decimal("-1.00")
        )
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}"

        await client_with_db.patch(url, json={"user_note": "Preservar isto"})
        resp = await client_with_db.patch(url, json={"user_action": "flag"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["user_note"] == "Preservar isto"

        await db_session.refresh(entry)
        await db_session.refresh(cli)
        cipher = await load_client_cipher(cli, settings=get_settings())
        assert entry.user_note_encrypted is not None
        assert entry.user_note_iv is not None
        assert (
            cipher.decrypt(
                entry.user_note_encrypted,
                entry.user_note_iv,
                field_locator(AAD_FILE_ENTRY_USER_NOTE, entry.id),
            )
            == "Preservar isto"
        )

    async def test_omitting_omie_id_preserves_the_link(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A outra metade do tri-estado do vínculo Omie, que a refatoração moveu.

        A derivação de "chave presente" saiu da rota para o service; sem este
        teste, só o caminho `null` (limpar) estava coberto e a omissão passaria
        a apagar o vínculo sem nada acusar.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Vinculada",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70009,
            situation="conciliado",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"user_note": "só anotando"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["omie_lancamento_id"] == 70009
        assert body["situation"] == "conciliado"

        await db_session.refresh(entry)
        assert entry.omie_lancamento_id == 70009

    async def test_counters_recomputed_after_update(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            situation="sem_omie",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"omie_lancamento_id": 99001},
        )
        assert resp.status_code == 200

        await db_session.refresh(sess)
        assert sess.conciliated_count == 1
        assert sess.sem_omie_count == 0

    async def test_trocar_omie_race_caught_by_unique_index(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Race em "Trocar Omie": 2 requests passam pela checagem aplicativa
        no MESMO instante e ambos tentam gravar o mesmo `omie_lancamento_id`.

        O índice ÚNICO PARCIAL `ix_recon_file_entry_session_omie_unique`
        (CLAUDE.md §5.4) detecta a colisão; o service captura o
        `IntegrityError` e devolve a MESMA `ValidationAppError` que o
        caminho aplicativo — UX idêntica com ou sem race.

        Como simular: monkey-patch da checagem aplicativa para retornar
        False, forçando o service a chegar até o flush onde o índice
        dispara o IntegrityError.
        """
        from app.modules.reconciliations.review.repository import ReviewRepository

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # entry_a já tem o vínculo Omie 70404 — basta persistir pra que o
        # índice único dispare quando o entry_b tentar o mesmo Omie ID.
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70404,
            situation="conciliado",
        )
        entry_b = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="B",
            amount=Decimal("-2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Força a checagem aplicativa a falsear o conflito — simula "ambos
        # requests passaram pela checagem quase ao mesmo tempo".
        async def _fake_taken(self: ReviewRepository, **kwargs: object) -> bool:
            return False

        monkeypatch.setattr(
            ReviewRepository,
            "file_entry_omie_id_taken_by_another",
            _fake_taken,
        )

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry_b.id}",
            json={"omie_lancamento_id": 70404},
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        # Mensagem amigável idêntica à do caminho aplicativo (§11 CLAUDE.md).
        assert "já está vinculado" in body["error"]["userMessage"]

        # Não checamos estado pós-PATCH via SELECT porque o conftest
        # injeta a MESMA `db_session` do teste no request via override —
        # quando o flush falha com IntegrityError, toda a transação fica
        # ROLLBACK ONLY e qualquer query subsequente levanta
        # PendingRollbackError. O caminho crítico (constraint dispara →
        # service captura → ValidationAppError com mensagem PT-BR) já
        # está provado pela resposta HTTP acima; o rollback transacional
        # da request (DbSessionDep) garante que nada foi persistido.


# ----------------------------------------------------------------------
# BACK 9.6 — PATCH /omie-entries/{id}
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateOmieEntry:
    async def test_update_user_action_and_note(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80001)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}",
            json={"user_action": "flag", "user_note": "Pendente conferência"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["user_action"] == "flag"
        assert body["user_note"] == "Pendente conferência"

    async def test_clear_note_with_null_actually_deletes_it(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Mesmo defeito vivia na aba de Divergências Omie — cobre o segundo ponto."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80002)
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}"

        gravou = await client_with_db.patch(url, json={"user_note": "Cobrar o cliente"})
        assert gravou.status_code == 200, gravou.text
        await db_session.refresh(entry)
        assert entry.user_note_encrypted is not None

        apagou = await client_with_db.patch(url, json={"user_note": None})
        assert apagou.status_code == 200, apagou.text
        assert apagou.json()["data"]["user_note"] is None
        await db_session.refresh(entry)
        assert (entry.user_note_encrypted, entry.user_note_iv) == (None, None)

    async def test_clear_note_with_empty_string_still_works(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Retrocompatibilidade no segundo endpoint — o branch aqui é próprio."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80004)
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}"

        await client_with_db.patch(url, json={"user_note": "nota"})
        resp = await client_with_db.patch(url, json={"user_note": ""})
        assert resp.status_code == 200, resp.text
        await db_session.refresh(entry)
        assert (entry.user_note_encrypted, entry.user_note_iv) == (None, None)

    async def test_omitting_note_preserves_it(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80003)
        await _login(client_with_db, ADMIN_EMAIL)
        url = f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}"

        await client_with_db.patch(url, json={"user_note": "Preservar isto"})
        resp = await client_with_db.patch(url, json={"user_action": "ignore"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["user_note"] == "Preservar isto"

        await db_session.refresh(entry)
        await db_session.refresh(cli)
        cipher = await load_client_cipher(cli, settings=get_settings())
        assert entry.user_note_encrypted is not None
        assert entry.user_note_iv is not None
        assert (
            cipher.decrypt(
                entry.user_note_encrypted,
                entry.user_note_iv,
                field_locator(AAD_OMIE_ENTRY_USER_NOTE, entry.id),
            )
            == "Preservar isto"
        )

    async def test_does_not_recompute_session_counters(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        sess.omie_sem_arquivo_count = 5
        await db_session.flush()
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80002)
        await _login(client_with_db, ADMIN_EMAIL)

        await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}",
            json={"user_action": "ignore"},
        )
        await db_session.refresh(sess)
        assert sess.omie_sem_arquivo_count == 5  # inalterado


# ----------------------------------------------------------------------
# BACK 9.7, 9.8, 9.9 — Anomalias
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestAnomalies:
    async def test_create_and_list_anomaly(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Foo",
            amount=Decimal("-3.00"),
        )
        types = await _seed_anomaly_types(db_session)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            json={
                "anomaly_type_id": str(types["wrong_account"].id),
                "file_entry_id": str(entry.id),
                "context": "Talvez seja Sicredi",
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()["data"]
        assert created["detected_by"] == "manual"
        assert created["resolved"] is False
        assert created["context"] == "Talvez seja Sicredi"
        assert created["anomaly_type"]["code"] == "wrong_account"
        assert created["related_file_entry"]["description"] == "Foo"

        await db_session.refresh(sess)
        assert sess.anomaly_count == 1

        # Lista
        resp_list = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/anomalies")
        assert resp_list.status_code == 200
        rows = resp_list.json()["data"]
        assert len(rows) == 1

    async def test_create_anomaly_with_both_entries_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        fe = await _seed_file_entry(
            db_session, reconciliation=sess, description="x", amount=Decimal("-1.00")
        )
        oe = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=42_424)
        types = await _seed_anomaly_types(db_session)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            json={
                "anomaly_type_id": str(types["wrong_account"].id),
                "file_entry_id": str(fe.id),
                "omie_entry_id": str(oe.id),
            },
        )
        assert resp.status_code == 400, resp.text

    async def test_create_with_inactive_type_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session, reconciliation=sess, description="x", amount=Decimal("-1.00")
        )
        types = await _seed_anomaly_types(db_session)
        types["wrong_account"].active = False
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            json={"anomaly_type_id": str(types["wrong_account"].id)},
        )
        assert resp.status_code == 400, resp.text

    async def test_resolve_with_short_note_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        anomaly = ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=types["wrong_account"].id,
            detected_by=AnomalyDetectedBy.AI.value,
            resolved=False,
        )
        db_session.add(anomaly)
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/anomalies/{anomaly.id}",
            json={"resolved": True, "resolution_note": "ok"},
        )
        assert resp.status_code == 400

    async def test_resolve_happy_path(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        anomaly = ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=types["wrong_account"].id,
            detected_by=AnomalyDetectedBy.AI.value,
            resolved=False,
        )
        db_session.add(anomaly)
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/anomalies/{anomaly.id}",
            json={
                "resolved": True,
                "resolution_note": "Conferido com fornecedor.",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["resolved"] is True
        assert body["resolution_note"] == "Conferido com fornecedor."

    async def test_filter_resolved_true_returns_only_resolved(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        # 1 resolvida + 1 pendente
        db_session.add(
            ReconciliationAnomaly(
                session_id=sess.id,
                anomaly_type_id=types["wrong_account"].id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=True,
            )
        )
        db_session.add(
            ReconciliationAnomaly(
                session_id=sess.id,
                anomaly_type_id=types["wrong_account"].id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            params={"resolved": "true"},
        )
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert all(item["resolved"] is True for item in rows)
        assert len(rows) == 1


@pytest.mark.integration
class TestAnomalyChronologicalOrder:
    """Sugestão 2 da Bruna (04/08/2026): a lista abre em ordem cronológica.

    A conferência se faz na ordem em que o dinheiro se moveu. A anomalia não tem
    data própria — ela vem da `file_entry` ou do `omie_entry` vinculado, e as que
    não têm vínculo nenhum vão para o FIM.
    """

    @staticmethod
    def _effective_date(row: dict[str, Any]) -> str | None:
        """A data que a ordenação usa: a do arquivo, a do Omie, ou nenhuma."""
        if row["related_file_entry"] is not None:
            return str(row["related_file_entry"]["transaction_date"])
        if row["related_omie_entry"] is not None:
            return str(row["related_omie_entry"]["transaction_date"])
        return None

    async def _seed_mixed(
        self,
        db_session: AsyncSession,
        *,
        reconciliation: ReconciliationSession,
        types: dict[str, AnomalyType],
    ) -> None:
        """Cria anomalias FORA de ordem, com as três origens de data."""
        # 20/07 — via omie_entry.
        oe = await _seed_omie_entry(
            db_session,
            reconciliation=reconciliation,
            omie_lancamento_id=9001,
            tx_date=date(2026, 7, 20),
        )
        # 05/07 e 12/07 — via file_entry.
        fe_05 = await _seed_file_entry(
            db_session,
            reconciliation=reconciliation,
            description="cinco de julho",
            amount=Decimal("-10.00"),
            tx_date=date(2026, 7, 5),
        )
        fe_12 = await _seed_file_entry(
            db_session,
            reconciliation=reconciliation,
            description="doze de julho",
            amount=Decimal("-20.00"),
            tx_date=date(2026, 7, 12),
        )
        # Inseridas propositalmente na ordem errada: 20, sem-data, 12, 05.
        db_session.add(
            ReconciliationAnomaly(
                session_id=reconciliation.id,
                anomaly_type_id=types["wrong_account"].id,
                omie_entry_id=oe.id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        db_session.add(
            ReconciliationAnomaly(
                session_id=reconciliation.id,
                anomaly_type_id=types["missing_in_omie"].id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        db_session.add(
            ReconciliationAnomaly(
                session_id=reconciliation.id,
                anomaly_type_id=types["wrong_account"].id,
                file_entry_id=fe_12.id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        db_session.add(
            ReconciliationAnomaly(
                session_id=reconciliation.id,
                anomaly_type_id=types["wrong_account"].id,
                file_entry_id=fe_05.id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        await db_session.flush()

    async def test_lista_abre_em_ordem_cronologica_com_sem_data_no_fim(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        await self._seed_mixed(db_session, reconciliation=sess, types=types)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/anomalies")

        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) == 4
        # Inseridas como 20/07, sem-data, 12/07, 05/07 — saem cronológicas, e a
        # sem-vínculo por ÚLTIMA. Quem abre a tela não pode esbarrar primeiro
        # justamente na anomalia que não tem contexto.
        assert [self._effective_date(r) for r in rows] == [
            "2026-07-05",
            "2026-07-12",
            "2026-07-20",
            None,
        ]

    async def test_ordem_atravessa_a_paginacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A ordenação roda no SQL — página 2 continua de onde a 1 parou.

        Se alguém mover a ordenação para o cliente, este teste quebra: ordenar
        só a página carregada faz as duas páginas se sobreporem.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        await self._seed_mixed(db_session, reconciliation=sess, types=types)
        await _login(client_with_db, ADMIN_EMAIL)

        p1 = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            params={"page": 1, "pageSize": 2},
        )
        p2 = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            params={"page": 2, "pageSize": 2},
        )

        assert p1.status_code == 200
        assert p2.status_code == 200
        rows_p1 = p1.json()["data"]
        rows_p2 = p2.json()["data"]
        assert [self._effective_date(r) for r in rows_p1] == ["2026-07-05", "2026-07-12"]
        assert [self._effective_date(r) for r in rows_p2] == ["2026-07-20", None]
        # Sem sobreposição e sem buraco: as 4 anomalias, cada uma uma vez.
        ids_p1 = {r["id"] for r in rows_p1}
        ids_p2 = {r["id"] for r in rows_p2}
        assert ids_p1.isdisjoint(ids_p2)
        assert len(ids_p1 | ids_p2) == 4

    async def test_filtro_de_severidade_combina_com_a_ordem(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        await self._seed_mixed(db_session, reconciliation=sess, types=types)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            params={"severity": AnomalySeverity.MODERATE.value},
        )

        assert resp.status_code == 200
        rows = resp.json()["data"]
        # As 3 `wrong_account` (moderate); a `missing_in_omie` (critical) fora.
        assert len(rows) == 3
        assert all(r["anomaly_type"]["severity"] == AnomalySeverity.MODERATE.value for r in rows)
        # E continuam cronológicas dentro do filtro.
        assert [self._effective_date(r) for r in rows] == [
            "2026-07-05",
            "2026-07-12",
            "2026-07-20",
        ]


# ----------------------------------------------------------------------
# BACK 9.10 — GET /api/v1/anomaly-types
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestAnomalyTypes:
    async def test_lists_only_active_sorted_by_severity(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_client(db_session, creator=admin)
        types = await _seed_anomaly_types(db_session)
        # inativa o "wrong_account"
        types["wrong_account"].active = False
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        codes = [r["code"] for r in rows]
        assert "wrong_account" not in codes
        assert "missing_in_omie" in codes
        # Critical primeiro
        severities = [r["severity"] for r in rows]
        assert severities == sorted(
            severities,
            key=lambda s: {"critical": 1, "moderate": 2, "info": 3}.get(s, 99),
        )

    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 401


# ----------------------------------------------------------------------
# Sanity — rotas registradas
# ----------------------------------------------------------------------


def test_review_routes_registered() -> None:
    from app.main import app as fastapi_app

    paths = {route.path for route in fastapi_app.routes}  # type: ignore[attr-defined]
    expected = {
        "/api/v1/reconciliations/{session_id}/file-entries",
        "/api/v1/reconciliations/{session_id}/file-entries/{entry_id}",
        "/api/v1/reconciliations/{session_id}/available-omie-entries",
        "/api/v1/reconciliations/{session_id}/omie-entries",
        "/api/v1/reconciliations/{session_id}/omie-entries/{entry_id}",
        "/api/v1/reconciliations/{session_id}/anomalies",
        "/api/v1/reconciliations/{session_id}/anomalies/{anomaly_id}",
        "/api/v1/omie/lancamentos",
        "/api/v1/anomaly-types",
    }
    assert expected.issubset(paths)


# ----------------------------------------------------------------------
# Garante que cleanup do `_seed_user` ainda enxerga o UUID do row.
# (sanidade que `client.id` é UUID)
# ----------------------------------------------------------------------


def test_uuid_type_sanity() -> None:
    assert isinstance(uuid4(), UUID)


# ----------------------------------------------------------------------
# Service `list_available_omie_entries` — período usado (S11 fix)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestAvailableOmieEntriesPeriod:
    """Cobre o fix de §S11: `list_available_omie_entries` usa o período REAL
    da sessão quando disponível e cai no fallback `[reference_month,
    last_day_of_month]` para sessões pré-migration (period_start IS NULL).

    Unit-style — exercício direto do service com OmieClient e cache
    mockados, sem subir HTTP. O foco é o período passado a
    `populate_from_extrato`.
    """

    async def test_uses_real_period_when_persisted(self, db_session: AsyncSession) -> None:
        from unittest.mock import AsyncMock

        from app.modules.reconciliations.review.repository import ReviewRepository
        from app.modules.reconciliations.review.service import ReviewService

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # Período real do statement — extrato "quebrado" (15/04 → 14/05).
        sess.period_start = date(2026, 4, 15)
        sess.period_end = date(2026, 5, 14)
        # FASE 1: sessões novas gravam 0 na coluna — a expansão tem de vir do
        # range FIXO (86e2z895j), não dela; 0 aqui trava essa garantia.
        sess.date_tolerance_days = 0
        await db_session.flush()

        cache = AsyncMock()
        cache.populate_from_extrato.return_value = {}
        omie_client = AsyncMock()
        service = ReviewService(
            ReviewRepository(db_session),
            cache=cache,
            settings=get_settings(),
        )

        await service.list_available_omie_entries(
            session=sess,
            omie_client=omie_client,
            search=None,
        )

        # Período expandido = period_real ± DATE_DIVERGENCE_RANGE (3, fixo).
        # period_start=2026-04-15 - 3 = 2026-04-12
        # period_end=2026-05-14 + 3 = 2026-05-17
        call_kwargs = cache.populate_from_extrato.call_args.kwargs
        assert call_kwargs["period_start"] == date(2026, 4, 12)
        assert call_kwargs["period_end"] == date(2026, 5, 17)

    async def test_falls_back_to_month_bounds_when_period_is_null(
        self, db_session: AsyncSession
    ) -> None:
        from unittest.mock import AsyncMock

        from app.modules.reconciliations.review.repository import ReviewRepository
        from app.modules.reconciliations.review.service import ReviewService

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # Sessão pré-migration — period_start/end ficam None.
        assert sess.period_start is None
        assert sess.period_end is None
        # Mesmo racional do teste acima: a janela vem do range fixo.
        sess.date_tolerance_days = 0
        await db_session.flush()

        cache = AsyncMock()
        cache.populate_from_extrato.return_value = {}
        omie_client = AsyncMock()
        service = ReviewService(
            ReviewRepository(db_session),
            cache=cache,
            settings=get_settings(),
        )

        await service.list_available_omie_entries(
            session=sess,
            omie_client=omie_client,
            search=None,
        )

        # Fallback: [2026-04-01, 2026-04-30] ± range fixo(3) → [2026-03-29, 2026-05-03].
        call_kwargs = cache.populate_from_extrato.call_args.kwargs
        assert call_kwargs["period_start"] == date(2026, 3, 29)
        assert call_kwargs["period_end"] == date(2026, 5, 3)


# ----------------------------------------------------------------------
# Service `list_omie_entries` — repopulação do cache no miss (86e2z895j)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestOmieEntriesRepopulateOnMiss:
    """86e2z895j — a aba Divergências Omie repopula o cache L1 no miss.

    O cache é in-memory/por processo com TTL de 2h; o endpoint era
    lookup-only e, expirado o cache (ou noutra réplica), a aba mostrava "—"
    em supplier/category/amount para sempre. Unit-style, como
    `TestAvailableOmieEntriesPeriod`: service direto, cache e OmieClient
    mockados. O que se trava aqui:
        - miss + omie_client → UMA repopulação no período expandido FIXO,
          seguida de novo lookup que enriquece a resposta;
        - cache quente → Omie não é chamado (custo zero no caminho comum);
        - sem omie_client (build falhou na rota) → lookup puro, sem erro;
        - falha do Omie na repopulação → degrada para "—", nunca explode.
    """

    @staticmethod
    def _cached_data() -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            supplier="Moinho Santa Clara",
            category="1.01 Insumos",
            amount=Decimal("-4622.96"),
        )

    @staticmethod
    def _cache_mock() -> Any:
        """AsyncMock com os métodos SÍNCRONOS do cache negativo configurados —
        um `AsyncMock` cru devolveria coroutine em `known_unresolved` e o
        `not in` do service estouraria TypeError."""
        from unittest.mock import AsyncMock, Mock

        cache = AsyncMock()
        cache.known_unresolved = Mock(return_value=set())
        cache.mark_unresolved = Mock()
        return cache

    async def _seed_scene(
        self, db_session: AsyncSession
    ) -> tuple[ReconciliationSession, ReconciliationOmieEntry]:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        sess.period_start = date(2026, 4, 1)
        sess.period_end = date(2026, 4, 30)
        # FASE 1: a coluna zerada NÃO pode encolher a janela da repopulação.
        sess.date_tolerance_days = 0
        await db_session.flush()
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=111)
        return sess, entry

    def _service(self, db_session: AsyncSession, cache: Any) -> Any:
        from app.modules.reconciliations.review.repository import ReviewRepository
        from app.modules.reconciliations.review.service import ReviewService

        return ReviewService(
            ReviewRepository(db_session),
            cache=cache,
            settings=get_settings(),
        )

    async def test_miss_repopula_no_periodo_fixo_e_enriquece(
        self, db_session: AsyncSession
    ) -> None:
        from unittest.mock import AsyncMock

        sess, _ = await self._seed_scene(db_session)
        cache = self._cache_mock()
        # 1º lookup: vazio (TTL expirou). 2º, pós-repopulação: dado presente.
        cache.get_many.side_effect = [{}, {111: self._cached_data()}]
        omie_client = AsyncMock()

        items, _ = await self._service(db_session, cache).list_omie_entries(
            session=sess, page=1, page_size=20, omie_client=omie_client
        )

        cache.populate_from_extrato.assert_awaited_once()
        kwargs = cache.populate_from_extrato.await_args.kwargs
        assert kwargs["omie_client"] is omie_client
        assert kwargs["omie_conta_id"] == sess.omie_conta_id
        # Período REAL ± DATE_DIVERGENCE_RANGE (3, fixo) — não a coluna (0).
        assert kwargs["period_start"] == date(2026, 3, 29)
        assert kwargs["period_end"] == date(2026, 5, 3)
        assert cache.get_many.await_count == 2

        assert items[0].supplier == "Moinho Santa Clara"
        assert items[0].category == "1.01 Insumos"
        assert items[0].amount == Decimal("-4622.96")

    async def test_cache_quente_nao_chama_omie(self, db_session: AsyncSession) -> None:
        from unittest.mock import AsyncMock

        sess, _ = await self._seed_scene(db_session)
        cache = self._cache_mock()
        cache.get_many.return_value = {111: self._cached_data()}
        omie_client = AsyncMock()

        items, _ = await self._service(db_session, cache).list_omie_entries(
            session=sess, page=1, page_size=20, omie_client=omie_client
        )

        cache.populate_from_extrato.assert_not_awaited()
        assert cache.get_many.await_count == 1
        assert items[0].supplier == "Moinho Santa Clara"

    async def test_sem_omie_client_segue_lookup_only(self, db_session: AsyncSession) -> None:

        sess, _ = await self._seed_scene(db_session)
        cache = self._cache_mock()
        cache.get_many.return_value = {}

        items, _ = await self._service(db_session, cache).list_omie_entries(
            session=sess, page=1, page_size=20, omie_client=None
        )

        cache.populate_from_extrato.assert_not_awaited()
        assert items[0].supplier is None
        assert items[0].category is None
        assert items[0].amount is None

    async def test_falha_do_omie_degrada_sem_erro(self, db_session: AsyncSession) -> None:
        from unittest.mock import AsyncMock

        sess, _ = await self._seed_scene(db_session)
        cache = self._cache_mock()
        cache.get_many.return_value = {}
        cache.populate_from_extrato.side_effect = RuntimeError("omie indisponível")

        items, _ = await self._service(db_session, cache).list_omie_entries(
            session=sess, page=1, page_size=20, omie_client=AsyncMock()
        )

        # Sem re-lookup após a falha e sem exceção — a aba renderiza com "—".
        assert cache.get_many.await_count == 1
        assert items[0].supplier is None
        # Falha NÃO marca irresolúvel: o próximo render tenta de novo.
        cache.mark_unresolved.assert_not_called()
        # Data e status continuam vindo do banco, não do cache.
        assert items[0].omie_lancamento_id == 111

    async def test_id_irresoluvel_nao_reconsulta_omie(self, db_session: AsyncSession) -> None:
        """Cache negativo: id que a última repopulação não trouxe (título
        Atrasado/A vencer fora do extrato) não pode custar `ListarExtrato`
        a cada render da aba."""
        from unittest.mock import AsyncMock, Mock

        sess, _ = await self._seed_scene(db_session)
        cache = self._cache_mock()
        cache.get_many.return_value = {}
        cache.known_unresolved = Mock(return_value={111})

        items, _ = await self._service(db_session, cache).list_omie_entries(
            session=sess, page=1, page_size=20, omie_client=AsyncMock()
        )

        cache.populate_from_extrato.assert_not_awaited()
        assert items[0].supplier is None

    async def test_repopulacao_sem_resultado_marca_irresoluvel(
        self, db_session: AsyncSession
    ) -> None:
        from unittest.mock import AsyncMock

        sess, _ = await self._seed_scene(db_session)
        cache = self._cache_mock()
        # Repopulação roda com sucesso, mas o extrato não traz o id.
        cache.get_many.side_effect = [{}, {}]

        await self._service(db_session, cache).list_omie_entries(
            session=sess, page=1, page_size=20, omie_client=AsyncMock()
        )

        cache.populate_from_extrato.assert_awaited_once()
        cache.mark_unresolved.assert_called_once_with(client_id=sess.client_id, omie_ids=[111])


# ----------------------------------------------------------------------
# Bloqueio de revisão quando session.status='error' (S11.fix)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestPaginacaoDaRevisao:
    """Contrato de paginação das 3 rotas da revisão (86e2u512z).

    Elas eram as ÚNICAS do sistema a receber o parâmetro como `page_size` e a
    limitar em 50 — todas as outras usam o alias `pageSize` com teto 100 (§7 do
    CLAUDE.md). O front sempre mandou `pageSize`, então o tamanho de página
    escolhido na tela era descartado pelo servidor, que devolvia 20 em silêncio:
    o seletor "50 por página" das Movimentações não fazia nada, e o rótulo
    "Mostrando 1-50 de N" mentia.

    Estes testes travam as duas metades: o NOME que entra pela URL e o TETO.
    """

    async def _sessao_com_linhas(
        self, db_session: AsyncSession, *, quantas: int
    ) -> ReconciliationSession:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        for i in range(quantas):
            await _seed_file_entry(
                db_session,
                reconciliation=sess,
                description=f"Lançamento {i}",
                amount=Decimal("10.00"),
            )
            await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=9000 + i)
        return sess

    async def test_page_size_chega_pelo_alias_camel_case(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`pageSize` é o nome que o front manda — e o que precisa valer."""
        sess = await self._sessao_com_linhas(db_session, quantas=5)
        await _login(client_with_db, ADMIN_EMAIL)

        for rota in ("file-entries", "omie-entries"):
            resp = await client_with_db.get(
                f"/api/v1/reconciliations/{sess.id}/{rota}", params={"pageSize": 2}
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["data"]) == 2, f"{rota} ignorou pageSize"
            assert body["pagination"]["pageSize"] == 2
            assert body["pagination"]["totalPages"] == 3

    async def test_teto_de_100_aceito_e_101_recusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O teto vira 100, igual ao resto do sistema. 101 é 422, não silêncio."""
        sess = await self._sessao_com_linhas(db_session, quantas=1)
        await _login(client_with_db, ADMIN_EMAIL)

        ok = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries", params={"pageSize": 100}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["pagination"]["pageSize"] == 100

        # O handler global converte erro de validação no envelope §7 da API:
        # 400 + `VALIDATION_ERROR`, nunca o 422 cru do FastAPI.
        estourado = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries", params={"pageSize": 101}
        )
        assert estourado.status_code == 400, estourado.text
        assert estourado.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_anomalias_tambem_respeitam_o_alias(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        for _ in range(3):
            db_session.add(
                ReconciliationAnomaly(
                    session_id=sess.id,
                    anomaly_type_id=types["wrong_account"].id,
                    detected_by=AnomalyDetectedBy.AI.value,
                    resolved=False,
                )
            )
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/anomalies", params={"pageSize": 1}
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 1
        assert resp.json()["pagination"]["totalPages"] == 3


class TestReviewBlockedWhenSessionInError:
    """Sessões em error não devem expor os endpoints de revisão — caller
    (front) deve mostrar a tela de erro + botão Reprocessar antes."""

    async def test_list_file_entries_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin, status="error")
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error"]["code"] == "CONFLICT"
        assert "reprocesse" in body["error"]["userMessage"].lower()

    async def test_list_anomalies_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin, status="error")
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/anomalies")
        assert resp.status_code == 409

    async def test_list_omie_entries_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin, status="error")
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/omie-entries")
        assert resp.status_code == 409

    async def test_get_session_detail_still_works_in_error(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O GET /reconciliations/{id} (não /review) continua acessível —
        o front precisa dele pra renderizar a tela de erro com
        `error_message` antes de oferecer o botão Reprocessar."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin, status="error")
        sess.error_message = "O Omie está com instabilidade no momento."
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "error"
        assert body["error_message"] == "O Omie está com instabilidade no momento."
