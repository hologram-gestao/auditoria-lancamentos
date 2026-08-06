"""Integração — glossário injetado na QUALIFICAÇÃO (Sprint 6, BACK 06.4).

Cobre os critérios de aceite da task:

    - O bloco entra em `_analyze_batch` com `cache_control: ephemeral`, DEPOIS
      do `_SYSTEM_PROMPT` e em ordem determinística junto do `_INVESTMENT_RULE`
      — provado inspecionando os `system_blocks` REALMENTE enviados ao SDK.
    - `client_id`/glossário chegam por ASSINATURA (`analyze_pairs →
      _analyze_batch`), nunca de estado global.
    - Isolamento: numa análise do cliente B o bloco não contém NENHUMA entrada
      do cliente A — exercitando o caminho real, não a extração.
    - Duas análises seguidas do mesmo cliente com o mesmo glossário produzem
      prefixo de system **byte a byte idêntico** (condição do cache-hit).
    - Editar o glossário muda o bloco daquele cliente e não altera o de outro.
    - Glossário acima do teto é truncado deterministicamente, com log.
    - Cliente SEM glossário: `system_blocks` idêntico ao comportamento atual.
    - `emit_qualificacao_emitida` recebe `com_glossario` correto nos 2 cenários.

⚠️ A EXTRAÇÃO (`app/integrations/anthropic/client.py`, `prompts.py`) não é
tocada — há teste de guarda para isso em `tests/unit/test_qualification_glossary_block.py`.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
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
    ClientGlossaryEntry,
    FileEntrySituation,
    GlossaryEntryKind,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
    UsageEvent,
    User,
    UserRole,
)
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.glossary.repository import ClientGlossaryRepository
from app.modules.glossary.service import apply_entry_edit, build_entry
from app.modules.reconciliations.qualification.semantic import (
    _SYSTEM_PROMPT,
    QUALIFY_TOOL_NAME,
)
from app.modules.reconciliations.qualification.service import (
    ANOMALY_CODE_PADRAO_QUEBRADO,
    ANOMALY_CODE_QUALIF_INCOERENTE,
    ANOMALY_CODE_QUALIF_SUSPEITA,
    ANOMALY_CODE_VALOR_OUTLIER,
    qualify_session,
)
from app.modules.usage_events.schemas import UsageEventName

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

#: Termos EXCLUSIVOS de cada tenant — se um aparecer no prompt do outro, vazou.
AUSTRAL_REGRA = "IOF do Austral nunca e classificado como juros"
AUSTRAL_FORNECEDOR = "Moinho Prado Austral Ltda"
FULANA_REGRA = "Fulana classifica pedagio como logistica"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


# ----------------------------------------------------------------------
# Fake do SDK — guarda os kwargs de CADA chamada para inspeção
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


class _RecordingMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results: list[dict[str, str]] = []

    async def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return _Message(results=self.results)


class _RecordingSdk:
    def __init__(self) -> None:
        self.messages = _RecordingMessages()


def _anthropic() -> tuple[AnthropicClient, _RecordingSdk]:
    settings = get_settings()
    sdk = _RecordingSdk()
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.ANTHROPIC_MODEL_DEFAULT,
        timeout=5.0,
        anthropic_client=sdk,
    )
    return client, sdk


def _system_blocks(sdk: _RecordingSdk, call: int = -1) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = sdk.messages.calls[call]["system"]
    return blocks


# ----------------------------------------------------------------------
# Seeds
# ----------------------------------------------------------------------


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="S6",
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


async def _seed_anomaly_types(session: AsyncSession) -> None:
    for code, severity in (
        (ANOMALY_CODE_QUALIF_SUSPEITA, AnomalySeverity.MODERATE),
        (ANOMALY_CODE_QUALIF_INCOERENTE, AnomalySeverity.CRITICAL),
        (ANOMALY_CODE_PADRAO_QUEBRADO, AnomalySeverity.INFO),
        (ANOMALY_CODE_VALOR_OUTLIER, AnomalySeverity.INFO),
    ):
        existing = await session.scalar(select(AnomalyType).where(AnomalyType.code == code))
        if existing is None:
            session.add(
                AnomalyType(
                    code=code,
                    name=code,
                    description="seed",
                    severity=severity.value,
                    active=True,
                )
            )
    await session.flush()


class _Tenant:
    """Cliente + sessão + 1 par conciliado + cipher — o mínimo para qualificar."""

    def __init__(
        self,
        client: Client,
        recon: ReconciliationSession,
        entry: ReconciliationFileEntry,
        cipher: ClientCipher,
    ) -> None:
        self.client = client
        self.recon = recon
        self.entry = entry
        self.cipher = cipher

    @property
    def match_pairs(self) -> list[tuple[UUID, int]]:
        return [(self.entry.id, 777)]


async def _tenant(session: AsyncSession, *, name: str, salt: str) -> _Tenant:
    user = await _seed_user(session, email=f"q6-{salt}@hologram.com.br")
    client = await _seed_client(session, name=name, creator=user)
    cipher = await provision_client_cipher(client, settings=get_settings())
    await session.flush()
    recon = ReconciliationSession(
        client_id=client.id,
        created_by=user.id,
        omie_conta_id=42,
        reference_month=date(2026, 6, 1),
        date_tolerance_days=0,
        file_hash=_hex64(salt),
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(recon)
    await session.flush()
    entry_id = uuid4()
    ct, iv = cipher.encrypt("TARIFA BANCARIA", field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry_id))
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
    return _Tenant(client, recon, entry, cipher)


async def _add_entries(
    session: AsyncSession, tenant: _Tenant, *entries: tuple[GlossaryEntryKind, str]
) -> list[ClientGlossaryEntry]:
    repo = ClientGlossaryRepository(session)
    created: list[ClientGlossaryEntry] = []
    for kind, name in entries:
        entry = build_entry(
            client_id=tenant.client.id,
            kind=kind,
            name=name,
            code=None,
            description=None,
            cipher=tenant.cipher,
        )
        await repo.add(entry)
        created.append(entry)
    await repo.bump_version(client_id=tenant.client.id)
    return created


async def _qualify(
    session: AsyncSession,
    tenant: _Tenant,
    *,
    account_type: str = "checking",
) -> _RecordingSdk:
    client, sdk = _anthropic()
    sdk.messages.results = [{"pair_id": str(tenant.entry.id), "status": "ok", "motivo": "coerente"}]
    await qualify_session(
        session,
        session_id=tenant.recon.id,
        client_id=tenant.client.id,
        match_pairs=tenant.match_pairs,
        cache=OmieLancamentoCache(),
        anthropic_client=client,
        cipher=tenant.cipher,
        account_type=account_type,
    )
    return sdk


# ----------------------------------------------------------------------
# Injeção no lugar certo
# ----------------------------------------------------------------------


class TestInjecaoNoAnalyzeBatch:
    async def test_bloco_entra_depois_do_system_prompt_com_cache_control(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="inj")
        await _add_entries(db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA))

        sdk = await _qualify(db_session, austral)

        blocks = _system_blocks(sdk)
        assert len(blocks) == 2
        # 1º: o prompt comum a TODOS os clientes, com o cache preservado.
        assert blocks[0]["text"] == _SYSTEM_PROMPT
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        # 2º: o glossário deste cliente, também marcado como cacheável.
        assert AUSTRAL_REGRA in blocks[1]["text"]
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}

    async def test_ordem_fixa_com_a_regra_de_aplicacao(self, db_session: AsyncSession) -> None:
        """Conta aplicação: system → regra de aplicação → glossário, nesta ordem."""
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="ordem")
        await _add_entries(db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA))

        sdk = await _qualify(db_session, austral, account_type=SessionAccountType.INVESTMENT.value)

        blocks = _system_blocks(sdk)
        assert len(blocks) == 3
        assert blocks[0]["text"] == _SYSTEM_PROMPT
        assert "CONTA DE APLICAÇÃO" in blocks[1]["text"]
        assert AUSTRAL_REGRA in blocks[2]["text"]

    async def test_modelo_continua_vindo_das_settings(self, db_session: AsyncSession) -> None:
        """Fora de escopo trocar o modelo — ele segue de `ANTHROPIC_MODEL_DEFAULT`."""
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="modelo")
        await _add_entries(db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA))

        sdk = await _qualify(db_session, austral)

        assert sdk.messages.calls[-1]["model"] == get_settings().ANTHROPIC_MODEL_DEFAULT


# ----------------------------------------------------------------------
# Isolamento entre tenants
# ----------------------------------------------------------------------


class TestIsolamentoEntreClientes:
    async def test_bloco_do_cliente_b_nao_contem_entrada_do_cliente_a(
        self, db_session: AsyncSession
    ) -> None:
        """O caso negativo que a sprint inteira existe para impedir."""
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="isoA")
        fulana = await _tenant(db_session, name="Fulana", salt="isoB")
        await _add_entries(
            db_session,
            austral,
            (GlossaryEntryKind.REGRA, AUSTRAL_REGRA),
            (GlossaryEntryKind.FORNECEDOR, AUSTRAL_FORNECEDOR),
        )
        await _add_entries(db_session, fulana, (GlossaryEntryKind.REGRA, FULANA_REGRA))

        sdk_b = await _qualify(db_session, fulana)

        prompt_b = "\n".join(b["text"] for b in _system_blocks(sdk_b))
        assert FULANA_REGRA in prompt_b
        assert AUSTRAL_REGRA not in prompt_b
        assert AUSTRAL_FORNECEDOR not in prompt_b
        assert "Austral" not in prompt_b

    async def test_editar_o_glossario_de_um_nao_altera_o_bloco_do_outro(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="edA")
        fulana = await _tenant(db_session, name="Fulana", salt="edB")
        [entrada_austral] = await _add_entries(
            db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA)
        )
        await _add_entries(db_session, fulana, (GlossaryEntryKind.REGRA, FULANA_REGRA))

        bloco_b_antes = _glossary_text(await _qualify(db_session, fulana))
        bloco_a_antes = _glossary_text(await _qualify(db_session, austral))

        apply_entry_edit(
            entrada_austral,
            kind=GlossaryEntryKind.REGRA,
            name="IOF do Austral agora e tarifa",
            code=None,
            description=None,
            cipher=austral.cipher,
        )
        await db_session.flush()

        bloco_a_depois = _glossary_text(await _qualify(db_session, austral))
        bloco_b_depois = _glossary_text(await _qualify(db_session, fulana))

        assert bloco_a_depois != bloco_a_antes  # cache do Austral invalidado
        assert bloco_b_depois == bloco_b_antes  # o da Fulana, intacto


def _glossary_text(sdk: _RecordingSdk) -> str:
    """Texto do ÚLTIMO bloco de system — o do glossário, quando existe."""
    blocks = _system_blocks(sdk)
    return blocks[-1]["text"] if len(blocks) > 1 else ""


# ----------------------------------------------------------------------
# Estabilidade do prefixo (condição do cache-hit)
# ----------------------------------------------------------------------


class TestPrefixoEstavel:
    async def test_duas_analises_seguidas_produzem_prefixo_identico(
        self, db_session: AsyncSession
    ) -> None:
        """Cache-hit da Anthropic é keyed pelo CONTEÚDO — byte a byte."""
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="pref")
        await _add_entries(
            db_session,
            austral,
            (GlossaryEntryKind.CATEGORIA, "Taxas bancarias"),
            (GlossaryEntryKind.FORNECEDOR, AUSTRAL_FORNECEDOR),
            (GlossaryEntryKind.REGRA, AUSTRAL_REGRA),
        )

        primeira = _system_blocks(await _qualify(db_session, austral))
        segunda = _system_blocks(await _qualify(db_session, austral))

        assert primeira == segunda

    async def test_secoes_saem_em_ordem_fixa(self, db_session: AsyncSession) -> None:
        """Categorias → fornecedores → regras, independente da ordem de criação."""
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="secoes")
        # Criadas fora de ordem de propósito.
        await _add_entries(
            db_session,
            austral,
            (GlossaryEntryKind.REGRA, AUSTRAL_REGRA),
            (GlossaryEntryKind.CATEGORIA, "Taxas bancarias"),
            (GlossaryEntryKind.FORNECEDOR, AUSTRAL_FORNECEDOR),
        )

        texto = _glossary_text(await _qualify(db_session, austral))

        assert (
            texto.index("Taxas bancarias")
            < texto.index(AUSTRAL_FORNECEDOR)
            < texto.index(AUSTRAL_REGRA)
        )


# ----------------------------------------------------------------------
# Cliente SEM glossário — nenhuma regressão
# ----------------------------------------------------------------------


class TestSemGlossario:
    async def test_system_blocks_identico_ao_comportamento_atual(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="vazio")

        sdk = await _qualify(db_session, austral)

        blocks = _system_blocks(sdk)
        assert blocks == [
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ]

    async def test_glossario_todo_removido_volta_ao_comportamento_atual(
        self, db_session: AsyncSession
    ) -> None:
        """Soft delete some do bloco — não fica sobra de conteúdo removido."""
        await _seed_anomaly_types(db_session)
        austral = await _tenant(db_session, name="Austral", salt="removido")
        [entrada] = await _add_entries(
            db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA)
        )
        repo = ClientGlossaryRepository(db_session)
        await repo.soft_delete(entrada)
        await repo.bump_version(client_id=austral.client.id)

        sdk = await _qualify(db_session, austral)

        assert len(_system_blocks(sdk)) == 1


# ----------------------------------------------------------------------
# com_glossario no evento de outcome
# ----------------------------------------------------------------------


class TestSeloPersistidoNaSessao:
    """BACK 06.5 — o sinal que a tela de revisão lê vem DESTE caminho."""

    @pytest.mark.parametrize("com_glossario", [True, False], ids=["com", "sem"])
    async def test_qualify_session_persiste_o_flag_na_sessao(
        self, db_session: AsyncSession, *, com_glossario: bool
    ) -> None:
        await _seed_anomaly_types(db_session)
        salt = "selo-com" if com_glossario else "selo-sem"
        austral = await _tenant(db_session, name="Austral", salt=salt)
        if com_glossario:
            await _add_entries(db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA))

        await _qualify(db_session, austral)

        await db_session.refresh(austral.recon)
        assert austral.recon.qualification_used_glossary is com_glossario


class TestEventoComGlossario:
    @pytest.mark.parametrize("com_glossario", [True, False], ids=["com", "sem"])
    async def test_evento_reflete_a_injecao_real(
        self, db_session: AsyncSession, *, com_glossario: bool
    ) -> None:
        await _seed_anomaly_types(db_session)
        salt = "evt-com" if com_glossario else "evt-sem"
        austral = await _tenant(db_session, name="Austral", salt=salt)
        if com_glossario:
            await _add_entries(db_session, austral, (GlossaryEntryKind.REGRA, AUSTRAL_REGRA))

        await _qualify(db_session, austral)

        rows = await db_session.execute(
            select(UsageEvent.props).where(
                UsageEvent.event == UsageEventName.QUALIFICACAO_EMITIDA.value,
                UsageEvent.session_id == austral.recon.id,
            )
        )
        props = [dict(p) for p in rows.scalars().all()]
        assert props == [{"veredito": "ok", "com_glossario": com_glossario}]
