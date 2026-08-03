"""Integração — veredito do revisor sobre o flag + selo do glossário (BACK 06.5).

Cobre os critérios de aceite da task:

    - Revisor marca um flag da qualificação como procedente/improcedente e o
      estado persiste; remarcar ATUALIZA sem duplicar linha.
    - Marcar anomalia que NÃO é da qualificação é recusado no servidor, com
      código canônico e `userMessage` em português.
    - Cada marcação emite `flag_revisado {session_id, procedente}` sem PII; a
      contagem permite calcular improcedentes ÷ emitidas (2 flags distintos da
      mesma sessão → 2 linhas).
    - A rota é escopada por tenant: anomalia de outro tenant devolve 404 sem
      vazar dado.
    - O contrato de leitura da revisão expõe se o veredito considerou o
      glossário, com valor REAL vindo do caminho da BACK 06.4.
    - Sessão de cliente SEM glossário: campo `false` e nenhuma regressão.

A rota é a mesma `PATCH .../anomalies/{id}` que já existia (ADR-014: o eixo
novo ESTENDE o caminho de revisão, não cria um paralelo) — então ela já está na
lista canônica de `sensitive_endpoints.py` e já é coberta pelo caso negativo
cross-tenant parametrizado de `test_sensitive_endpoints.py`.
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
    AAD_ANOMALY_CONTEXT,
    AAD_FILE_ENTRY_DESCRIPTION,
    field_locator,
    provision_client_cipher,
)
from app.core.security import hash_password
from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    UsageEvent,
    User,
    UserRole,
)
from app.modules.reconciliations.processing.anomalies import ANOMALY_CODE_MISSING_IN_OMIE
from app.modules.reconciliations.qualification.service import (
    ANOMALY_CODE_QUALIF_INCOERENTE,
    ANOMALY_CODE_QUALIF_SUSPEITA,
)
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import UsageEventName

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
SECRET_NAME_B = "Fulana Participacoes LTDA"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_admin(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="Revisor",
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


async def _seed_types(session: AsyncSession) -> dict[str, AnomalyType]:
    out: dict[str, AnomalyType] = {}
    for code, severity in (
        (ANOMALY_CODE_QUALIF_SUSPEITA, AnomalySeverity.MODERATE),
        (ANOMALY_CODE_QUALIF_INCOERENTE, AnomalySeverity.CRITICAL),
        (ANOMALY_CODE_MISSING_IN_OMIE, AnomalySeverity.CRITICAL),
    ):
        existing = await session.scalar(select(AnomalyType).where(AnomalyType.code == code))
        if existing is None:
            existing = AnomalyType(
                code=code, name=code, description="seed", severity=severity.value, active=True
            )
            session.add(existing)
            await session.flush()
        out[code] = existing
    return out


class _Scene:
    def __init__(
        self,
        client: Client,
        recon: ReconciliationSession,
        cipher: ClientCipher,
        types: dict[str, AnomalyType],
    ) -> None:
        self.client = client
        self.recon = recon
        self.cipher = cipher
        self.types = types


async def _scene(session: AsyncSession, *, name: str, salt: str, email: str) -> _Scene:
    admin = await _seed_admin(session, email=email)
    client = await _seed_client(session, name=name, creator=admin)
    cipher = await provision_client_cipher(client, settings=get_settings())
    await session.flush()
    recon = ReconciliationSession(
        client_id=client.id,
        created_by=admin.id,
        omie_conta_id=42,
        reference_month=date(2026, 6, 1),
        date_tolerance_days=0,
        file_hash=_hex64(salt),
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(recon)
    await session.flush()
    types = await _seed_types(session)
    return _Scene(client, recon, cipher, types)


async def _seed_anomaly(
    session: AsyncSession, scene: _Scene, *, code: str, motivo: str = "tarifa nao e receita"
) -> ReconciliationAnomaly:
    """Anomalia ancorada numa `file_entry` real (XOR do modelo)."""
    entry_id = uuid4()
    ct_desc, iv_desc = scene.cipher.encrypt(
        "TARIFA BANCARIA", field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry_id)
    )
    session.add(
        ReconciliationFileEntry(
            id=entry_id,
            session_id=scene.recon.id,
            transaction_date=date(2026, 6, 3),
            description_encrypted=ct_desc,
            description_iv=iv_desc,
            amount=Decimal("-100.00"),
            situation=FileEntrySituation.CONCILIADO.value,
        )
    )
    anomaly_id = uuid4()
    ct, iv = scene.cipher.encrypt(motivo, field_locator(AAD_ANOMALY_CONTEXT, anomaly_id))
    anomaly = ReconciliationAnomaly(
        id=anomaly_id,
        session_id=scene.recon.id,
        anomaly_type_id=scene.types[code].id,
        file_entry_id=entry_id,
        detected_by=AnomalyDetectedBy.AI.value,
        context_encrypted=ct,
        context_iv=iv,
        resolved=False,
    )
    session.add(anomaly)
    await session.flush()
    return anomaly


async def _login(http: AsyncClient, email: str) -> None:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD})
    assert resp.status_code == 200, resp.text


def _url(scene: _Scene, anomaly_id: UUID) -> str:
    return f"/api/v1/reconciliations/{scene.recon.id}/anomalies/{anomaly_id}"


async def _flag_revisado(session: AsyncSession, session_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(UsageEvent.props)
        .where(
            UsageEvent.event == UsageEventName.FLAG_REVISADO.value,
            UsageEvent.session_id == session_id,
        )
        .order_by(UsageEvent.created_at)
    )
    return [dict(p) for p in rows.scalars().all()]


# ----------------------------------------------------------------------
# Marcação procedente / improcedente
# ----------------------------------------------------------------------


class TestMarcacaoDoFlag:
    async def test_marca_e_persiste(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session, name="Austral", salt="mark", email="rev1@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev1@hologram.com.br")

        resp = await client_with_db.patch(
            _url(scene, anomaly.id), json={"review_verdict": "improcedente"}
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["review_verdict"] == "improcedente"
        # `resolved` NÃO foi tocado: são eixos independentes.
        assert resp.json()["data"]["resolved"] is False
        await db_session.refresh(anomaly)
        assert anomaly.review_verdict == "improcedente"

    async def test_remarcar_atualiza_sem_duplicar_linha(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        scene = await _scene(
            db_session, name="Austral", salt="remark", email="rev2@hologram.com.br"
        )
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_INCOERENTE)
        await _login(client_with_db, "rev2@hologram.com.br")

        await client_with_db.patch(_url(scene, anomaly.id), json={"review_verdict": "improcedente"})
        resp = await client_with_db.patch(
            _url(scene, anomaly.id), json={"review_verdict": "procedente"}
        )

        assert resp.json()["data"]["review_verdict"] == "procedente"
        total = await db_session.scalar(
            select(func.count(ReconciliationAnomaly.id)).where(
                ReconciliationAnomaly.session_id == scene.recon.id
            )
        )
        assert total == 1  # atualizou a linha, não criou outra

    async def test_marcar_e_resolver_sao_eixos_independentes(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Enviar só `resolved` continua funcionando (contrato antigo intacto)."""
        scene = await _scene(db_session, name="Austral", salt="eixos", email="rev3@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev3@hologram.com.br")

        await client_with_db.patch(_url(scene, anomaly.id), json={"review_verdict": "improcedente"})
        resp = await client_with_db.patch(
            _url(scene, anomaly.id),
            json={"resolved": True, "resolution_note": "Fechado sem acao no Omie."},
        )

        # Resolver não apaga o veredito, e vice-versa.
        assert resp.json()["data"]["resolved"] is True
        assert resp.json()["data"]["review_verdict"] == "improcedente"

    async def test_corpo_vazio_e_recusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session, name="Austral", salt="vazio", email="rev4@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev4@hologram.com.br")

        resp = await client_with_db.patch(_url(scene, anomaly.id), json={})

        assert resp.status_code == 400, resp.text

    async def test_veredito_fora_do_enum_e_recusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session, name="Austral", salt="enum", email="rev5@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev5@hologram.com.br")

        resp = await client_with_db.patch(
            _url(scene, anomaly.id), json={"review_verdict": "talvez"}
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


class TestTipoDeAnomalia:
    async def test_anomalia_que_nao_e_da_qualificacao_e_recusada(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`missing_in_omie` não entra no denominador — julgá-la distorceria a razão."""
        scene = await _scene(db_session, name="Austral", salt="tipo", email="rev6@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_MISSING_IN_OMIE)
        await _login(client_with_db, "rev6@hologram.com.br")

        resp = await client_with_db.patch(
            _url(scene, anomaly.id), json={"review_verdict": "improcedente"}
        )

        assert resp.status_code == 400, resp.text
        erro = resp.json()["error"]
        assert erro["code"] == "VALIDATION_ERROR"
        # Mensagem ACIONÁVEL e em português (CLAUDE.md §7 idioma).
        assert "procedente" in erro["userMessage"]
        assert "classificação" in erro["userMessage"]
        await db_session.refresh(anomaly)
        assert anomaly.review_verdict is None

    async def test_resolver_anomalia_estrutural_continua_funcionando(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Sem regressão: o eixo `resolved` vale para QUALQUER tipo."""
        scene = await _scene(
            db_session, name="Austral", salt="estrutural", email="rev7@hologram.com.br"
        )
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_MISSING_IN_OMIE)
        await _login(client_with_db, "rev7@hologram.com.br")

        resp = await client_with_db.patch(
            _url(scene, anomaly.id),
            json={"resolved": True, "resolution_note": "Lancado manualmente no Omie."},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["resolved"] is True


# ----------------------------------------------------------------------
# Evento de outcome
# ----------------------------------------------------------------------


class TestEventoFlagRevisado:
    async def test_dois_flags_distintos_da_mesma_sessao_geram_duas_linhas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """É esta contagem que permite calcular improcedentes ÷ emitidas."""
        scene = await _scene(db_session, name="Austral", salt="dois", email="rev8@hologram.com.br")
        a1 = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        a2 = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_INCOERENTE)
        await _login(client_with_db, "rev8@hologram.com.br")

        await client_with_db.patch(_url(scene, a1.id), json={"review_verdict": "improcedente"})
        await client_with_db.patch(_url(scene, a2.id), json={"review_verdict": "procedente"})

        eventos = await _flag_revisado(db_session, scene.recon.id)
        assert eventos == [{"procedente": False}, {"procedente": True}]

    async def test_remarcar_com_o_mesmo_valor_nao_emite_de_novo(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """ADR-010: grão do evento é a MUDANÇA — reenvio não infla o denominador."""
        scene = await _scene(db_session, name="Austral", salt="idem", email="rev9@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev9@hologram.com.br")

        for _ in range(3):
            await client_with_db.patch(
                _url(scene, anomaly.id), json={"review_verdict": "improcedente"}
            )

        assert await _flag_revisado(db_session, scene.recon.id) == [{"procedente": False}]

    async def test_mudar_de_ideia_emite_de_novo(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """...e a mudança não some: o par é (mudança emitida, estado vigente)."""
        scene = await _scene(db_session, name="Austral", salt="flip", email="rev10@hologram.com.br")
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev10@hologram.com.br")

        await client_with_db.patch(_url(scene, anomaly.id), json={"review_verdict": "improcedente"})
        await client_with_db.patch(_url(scene, anomaly.id), json={"review_verdict": "procedente"})

        eventos = await _flag_revisado(db_session, scene.recon.id)
        assert eventos == [{"procedente": False}, {"procedente": True}]
        await db_session.refresh(anomaly)
        assert anomaly.review_verdict == "procedente"

    async def test_evento_nao_carrega_pii(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session, name="Austral", salt="pii", email="rev11@hologram.com.br")
        anomaly = await _seed_anomaly(
            db_session,
            scene,
            code=ANOMALY_CODE_QUALIF_SUSPEITA,
            motivo="PAG PIX MOINHO PRADO classificado como receita",
        )
        await _login(client_with_db, "rev11@hologram.com.br")

        await client_with_db.patch(_url(scene, anomaly.id), json={"review_verdict": "improcedente"})

        eventos = await _flag_revisado(db_session, scene.recon.id)
        assert eventos == [{"procedente": False}]
        assert "MOINHO" not in str(eventos)

    async def test_falha_do_emissor_nao_derruba_a_marcacao(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scene = await _scene(
            db_session, name="Austral", salt="failsoft", email="rev12@hologram.com.br"
        )
        anomaly = await _seed_anomaly(db_session, scene, code=ANOMALY_CODE_QUALIF_SUSPEITA)

        async def _boom(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("sink fora do ar")

        monkeypatch.setattr(UsageEventRepository, "insert_many_ignore_duplicate", _boom)
        await _login(client_with_db, "rev12@hologram.com.br")

        resp = await client_with_db.patch(
            _url(scene, anomaly.id), json={"review_verdict": "improcedente"}
        )

        assert resp.status_code == 200, resp.text
        await db_session.refresh(anomaly)
        assert anomaly.review_verdict == "improcedente"
        assert await _flag_revisado(db_session, scene.recon.id) == []


# ----------------------------------------------------------------------
# Isolamento por tenant
# ----------------------------------------------------------------------


class TestIsolamento:
    async def test_anomalia_de_outra_sessao_devolve_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Anti-IDOR: o `anomaly_id` existe, mas noutra sessão (outro tenant)."""
        austral = await _scene(
            db_session, name="Austral", salt="isoA", email="rev13a@hologram.com.br"
        )
        fulana = await _scene(
            db_session, name=SECRET_NAME_B, salt="isoB", email="rev13b@hologram.com.br"
        )
        alvo = await _seed_anomaly(db_session, fulana, code=ANOMALY_CODE_QUALIF_SUSPEITA)
        await _login(client_with_db, "rev13a@hologram.com.br")

        resp = await client_with_db.patch(
            _url(austral, alvo.id), json={"review_verdict": "improcedente"}
        )

        assert resp.status_code == 404, resp.text
        assert SECRET_NAME_B not in resp.text
        await db_session.refresh(alvo)
        assert alvo.review_verdict is None


# ----------------------------------------------------------------------
# Selo "o veredito considerou o glossário"
# ----------------------------------------------------------------------


class TestSeloDoGlossario:
    async def test_detalhe_expoe_o_sinal_vindo_do_caminho_real(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Valor REAL da coluna escrita por `qualify_session` (BACK 06.4)."""
        scene = await _scene(db_session, name="Austral", salt="selo", email="rev14@hologram.com.br")
        scene.recon.qualification_used_glossary = True
        await db_session.flush()
        await _login(client_with_db, "rev14@hologram.com.br")

        resp = await client_with_db.get(f"/api/v1/reconciliations/{scene.recon.id}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["qualification_used_glossary"] is True

    async def test_cliente_sem_glossario_vem_false_e_sem_regressao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        scene = await _scene(
            db_session, name="Austral", salt="selo-off", email="rev15@hologram.com.br"
        )
        await _login(client_with_db, "rev15@hologram.com.br")

        detalhe = await client_with_db.get(f"/api/v1/reconciliations/{scene.recon.id}")
        anomalias = await client_with_db.get(f"/api/v1/reconciliations/{scene.recon.id}/anomalies")

        assert detalhe.json()["data"]["qualification_used_glossary"] is False
        # A tela de revisão continua respondendo normalmente.
        assert anomalias.status_code == 200, anomalias.text
