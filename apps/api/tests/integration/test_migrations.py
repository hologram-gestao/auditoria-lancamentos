"""Round-trip das migrations Alembic (Sprint 5 — BACK 05.1 / 05.2).

Os testes de schema usam `Base.metadata.create_all` (conftest), o que NÃO exercita
as migrations. Este módulo fecha esse buraco: roda `upgrade head → downgrade -1 →
upgrade head` num banco descartável, provando que a migration da sprint é
reversível de verdade e que o backfill é idempotente.

Roda num banco PRÓPRIO (criado no mesmo container do `pg_container`) para não
colidir com o schema que o `db_engine` monta via `create_all`.

Os testes aqui são **síncronos** de propósito: o `alembic/env.py` chama
`asyncio.run(...)` internamente, o que estouraria dentro de um teste async
(event loop já rodando).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.core.config import get_settings

pytestmark = pytest.mark.integration

# tests/integration/test_migrations.py → apps/api
API_ROOT = Path(__file__).resolve().parents[2]

ROUNDTRIP_DB = "alembic_roundtrip"


@pytest.fixture
def migrations_db_url(db_url: str) -> Iterator[str]:
    """Cria (e derruba) um banco vazio dedicado ao round-trip das migrations."""
    admin_url = db_url
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{ROUNDTRIP_DB}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{ROUNDTRIP_DB}"'))
    try:
        yield admin_url.rsplit("/", 1)[0] + f"/{ROUNDTRIP_DB}"
    finally:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{ROUNDTRIP_DB}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture
def alembic_cfg(migrations_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """Config do Alembic apontando para o banco descartável.

    O `alembic/env.py` lê a URL das Settings (`get_settings()`), não do
    `alembic.ini` — por isso a injeção é via env var + limpeza do cache do
    `lru_cache`, e não `set_main_option("sqlalchemy.url", ...)`.
    """
    monkeypatch.setenv("DATABASE_URL", migrations_db_url)
    get_settings.cache_clear()

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    try:
        yield cfg
    finally:
        get_settings.cache_clear()


def _scalar(url: str, sql: str, **params: object) -> object:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text(sql), params).scalar()
    finally:
        engine.dispose()


def _execute(url: str, sql: str, **params: object) -> None:
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(sql), params)
    finally:
        engine.dispose()


# Revisões da Sprint 5, da mais antiga para a mais nova. Downgrade percorre ao
# contrário. Referenciar por ID (e não por "-1") mantém o teste correto quando
# uma migration nova entrar por cima.
PRE_S5_REV = "c4f1a8b62e93"
S5_TENANCY_REV = "d5c81a4e9b27"
S5_AUDIT_ACTOR_REV = "e9a4b71c3d68"

_INSERT_LEGACY_USER = (
    "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
    "created_at, updated_at) VALUES (gen_random_uuid(), 'Legado', "
    "'legado-rt@hologram.com.br', 'x', 'manager', true, 'system', now(), now())"
)


def _table_exists(url: str, table: str) -> bool:
    return (
        _scalar(
            url,
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_schema = 'public' AND table_name = '{table}'",
        )
        == 1
    )


def _columns(url: str, table: str, *cols: str) -> object:
    placeholders = ", ".join(f"'{c}'" for c in cols)
    return _scalar(
        url,
        "SELECT count(*) FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name IN ({placeholders})",
    )


class TestMigrationRoundTrip:
    def test_upgrade_downgrade_upgrade(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        """As DUAS migrations da sprint sobem, descem e sobem sem perder usuário."""
        url = migrations_db_url

        command.upgrade(alembic_cfg, "head")

        # Um usuário "legado" (como os que já existem na base real) antes do downgrade.
        _execute(url, _INSERT_LEGACY_USER)

        # Desce a da auditoria: as colunas de ator somem, a de tenancy fica.
        command.downgrade(alembic_cfg, S5_TENANCY_REV)
        assert _columns(url, "access_audit", "user_scope", "actor_client_id") == 0
        assert _columns(url, "users", "scope", "client_id") == 2

        # Desce a de tenancy: as colunas somem, o usuário permanece.
        command.downgrade(alembic_cfg, "-1")
        assert _scalar(url, "SELECT count(*) FROM users") == 1
        assert _columns(url, "users", "scope", "client_id") == 0

        command.upgrade(alembic_cfg, "head")

        # Backfill: o usuário pré-existente volta como 'system', sem tenant.
        assert _scalar(url, "SELECT count(*) FROM users") == 1
        assert (
            _scalar(
                url,
                "SELECT count(*) FROM users WHERE scope = 'system' AND client_id IS NULL",
            )
            == 1
        )
        assert _columns(url, "access_audit", "user_scope", "actor_client_id") == 2

    def test_check_constraint_existe_apos_upgrade(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """A integridade scope/client_id é do BANCO, não só da aplicação."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        assert (
            _scalar(
                url,
                "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_users_scope_client_id'",
            )
            == 1
        )
        assert (
            _scalar(
                url,
                "SELECT count(*) FROM pg_indexes "
                "WHERE tablename = 'users' AND indexname = 'ix_users_client_id'",
            )
            == 1
        )

        with pytest.raises(sa.exc.IntegrityError):
            _execute(
                url,
                "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'Bad', "
                "'bad-rt@austral.com.br', 'x', 'client_operator', true, 'client', now(), now())",
            )

    def test_backfill_e_idempotente(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        """Reexecutar o backfill (downgrade+upgrade repetidos) converge, não acumula."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        _execute(
            url,
            "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'Idem', "
            "'idem-rt@hologram.com.br', 'x', 'admin', true, 'system', now(), now())",
        )

        for _ in range(2):
            # Por ID, não "-2": migration nova por cima (Sprint 6) mudaria o
            # alvo relativo e o teste deixaria de descer o backfill da S5.
            command.downgrade(alembic_cfg, PRE_S5_REV)
            command.upgrade(alembic_cfg, "head")

        assert _scalar(url, "SELECT count(*) FROM users") == 1
        assert _scalar(url, "SELECT count(*) FROM users WHERE scope = 'system'") == 1

    def test_backfill_da_auditoria_marca_linhas_antigas_como_system(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """Linha de `access_audit` gravada antes da S5 vira `system` sem tenant."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, S5_AUDIT_ACTOR_REV)
        command.downgrade(alembic_cfg, S5_TENANCY_REV)

        # Linha "da S3": sem as colunas de ator, porque elas ainda não existem.
        _execute(
            url,
            "INSERT INTO access_audit (id, user_id, client_id, action, rota, timestamp) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'view', "
            "'/api/v1/reconciliations/x', now())",
        )

        command.upgrade(alembic_cfg, "head")

        assert (
            _scalar(
                url,
                "SELECT count(*) FROM access_audit "
                "WHERE user_scope = 'system' AND actor_client_id IS NULL",
            )
            == 1
        )


# ----------------------------------------------------------------------
# Sprint 6 — BACK 06.1 (allow-list de dedup em usage_events)
# ----------------------------------------------------------------------

S6_DEDUP_ALLOWLIST_REV = "a1d7f36c9b52"

_INDEXDEF = (
    "SELECT indexdef FROM pg_indexes "
    "WHERE tablename = 'usage_events' AND indexname = 'uq_usage_events_event_session'"
)

_INSERT_EVENT = (
    "INSERT INTO usage_events (id, event, session_id, props, created_at) "
    "VALUES (gen_random_uuid(), :event, :session_id, '{}'::jsonb, now())"
)


class TestDedupAllowListRoundTrip:
    """ADR-010 — o índice parcial passa a listar quem aceita dedup."""

    def test_upgrade_troca_o_predicado_do_indice(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        command.upgrade(alembic_cfg, "head")

        indexdef = str(_scalar(migrations_db_url, _INDEXDEF))
        assert "qualificacao_emitida" not in indexdef
        assert "flag_revisado" not in indexdef
        for event in ("conciliacao_criada", "conciliacao_concluida", "notificacao_entregue"):
            assert event in indexdef

    def test_upgrade_downgrade_upgrade_preserva_as_linhas(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """Round-trip real; o dado do sink sobrevive à ida e à volta."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        # Uma linha de cada lado da allow-list, sem duplicata (o downgrade cabe).
        _execute(url, _INSERT_EVENT, event="conciliacao_criada", session_id=str(uuid4()))
        _execute(url, _INSERT_EVENT, event="qualificacao_emitida", session_id=str(uuid4()))

        command.downgrade(alembic_cfg, S5_AUDIT_ACTOR_REV)
        assert str(_scalar(url, _INDEXDEF)).endswith("(session_id IS NOT NULL)")
        assert _scalar(url, "SELECT count(*) FROM usage_events") == 2

        command.upgrade(alembic_cfg, "head")
        assert _scalar(url, "SELECT count(*) FROM usage_events") == 2
        assert "qualificacao_emitida" not in str(_scalar(url, _INDEXDEF))

    def test_apos_upgrade_a_mesma_sessao_aceita_duas_qualificacoes(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """Contra o schema das MIGRATIONS (não o do `create_all`): 2 linhas, sem erro."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        session_id = str(uuid4())

        _execute(url, _INSERT_EVENT, event="qualificacao_emitida", session_id=session_id)
        _execute(url, _INSERT_EVENT, event="qualificacao_emitida", session_id=session_id)

        assert (
            _scalar(
                url,
                "SELECT count(*) FROM usage_events WHERE event = 'qualificacao_emitida' "
                "AND session_id = :sid",
                sid=session_id,
            )
            == 2
        )

    def test_downgrade_aborta_com_mensagem_acionavel_se_houver_duplicata(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """Recriar o índice antigo sobre dado multi-ocorrência apagaria métrica.

        Escolher qual linha morre é decisão de dado, não de migration — então a
        migration ABORTA, e a mensagem diz exatamente qual consulta rodar.
        """
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        session_id = str(uuid4())
        _execute(url, _INSERT_EVENT, event="qualificacao_emitida", session_id=session_id)
        _execute(url, _INSERT_EVENT, event="qualificacao_emitida", session_id=session_id)

        with pytest.raises(sa.exc.DBAPIError) as exc:
            command.downgrade(alembic_cfg, S5_AUDIT_ACTOR_REV)

        assert "Downgrade bloqueado" in str(exc.value)
        # O índice antigo NÃO foi recriado e nada foi apagado.
        assert _scalar(url, "SELECT count(*) FROM usage_events") == 2


# ----------------------------------------------------------------------
# Sprint 6 — BACK 06.2 (glossário por tenant + glossary_version)
# ----------------------------------------------------------------------

S6_GLOSSARY_REV = "b3e6a91d4c78"

_INSERT_GLOSSARY_ENTRY = (
    "INSERT INTO client_glossary_entries "
    "(id, client_id, kind, name_encrypted, name_iv, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :client_id, :kind, :ct, :iv, now(), now())"
)


def _seed_client_row(url: str) -> str:
    """Um usuário + um cliente mínimos, direto em SQL (sem crypto de app)."""
    user_id = str(uuid4())
    client_id = str(uuid4())
    _execute(
        url,
        "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
        "created_at, updated_at) VALUES (:uid, 'Seed', :email, 'x', 'admin', true, "
        "'system', now(), now())",
        uid=user_id,
        email=f"seed-{user_id}@hologram.com.br",
    )
    _execute(
        url,
        "INSERT INTO clients (id, name, omie_app_key_encrypted, omie_app_key_iv, "
        "omie_app_secret_encrypted, omie_app_secret_iv, active, created_by, "
        "created_at, updated_at) VALUES (:cid, 'Austral', 'ct', '0123456789abcdef01234567', "
        "'ct', '0123456789abcdef01234567', true, :uid, now(), now())",
        cid=client_id,
        uid=user_id,
    )
    return client_id


class TestGlossarioRoundTrip:
    """BACK 06.2 — a migration do glossário sobe, desce e sobe."""

    def test_upgrade_cria_tabela_e_contador_de_versao(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        assert (
            _columns(
                url,
                "client_glossary_entries",
                "client_id",
                "kind",
                "code_encrypted",
                "name_encrypted",
                "description_encrypted",
                "deleted_at",
            )
            == 6
        )
        assert _columns(url, "clients", "glossary_version") == 1
        # Cliente pré-existente nasce com versão 0 (server_default), não NULL.
        client_id = _seed_client_row(url)
        assert (
            _scalar(
                url,
                "SELECT glossary_version FROM clients WHERE id = :cid",
                cid=client_id,
            )
            == 0
        )

    def test_upgrade_downgrade_upgrade_preserva_os_clientes(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """O downgrade descarta o glossário (feature nova), mas não o cliente."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        client_id = _seed_client_row(url)
        _execute(
            url,
            _INSERT_GLOSSARY_ENTRY,
            client_id=client_id,
            kind="categoria",
            ct="v1:k1:abcd",
            iv="0123456789abcdef01234567",
        )
        _execute(
            url,
            "UPDATE clients SET glossary_version = 7 WHERE id = :cid",
            cid=client_id,
        )

        command.downgrade(alembic_cfg, S6_DEDUP_ALLOWLIST_REV)
        assert _columns(url, "clients", "glossary_version") == 0
        assert (
            _scalar(
                url,
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'client_glossary_entries'",
            )
            == 0
        )
        assert _scalar(url, "SELECT count(*) FROM clients") == 1

        command.upgrade(alembic_cfg, "head")
        assert _scalar(url, "SELECT count(*) FROM clients") == 1
        assert _columns(url, "clients", "glossary_version") == 1
        # Reversão é reversão: a versão volta ao default, não ao valor antigo.
        assert (
            _scalar(url, "SELECT glossary_version FROM clients WHERE id = :cid", cid=client_id) == 0
        )

    def test_entradas_sobrevivem_ao_ciclo_quando_o_downgrade_nao_e_aplicado(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """`upgrade head` duas vezes é no-op — as entradas continuam lá."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        client_id = _seed_client_row(url)
        for kind in ("categoria", "fornecedor", "regra"):
            _execute(
                url,
                _INSERT_GLOSSARY_ENTRY,
                client_id=client_id,
                kind=kind,
                ct="v1:k1:abcd",
                iv="0123456789abcdef01234567",
            )

        command.upgrade(alembic_cfg, "head")

        assert _scalar(url, "SELECT count(*) FROM client_glossary_entries") == 3

    def test_fk_do_cliente_e_cascade(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        """Remover o cliente leva o glossário junto — não trava a remoção."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        client_id = _seed_client_row(url)
        _execute(
            url,
            _INSERT_GLOSSARY_ENTRY,
            client_id=client_id,
            kind="regra",
            ct="v1:k1:abcd",
            iv="0123456789abcdef01234567",
        )

        _execute(url, "DELETE FROM clients WHERE id = :cid", cid=client_id)

        assert _scalar(url, "SELECT count(*) FROM client_glossary_entries") == 0


# ----------------------------------------------------------------------
# Sprint 6 — BACK 06.5 (veredito do revisor + flag de glossário na sessão)
# ----------------------------------------------------------------------

S6_REVIEW_VERDICT_REV = "c5a2f81b6d34"


class TestVereditoDoRevisorRoundTrip:
    def test_upgrade_cria_as_duas_colunas_com_o_default_certo(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        assert _columns(url, "reconciliation_anomalies", "review_verdict") == 1
        assert _columns(url, "reconciliation_sessions", "qualification_used_glossary") == 1
        # `review_verdict` é NULLABLE (todo o histórico fica sem julgamento).
        assert (
            _scalar(
                url,
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'reconciliation_anomalies' "
                "AND column_name = 'review_verdict'",
            )
            == "YES"
        )
        # O flag da sessão é NOT NULL com default false — sessão antiga não quebra.
        assert (
            _scalar(
                url,
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'reconciliation_sessions' "
                "AND column_name = 'qualification_used_glossary'",
            )
            == "NO"
        )

    def test_upgrade_downgrade_upgrade(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        command.downgrade(alembic_cfg, S6_GLOSSARY_REV)
        assert _columns(url, "reconciliation_anomalies", "review_verdict") == 0
        assert _columns(url, "reconciliation_sessions", "qualification_used_glossary") == 0

        command.upgrade(alembic_cfg, "head")
        assert _columns(url, "reconciliation_anomalies", "review_verdict") == 1
        assert _columns(url, "reconciliation_sessions", "qualification_used_glossary") == 1


# Revisões da Sprint 7 (BACK 07.2). Por ID, não por "-1": migration nova por
# cima mudaria o alvo relativo e o teste deixaria de descer a desta sprint.
PRE_S7_REV = "c5a2f81b6d34"
S7_OMIE_POSTINGS_REV = "d7c2b9f14a86"


class TestOmiePostingsRoundTrip:
    """A tabela de intenção de lançamento sobe e desce limpo (Sprint 7 / BACK 07.2).

    A migration é puro DDL aditivo — nenhuma tabela anterior é tocada. O que
    este round-trip prova é que reverter não deixa resto (índices órfãos,
    constraint pendurada) num ambiente que já tenha subido a sprint.
    """

    def test_upgrade_downgrade_upgrade(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        assert _table_exists(url, "reconciliation_omie_postings")

        command.downgrade(alembic_cfg, PRE_S7_REV)
        assert not _table_exists(url, "reconciliation_omie_postings")
        # A tabela que o fluxo REFLETE continua de pé — o rollback é perda de
        # feature, não corrupção de dado pré-existente.
        assert _table_exists(url, "reconciliation_file_entries")

        command.upgrade(alembic_cfg, "head")
        assert _table_exists(url, "reconciliation_omie_postings")

    def test_uniqueness_lives_in_the_database(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """As duas chaves da dedup primária existem NO BANCO, não só no ORM.

        É o ponto inteiro da BACK 07.2: a proteção contra lançar duas vezes não
        depende de o Omie impor unicidade sobre `cCodIntLanc` (S-1, não
        verificado) nem de a aplicação lembrar de checar.
        """
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        for constraint in (
            "uq_recon_omie_postings_file_entry",
            "uq_recon_omie_postings_client_cod_int",
        ):
            assert (
                _scalar(
                    url,
                    "SELECT count(*) FROM pg_constraint "
                    f"WHERE conname = '{constraint}' AND contype = 'u'",
                )
                == 1
            ), f"constraint {constraint} ausente no banco"

    def test_cod_int_lanc_column_respects_omie_string20(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """`cCodIntLanc` é `string20` na Omie — a coluna não pode ser mais larga.

        Uma coluna maior aceitaria em silêncio uma chave que a Omie recusaria
        (ou truncaria, o que é pior: duas linhas com a mesma chave truncada).
        """
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        assert (
            _scalar(
                url,
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'reconciliation_omie_postings' "
                "AND column_name = 'cod_int_lanc'",
            )
            == 20
        )
