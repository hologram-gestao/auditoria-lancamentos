"""Instrumentação do lançamento no Omie (Sprint 7 / BACK 07.5).

A métrica declarada é `linhas lançadas no Omie pelo ADL ÷ linhas conciliadas que
precisam de lançamento`, janela mensal, baseline 0%. Estes testes travam o que
faz esse número ser confiável:

  - o evento é gravado **no backend, no ponto real do fluxo** — o browser não
    consegue forjar nem numerador nem denominador;
  - `props` não tem **nenhum** campo de texto livre: `faultstring` vira família
    categórica e o texto integral fica fora do sink (ADR-031-BE);
  - nenhum dos dois eventos é deduplicado: o mesmo operador manda vários lotes
    na mesma sessão e **cada lote é um fato**;
  - falha do sink **não** derruba o lançamento (que já aconteceu no ERP do
    cliente) — e é sempre logada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import OmieFaultError
from app.core.security import hash_password
from app.db.models import (
    Client,
    FileEntrySituation,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
    UsageEvent,
    User,
    UserRole,
)
from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient
from app.integrations.omie.schemas import IncluirLancCCResponse
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    OmieLancamentoEnviadoProps,
    OmieLancamentoRejeitadoProps,
    UsageEventName,
)
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Evento7#1"
POSTING_URL = "/api/v1/reconciliations/{session_id}/omie-postings"


# ----------------------------------------------------------------------
# Contrato do enum — não precisa de DB
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestEventContract:
    def test_both_events_are_backend_only(self) -> None:
        """Fora de `CLIENT_EMITTED_EVENTS`: o browser não forja a métrica.

        `omie_lancamento_enviado` carrega numerador (`sucesso`) E denominador
        (`linhas`) da sprint. Aceitá-lo do cliente tornaria o resultado
        inverificável.
        """
        assert UsageEventName.OMIE_LANCAMENTO_ENVIADO not in CLIENT_EMITTED_EVENTS
        assert UsageEventName.OMIE_LANCAMENTO_REJEITADO not in CLIENT_EMITTED_EVENTS

    def test_neither_event_is_deduplicated(self) -> None:
        """Evento novo nasce SEM dedup (ADR-010).

        O mesmo operador lança vários lotes na mesma sessão. Deduplicar por
        `(event, session_id)` apagaria o 2º lote **em silêncio** e a métrica
        ficaria menor que a realidade.
        """
        assert UsageEventName.OMIE_LANCAMENTO_ENVIADO.value not in DEDUPED_EVENT_NAMES
        assert UsageEventName.OMIE_LANCAMENTO_REJEITADO.value not in DEDUPED_EVENT_NAMES

    def test_props_reject_free_text(self) -> None:
        """`extra="forbid"`: a chave `faultstring` é RECUSADA, não ignorada."""
        with pytest.raises(Exception, match="faultstring"):
            OmieLancamentoRejeitadoProps(
                codigo="OMIE_FAULT",
                categoria="categoria_invalida",
                faultstring="Categoria de PADARIA PAO QUENTE não cadastrada.",
            )

    def test_enviado_props_reject_free_text_too(self) -> None:
        with pytest.raises(Exception, match="descricao"):
            OmieLancamentoEnviadoProps(
                linhas=1,
                sucesso=1,
                falha=0,
                duracao_ms=10,
                descricao="COMPRA CAFETERIA DO LARGO",
            )

    def test_duracao_is_an_integer(self) -> None:
        """§3.4 — nunca float para grandeza medida."""
        props = OmieLancamentoEnviadoProps(linhas=3, sucesso=2, falha=1, duracao_ms=1234)
        assert isinstance(props.duracao_ms, int)
        assert props.model_dump(mode="json")["duracao_ms"] == 1234


# ----------------------------------------------------------------------
# Seeds
# ----------------------------------------------------------------------


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="Evento",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(f"{FAKE_DEMO_KEY_PREFIX}{uuid4().hex[:8]}", hex_key)
    ct_s, iv_s = encrypt("demo-secret", hex_key)
    client = Client(
        name=f"Cli {uuid4().hex[:6]}",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=900_000_003,
        account_type=SessionAccountType.CREDIT_CARD.value,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=None,
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_entry(
    session: AsyncSession, *, sess: ReconciliationSession, day: int = 15
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt("CAFETERIA DO LARGO", hex_key)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=date(2026, 4, day),
        description_encrypted=ct,
        description_iv=iv,
        amount=Decimal("-12.00"),
        situation=FileEntrySituation.SEM_OMIE.value,
    )
    session.add(entry)
    await session.flush()
    return entry


class Scenario(NamedTuple):
    admin: User
    client: Client
    session: ReconciliationSession


@pytest.fixture
async def scenario(db_session: AsyncSession) -> Scenario:
    admin = await _seed_user(db_session, email=f"ev7-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db_session, creator=admin)
    sess = await _seed_session(db_session, client=client, creator=admin)
    return Scenario(admin, client, sess)


@pytest.fixture
def posting_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMIE_POSTING_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _events(db: AsyncSession, event: UsageEventName) -> list[UsageEvent]:
    return list(
        (await db.execute(select(UsageEvent).where(UsageEvent.event == event.value)))
        .scalars()
        .all()
    )


def _body(entry_id: object, categoria: str = "2.01.03") -> dict:
    return {"lines": [{"file_entry_id": str(entry_id), "cod_categoria": categoria}]}


# ----------------------------------------------------------------------
# Emissão no ponto real do fluxo
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestEmittedOnTheRealPath:
    async def test_one_event_per_executed_batch(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        first = await _seed_entry(db_session, sess=scenario.session, day=10)
        second = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json={
                "lines": [
                    {"file_entry_id": str(first.id), "cod_categoria": "2.01.03"},
                    {"file_entry_id": str(second.id), "cod_categoria": "2.01.03"},
                ]
            },
        )
        assert resp.status_code == 200, resp.text

        events = await _events(db_session, UsageEventName.OMIE_LANCAMENTO_ENVIADO)
        assert len(events) == 1
        event = events[0]
        assert event.session_id == scenario.session.id, "session_id tem de vir pela COLUNA"
        assert event.props["linhas"] == 2
        assert event.props["sucesso"] == 2
        assert event.props["falha"] == 0
        assert isinstance(event.props["duracao_ms"], int)
        assert event.props["duracao_ms"] >= 0
        assert set(event.props) == {"linhas", "sucesso", "falha", "duracao_ms"}

    async def test_two_batches_in_the_same_session_are_two_events(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """Sem dedup: o 2º lote não pode sumir em silêncio."""
        first = await _seed_entry(db_session, sess=scenario.session, day=10)
        second = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)
        url = POSTING_URL.format(session_id=scenario.session.id)

        await client_with_db.post(url, json=_body(first.id))
        await client_with_db.post(url, json=_body(second.id))

        events = await _events(db_session, UsageEventName.OMIE_LANCAMENTO_ENVIADO)
        assert len(events) == 2, "o 2º lote da mesma sessão foi engolido pela dedup"

    async def test_rejection_records_the_category_not_the_text(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A prova central da 07.5: o `faultstring` NÃO entra no sink."""
        entry = await _seed_entry(db_session, sess=scenario.session)
        secret_text = "Categoria de PADARIA PAO QUENTE LTDA (12.345.678/0001-90) não cadastrada."

        async def faulting(self: MockOmieClient, request: object) -> IncluirLancCCResponse:
            raise OmieFaultError("Fault", user_message=secret_text)

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", faulting)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id), json=_body(entry.id, "9.99")
        )
        assert resp.status_code == 200, resp.text

        events = await _events(db_session, UsageEventName.OMIE_LANCAMENTO_REJEITADO)
        assert len(events) == 1
        props = events[0].props
        assert props == {"codigo": "OMIE_FAULT", "categoria": "categoria_invalida"}
        # Nenhum fragmento do texto do fornecedor sobrevive no sink.
        serialized = str(props)
        for fragment in ("PADARIA", "12.345", "cadastrada"):
            assert fragment not in serialized

        # ...e o lote ainda contabiliza a falha no evento agregado.
        enviado = await _events(db_session, UsageEventName.OMIE_LANCAMENTO_ENVIADO)
        assert enviado[0].props == {
            **enviado[0].props,
            "linhas": 1,
            "sucesso": 0,
            "falha": 1,
        }


# ----------------------------------------------------------------------
# O endpoint público recusa os dois
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestClientCannotForgeTheMetric:
    @pytest.mark.parametrize(
        ("event", "props"),
        [
            ("omie_lancamento_enviado", {"linhas": 99, "sucesso": 99, "falha": 0, "duracao_ms": 1}),
            ("omie_lancamento_rejeitado", {"codigo": "OMIE_FAULT", "categoria": "outro"}),
        ],
    )
    async def test_post_usage_events_refuses_both(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        event: str,
        props: dict,
    ) -> None:
        """422 na union discriminada: os dois estão fora da allow-list."""
        await _login(client_with_db, scenario.admin.email)
        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={"event": event, "session_id": str(scenario.session.id), "props": props},
        )
        assert resp.status_code in {400, 422}, resp.text
        assert resp.status_code not in range(200, 300)


# ----------------------------------------------------------------------
# Fail-soft
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestSinkFailureDoesNotBreakThePosting:
    async def test_posting_survives_a_dead_sink(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """O lançamento JÁ está no ERP do cliente — instrumentação não o desfaz."""
        entry = await _seed_entry(db_session, sess=scenario.session)

        async def dead_sink(self: UsageEventRepository, **_kwargs: object) -> bool:
            raise RuntimeError("sink fora do ar")

        monkeypatch.setattr(UsageEventRepository, "insert_ignore_duplicate", dead_sink)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id), json=_body(entry.id)
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["lancadas"] == 1
        await db_session.refresh(entry)
        assert entry.situation == FileEntrySituation.CONCILIADO.value

    async def test_emit_failure_is_logged_never_swallowed(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`except: pass` é proibido — a falha vira warning com o nome do evento."""
        warnings: list[tuple[str, dict]] = []

        service = UsageEventService(UsageEventRepository(db_session))

        async def dead_sink(self: UsageEventRepository, **_kwargs: object) -> bool:
            raise RuntimeError("sink fora do ar")

        def capture(message: str, **kwargs: object) -> None:
            warnings.append((message, dict(kwargs)))

        monkeypatch.setattr(UsageEventRepository, "insert_ignore_duplicate", dead_sink)
        monkeypatch.setattr(
            "app.modules.usage_events.service.logger.warning", capture, raising=False
        )

        recorded = await service.emit_omie_lancamento_enviado(
            session_id=uuid4(), linhas=1, sucesso=1, falha=0, duracao_ms=5
        )

        assert recorded is False
        assert warnings, "a falha do sink foi engolida sem log"
        message, fields = warnings[0]
        assert message == "usage_event_emit_failed"
        assert fields["usage_event"] == "omie_lancamento_enviado"
