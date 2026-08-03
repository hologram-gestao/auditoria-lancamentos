"""Integração — instrumentação de outcome da Sprint 6 (BACK 06.1).

Cobre os critérios de aceite da task:

    - `qualificacao_emitida` e `flag_revisado` ocorrem **N vezes por sessão** e
      NÃO podem ser colapsados pelo índice parcial `uq_usage_events_event_session`
      (ADR-010) — duas emissões da mesma sessão viram DUAS linhas contáveis.
    - Os eventos da Sprint 4, cujo grão é "1 por sessão", CONTINUAM deduplicados:
      é o que prova que o `ON CONFLICT` ainda infere o índice parcial novo (se a
      expressão divergisse, o Postgres devolveria `42P10` — que o fail-soft
      esconderia).
    - `qualify_session` emite um `qualificacao_emitida` por veredito REAL da
      Camada 1, com `com_glossario` DERIVADO do bloco de glossário realmente
      injetado (BACK 06.4).
    - Falha simulada da instrumentação NÃO aborta a transação da qualificação.
    - Nenhum dos 3 eventos é aceito pelo `POST /api/v1/usage-events`.
    - A razão do outcome (`improcedentes ÷ emitidas`) é calculável por SQL sobre
      as linhas gravadas.

Os testes de contagem batem no `UsageEventRepository` DIRETO, sem passar pelo
`UsageEventService`: o service é fail-soft e transformaria um `42P10` em
"gravou 0" silencioso — exatamente o defeito que estes testes existem para pegar.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import ClientCipher, encrypt
from app.core.crypto_service import (
    AAD_FILE_ENTRY_DESCRIPTION,
    field_locator,
    provision_client_cipher,
)
from app.core.security import hash_password
from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    GlossaryEntryKind,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    UsageEvent,
    User,
    UserRole,
)
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.glossary.repository import ClientGlossaryRepository
from app.modules.glossary.service import build_entry
from app.modules.reconciliations.qualification.semantic import QUALIFY_TOOL_NAME
from app.modules.reconciliations.qualification.service import (
    ANOMALY_CODE_PADRAO_QUEBRADO,
    ANOMALY_CODE_QUALIF_INCOERENTE,
    ANOMALY_CODE_QUALIF_SUSPEITA,
    ANOMALY_CODE_VALOR_OUTLIER,
    qualify_session,
)
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import UsageEventName
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
_QUALIF = UsageEventName.QUALIFICACAO_EMITIDA.value
_FLAG = UsageEventName.FLAG_REVISADO.value
_GLOSS = UsageEventName.GLOSSARIO_EDITADO.value


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


# ----------------------------------------------------------------------
# Seeds
# ----------------------------------------------------------------------


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="S6 User",
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


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User, salt: str
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 6, 1),
        date_tolerance_days=0,
        file_hash=_hex64(salt),
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_anomaly_types(session: AsyncSession) -> None:
    seed = [
        (ANOMALY_CODE_QUALIF_SUSPEITA, AnomalySeverity.MODERATE),
        (ANOMALY_CODE_QUALIF_INCOERENTE, AnomalySeverity.CRITICAL),
        (ANOMALY_CODE_PADRAO_QUEBRADO, AnomalySeverity.INFO),
        (ANOMALY_CODE_VALOR_OUTLIER, AnomalySeverity.INFO),
    ]
    for code, severity in seed:
        existing = await session.scalar(select(AnomalyType).where(AnomalyType.code == code))
        if existing is None:
            session.add(
                AnomalyType(
                    code=code,
                    name=code.replace("_", " ").title(),
                    description=f"Seed teste — {code}",
                    severity=severity.value,
                    active=True,
                )
            )
    await session.flush()


async def _cipher(client: Client) -> ClientCipher:
    """`ClientCipher` COM DEK (envelope v1) — o mesmo caminho do runtime.

    `provision_client_cipher` seta `client.dek_wrapped` in-place quando o
    cliente ainda não tem DEK; sem isso, `encrypt` com AAD levanta
    `CryptoError` (não há fallback silencioso — CLAUDE.md §4.1).
    """
    return await provision_client_cipher(client, settings=get_settings())


async def _seed_file_entry(
    session: AsyncSession,
    *,
    recon: ReconciliationSession,
    cipher: ClientCipher,
    description: str,
) -> ReconciliationFileEntry:
    entry_id = uuid4()
    ct, iv = cipher.encrypt(description, field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry_id))
    entry = ReconciliationFileEntry(
        id=entry_id,
        session_id=recon.id,
        transaction_date=date(2026, 6, 3),
        description_encrypted=ct,
        description_iv=iv,
        amount=Decimal("-500.00"),
        situation=FileEntrySituation.CONCILIADO.value,
    )
    session.add(entry)
    await session.flush()
    return entry


# ----------------------------------------------------------------------
# Fake do SDK Anthropic (mesma forma da suíte de qualificação)
# ----------------------------------------------------------------------


class _ToolUseBlock:
    def __init__(self, *, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = QUALIFY_TOOL_NAME
        self.id = "tu_test"
        self.input = payload


class _Usage:
    def __init__(self) -> None:
        self.input_tokens = 1200
        self.output_tokens = 80
        self.cache_read_input_tokens = 1100


class _Message:
    def __init__(self, *, results: list[dict[str, str]]) -> None:
        self.content = [_ToolUseBlock(payload={"results": results})]
        self.usage = _Usage()
        self.stop_reason = "tool_use"


class _FakeMessages:
    def __init__(self, *, message: _Message) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return self._message


class _FakeSdk:
    def __init__(self, *, message: _Message) -> None:
        self.messages = _FakeMessages(message=message)


def _anthropic_with(results: list[dict[str, str]]) -> AnthropicClient:
    settings = get_settings()
    return AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.ANTHROPIC_MODEL_DEFAULT,
        timeout=5.0,
        anthropic_client=_FakeSdk(message=_Message(results=results)),
    )


async def _count(session: AsyncSession, event: str, session_id: UUID | None = None) -> int:
    stmt = select(func.count()).select_from(UsageEvent).where(UsageEvent.event == event)
    if session_id is not None:
        stmt = stmt.where(UsageEvent.session_id == session_id)
    return int(await session.scalar(stmt) or 0)


async def _props(session: AsyncSession, event: str, session_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(UsageEvent.props)
        .where(UsageEvent.event == event, UsageEvent.session_id == session_id)
        .order_by(UsageEvent.created_at)
    )
    return [dict(p) for p in rows.scalars().all()]


# ----------------------------------------------------------------------
# ADR-010 — a allow-list de dedup preserva a contagem da métrica
# ----------------------------------------------------------------------


class TestAllowListDeDedupNoBanco:
    async def test_duas_qualificacoes_da_mesma_sessao_geram_duas_linhas(
        self, db_session: AsyncSession
    ) -> None:
        """O índice parcial NÃO pode colapsar a métrica em silêncio."""
        repo = UsageEventRepository(db_session)
        session_id = uuid4()

        first = await repo.insert_ignore_duplicate(
            event=_QUALIF,
            session_id=session_id,
            props={"veredito": "suspeita", "com_glossario": False},
        )
        second = await repo.insert_ignore_duplicate(
            event=_QUALIF,
            session_id=session_id,
            props={"veredito": "incoerente", "com_glossario": False},
        )

        assert first is True
        assert second is True
        assert await _count(db_session, _QUALIF, session_id) == 2

    async def test_flag_revisado_aceita_duas_marcacoes_na_mesma_sessao(
        self, db_session: AsyncSession
    ) -> None:
        """Uma sessão tem N flags; cada julgamento é uma linha própria."""
        repo = UsageEventRepository(db_session)
        session_id = uuid4()

        await repo.insert_ignore_duplicate(
            event=_FLAG, session_id=session_id, props={"procedente": True}
        )
        await repo.insert_ignore_duplicate(
            event=_FLAG, session_id=session_id, props={"procedente": False}
        )

        assert await _count(db_session, _FLAG, session_id) == 2

    async def test_evento_de_grao_por_sessao_continua_deduplicado(
        self, db_session: AsyncSession
    ) -> None:
        """Prova que o `ON CONFLICT` ainda INFERE o índice parcial novo.

        Se a expressão do `index_where` divergisse do predicado do índice, o
        Postgres levantaria `42P10` — e aqui, sem o fail-soft do service, o
        teste quebraria em vez de passar batido.
        """
        repo = UsageEventRepository(db_session)
        session_id = uuid4()
        event = UsageEventName.CONCILIACAO_CRIADA.value

        first = await repo.insert_ignore_duplicate(
            event=event, session_id=session_id, props={"n_arquivos": 1}
        )
        second = await repo.insert_ignore_duplicate(
            event=event, session_id=session_id, props={"n_arquivos": 1}
        )

        assert (first, second) == (True, False)
        assert await _count(db_session, event, session_id) == 1

    async def test_glossario_editado_nao_tem_sessao_e_nunca_dedup(
        self, db_session: AsyncSession
    ) -> None:
        """`session_id IS NULL` fica fora do índice parcial por construção."""
        service = UsageEventService(UsageEventRepository(db_session))
        client_id = uuid4()

        assert await service.emit_glossario_editado(client_id=client_id, n_categorias=12) is True
        assert await service.emit_glossario_editado(client_id=client_id, n_categorias=13) is True
        assert await _count(db_session, _GLOSS) == 2

    async def test_razao_do_outcome_e_calculavel_por_sql(self, db_session: AsyncSession) -> None:
        """`improcedentes ÷ emitidas` — a fórmula do PRD, sobre as linhas gravadas."""
        service = UsageEventService(UsageEventRepository(db_session))
        session_id = uuid4()

        # 3 vereditos: 1 ok + 2 flags (suspeita, incoerente).
        await service.emit_qualificacao_emitida_many(
            session_id=session_id,
            vereditos=["ok", "suspeita", "incoerente"],
            com_glossario=False,
        )
        # A revisão julga os 2 flags: um procede, o outro não.
        await service.emit_flag_revisado(session_id=session_id, procedente=True)
        await service.emit_flag_revisado(session_id=session_id, procedente=False)

        emitidas = int(
            await db_session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    UsageEvent.event == _QUALIF,
                    UsageEvent.session_id == session_id,
                    UsageEvent.props["veredito"].astext != "ok",
                )
            )
            or 0
        )
        improcedentes = int(
            await db_session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    UsageEvent.event == _FLAG,
                    UsageEvent.session_id == session_id,
                    UsageEvent.props["procedente"].astext == "false",
                )
            )
            or 0
        )

        assert (emitidas, improcedentes) == (2, 1)


# ----------------------------------------------------------------------
# Call site real — qualify_session
# ----------------------------------------------------------------------


class TestQualifySessionEmiteEvento:
    async def _arrange(
        self, db_session: AsyncSession, *, salt: str
    ) -> tuple[ReconciliationSession, Client, ReconciliationFileEntry, ClientCipher]:
        await _seed_anomaly_types(db_session)
        user = await _seed_user(db_session, email=f"s6-{salt}@hologram.com.br")
        client = await _seed_client(db_session, name=f"Austral {salt}", creator=user)
        recon = await _seed_session(db_session, client=client, creator=user, salt=salt)
        cipher = await _cipher(client)
        await db_session.flush()
        entry = await _seed_file_entry(
            db_session, recon=recon, cipher=cipher, description="TARIFA BANCARIA"
        )
        return recon, client, entry, cipher

    async def test_emite_um_evento_por_veredito_real(self, db_session: AsyncSession) -> None:
        recon, client, entry, cipher = await self._arrange(db_session, salt="veredito")

        report = await qualify_session(
            db_session,
            session_id=recon.id,
            client_id=client.id,
            match_pairs=[(entry.id, 777)],
            cache=OmieLancamentoCache(),
            anthropic_client=_anthropic_with(
                [{"pair_id": str(entry.id), "status": "incoerente", "motivo": "tarifa ≠ receita"}]
            ),
            cipher=cipher,
        )

        assert report.incoerentes == 1
        props = await _props(db_session, _QUALIF, recon.id)
        assert props == [{"veredito": "incoerente", "com_glossario": False}]

    async def test_com_glossario_reflete_o_bloco_realmente_injetado(
        self, db_session: AsyncSession
    ) -> None:
        """BACK 06.4: o valor é DERIVADO do bloco montado, não afirmado pelo caller.

        Um booleano vindo de fora poderia mentir (default esquecido, caller
        enganado) e a leitura D+30 compararia "antes x depois" sobre uma flag
        que não corresponde ao prompt que rodou.
        """
        recon, client, entry, cipher = await self._arrange(db_session, salt="glossario")
        repo = ClientGlossaryRepository(db_session)
        await repo.add(
            build_entry(
                client_id=client.id,
                kind=GlossaryEntryKind.REGRA,
                name="IOF nunca e juros",
                code=None,
                description=None,
                cipher=cipher,
            )
        )

        await qualify_session(
            db_session,
            session_id=recon.id,
            client_id=client.id,
            match_pairs=[(entry.id, 777)],
            cache=OmieLancamentoCache(),
            anthropic_client=_anthropic_with(
                [{"pair_id": str(entry.id), "status": "ok", "motivo": "coerente"}]
            ),
            cipher=cipher,
        )

        props = await _props(db_session, _QUALIF, recon.id)
        assert props == [{"veredito": "ok", "com_glossario": True}]

    async def test_falha_da_instrumentacao_nao_aborta_a_qualificacao(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SAVEPOINT + fail-soft: o sink quebra, a anomalia continua persistindo."""
        recon, client, entry, cipher = await self._arrange(db_session, salt="failsoft")

        async def _boom(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("sink fora do ar")

        monkeypatch.setattr(UsageEventRepository, "insert_many_ignore_duplicate", _boom)

        report = await qualify_session(
            db_session,
            session_id=recon.id,
            client_id=client.id,
            match_pairs=[(entry.id, 777)],
            cache=OmieLancamentoCache(),
            anthropic_client=_anthropic_with(
                [{"pair_id": str(entry.id), "status": "incoerente", "motivo": "tarifa ≠ receita"}]
            ),
            cipher=cipher,
        )

        assert report.incoerentes == 1
        # A transação de negócio sobreviveu: a anomalia está gravada e legível.
        anomalias = await db_session.scalar(
            select(func.count())
            .select_from(ReconciliationAnomaly)
            .where(ReconciliationAnomaly.session_id == recon.id)
        )
        assert anomalias == 1
        assert await _count(db_session, _QUALIF, recon.id) == 0


# ----------------------------------------------------------------------
# Borda HTTP — nenhum dos 3 eventos é forjável pelo browser
# ----------------------------------------------------------------------


class TestEventosDeBackendNaoEntramPeloEndpoint:
    @pytest.mark.parametrize(
        ("event", "props"),
        [
            (_QUALIF, {"veredito": "suspeita", "com_glossario": True}),
            (_FLAG, {"procedente": False}),
            (_GLOSS, {"client_id": "3f7b1e2a-0000-4000-8000-0000000000c1", "n_categorias": 1}),
        ],
        ids=[_QUALIF, _FLAG, _GLOSS],
    )
    async def test_evento_de_backend_e_recusado(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        event: str,
        props: dict[str, Any],
    ) -> None:
        """Aceitar do cliente permitiria forjar numerador E denominador da métrica."""
        user = await _seed_user(db_session, email=f"s6-http-{event}@hologram.com.br")
        client = await _seed_client(db_session, name=f"HTTP {event}", creator=user)
        recon = await _seed_session(db_session, client=client, creator=user, salt=f"http-{event}")
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PLAIN_PASSWORD},
        )
        assert resp.status_code == 200, resp.text

        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={"event": event, "session_id": str(recon.id), "props": props},
        )

        assert resp.status_code == 400, resp.text
        assert await _count(db_session, event) == 0
