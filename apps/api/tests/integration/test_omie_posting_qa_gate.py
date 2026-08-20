"""Gate do QA sobre o lançamento no Omie (Sprint 7 / QA 07.8).

Estes testes NÃO duplicam `test_omie_posting_endpoint.py`; eles fecham quatro
buracos que a revisão encontrou naquela suíte, e cada um corresponde a uma
afirmação do veredito que sem teste seria só leitura:

1. **401 sem autenticação** nas DUAS rotas novas. A suíte do executor entra
   sempre logada; "a rota exige login" estava provado apenas pela presença da
   dependência no código, não pelo comportamento.
2. **Cross-tenant de usuário DE CLIENTE** no lançamento. O executor cobriu o
   `manager` fora da carteira (escopo `system`); o `client_operator` de OUTRO
   tenant — o caminho de vazamento que a Sprint 5 fechou em 34 endpoints — não
   tinha teste nesta rota.
3. **Contagem de POSTs no reenvio.** `test_resending_the_same_line_does_not_
   create_a_second_posting` afirma o desfecho (`bloqueada/ja_lancada`) e o
   número de linhas no banco do ADL — nenhum dos dois vê o fio. O guardrail da
   sprint é "zero lançamento duplicado **no Omie**", e o que prova isso é
   contar as chamadas a `incluir_lanc_cc`. Sem esta asserção, um refactor que
   passasse a reenviar e apenas mantivesse a linha do banco intacta deixaria a
   suíte verde e duplicaria dinheiro na contabilidade do cliente.
4. **Reexecução de lote parcial** — o "Tentar novamente" do resumo da gaveta:
   reenviar o MESMO lote não pode mandar ao fio a compra que já entrou.

A Omie continua sendo o `MockOmieClient`; o que se mede aqui é o comportamento
do ADL, nunca o contrato do fornecedor (S-1 segue não-verificada e é a fixture
da BACK 07.1 que responde por ela).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import (
    ReconciliationOmiePosting,
    UserRole,
    UserScope,
)
from app.integrations.omie.mock_client import MockOmieClient
from app.integrations.omie.schemas import IncluirLancCCRequest, IncluirLancCCResponse
from tests.integration.test_omie_posting_endpoint import (
    POSTING_URL,
    Scenario,
    _body,
    _login,
    _seed_client,
    _seed_entry,
    _seed_session,
    _seed_user,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

CATEGORIAS_URL = "/api/v1/omie/categorias"


# ----------------------------------------------------------------------
# Fixtures locais
# ----------------------------------------------------------------------
#
# Os SEEDS vêm de `test_omie_posting_endpoint` (funções puras, sem estado
# compartilhado); as FIXTURES são declaradas aqui de novo em vez de importadas
# porque importar o nome de uma fixture e usá-lo como parâmetro dispara F811 no
# ruff — e um `noqa` por assinatura esconderia um sombreamento de verdade no
# dia em que ele aparecer.


@pytest.fixture
async def cenario(db_session: AsyncSession) -> Scenario:
    """Admin + cliente + sessão de CARTÃO, o mínimo para lançar."""
    admin = await _seed_user(db_session, email=f"qa-lanc-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db_session, creator=admin, name=f"QA {uuid4().hex[:6]}")
    sess = await _seed_session(db_session, client=client, creator=admin)
    return Scenario(admin, client, sess)


@pytest.fixture
def lancamento_ligado(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Liga o kill-switch. O default é `False` de propósito (ADR-027-BE)."""
    monkeypatch.setenv("OMIE_POSTING_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _posting_rows(db: AsyncSession, file_entry_id: object) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(ReconciliationOmiePosting)
            .where(ReconciliationOmiePosting.file_entry_id == file_entry_id)
        )
    ) or 0


# ----------------------------------------------------------------------
# 1. Sem autenticação
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("lancamento_ligado")
class TestUnauthenticated:
    """Rota que ESCREVE no ERP do cliente não pode responder a anônimo."""

    async def test_posting_without_a_session_cookie_is_401(
        self, client_with_db: AsyncClient, cenario: Scenario, db_session: AsyncSession
    ) -> None:
        entry = await _seed_entry(db_session, sess=cenario.session)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=cenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )

        assert resp.status_code == 401, resp.text
        # E, o que mais importa: nada foi sequer registrado como intenção.
        assert await _posting_rows(db_session, entry.id) == 0

    async def test_categorias_without_a_session_cookie_is_401(
        self, client_with_db: AsyncClient, cenario: Scenario
    ) -> None:
        resp = await client_with_db.get(
            CATEGORIAS_URL, params={"session_id": str(cenario.session.id)}
        )

        assert resp.status_code == 401, resp.text


# ----------------------------------------------------------------------
# 2. Cross-tenant de usuário DE CLIENTE
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("lancamento_ligado")
class TestCrossTenantClientUser:
    """O caminho de vazamento da Sprint 5, aplicado à rota que escreve."""

    async def test_client_operator_of_another_tenant_gets_404_and_posts_nothing(
        self, client_with_db: AsyncClient, cenario: Scenario, db_session: AsyncSession
    ) -> None:
        """404 (não 403) e, acima de tudo, ZERO lançamento no tenant alheio."""
        outro_admin = await _seed_user(
            db_session, email=f"adm-outro-{uuid4().hex[:8]}@hologram.com.br"
        )
        outro_client = await _seed_client(
            db_session, creator=outro_admin, name=f"Outro {uuid4().hex[:6]}"
        )
        intruso = await _seed_user(
            db_session,
            email=f"op-outro-{uuid4().hex[:8]}@cli.com.br",
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=outro_client.id,
        )
        entry = await _seed_entry(db_session, sess=cenario.session)
        await _login(client_with_db, intruso.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=cenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )

        assert resp.status_code == 404, resp.text
        # A negação não pode vazar o alvo (CLAUDE.md §3.15).
        assert cenario.client.name not in resp.text
        assert await _posting_rows(db_session, entry.id) == 0

    async def test_client_manager_of_another_tenant_cannot_read_categorias(
        self, client_with_db: AsyncClient, cenario: Scenario, db_session: AsyncSession
    ) -> None:
        outro_admin = await _seed_user(
            db_session, email=f"adm-cat-{uuid4().hex[:8]}@hologram.com.br"
        )
        outro_client = await _seed_client(
            db_session, creator=outro_admin, name=f"OutroCat {uuid4().hex[:6]}"
        )
        intruso = await _seed_user(
            db_session,
            email=f"mgr-cat-{uuid4().hex[:8]}@cli.com.br",
            role=UserRole.CLIENT_MANAGER,
            scope=UserScope.CLIENT,
            client_id=outro_client.id,
        )
        await _login(client_with_db, intruso.email)

        resp = await client_with_db.get(
            CATEGORIAS_URL, params={"session_id": str(cenario.session.id)}
        )

        assert resp.status_code == 404, resp.text


# ----------------------------------------------------------------------
# 3 e 4. Zero duplicado — medido NO FIO, não no banco
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("lancamento_ligado")
class TestExactlyOnePostReachesOmie:
    """O guardrail da sprint é o número de POSTs, não o número de linhas."""

    async def test_resending_sends_exactly_one_post_to_omie(
        self,
        client_with_db: AsyncClient,
        cenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        entry = await _seed_entry(db_session, sess=cenario.session)
        sent: list[str] = []

        async def counting_post(
            self: MockOmieClient, request: IncluirLancCCRequest
        ) -> IncluirLancCCResponse:
            sent.append(request.c_cod_int_lanc or "")
            return IncluirLancCCResponse.model_validate(
                {"nCodLanc": 970_000_001, "cCodIntLanc": request.c_cod_int_lanc}
            )

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", counting_post)
        await _login(client_with_db, cenario.admin.email)
        url = POSTING_URL.format(session_id=cenario.session.id)
        body = _body((entry.id, "2.01.03"))

        first = await client_with_db.post(url, json=body)
        second = await client_with_db.post(url, json=body)
        third = await client_with_db.post(url, json=body)

        assert first.json()["data"]["lancadas"] == 1
        assert second.json()["data"]["lancadas"] == 0
        assert third.json()["data"]["lancadas"] == 0
        assert len(sent) == 1, (
            f"{len(sent)} POSTs chegaram ao Omie para a MESMA linha — "
            "cada um a mais é dinheiro duplicado na contabilidade do cliente."
        )
        assert await _posting_rows(db_session, entry.id) == 1

    async def test_relaunching_a_batch_only_posts_the_line_that_is_still_pending(
        self,
        client_with_db: AsyncClient,
        cenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reexecução de lote parcial: só a linha que faltava vai ao fio.

        É o fluxo real do resumo da gaveta ("Tentar novamente"): o operador
        reenvia o MESMO lote, e as compras que já entraram não podem virar um
        segundo movimento no ERP.

        ⚠️ Concorrência de verdade (duas requisições simultâneas) **não** é
        testável aqui: a fixture `client_with_db` compartilha UMA `AsyncSession`
        entre as requisições, e um `asyncio.gather` sobre ela quebra a conexão
        antes de exercitar o servidor. A garantia contra o duplo-clique
        simultâneo é o `ON CONFLICT DO NOTHING` sobre
        `uq_recon_omie_postings_file_entry`, provado no banco por
        `test_omie_postings.py::TestDatabaseEnforcesUniqueness`.
        """
        ja_lancada = await _seed_entry(db_session, sess=cenario.session, day=15)
        pendente = await _seed_entry(db_session, sess=cenario.session, day=16)
        sent: list[str] = []
        contador = iter(range(970_000_010, 970_000_020))

        async def counting_post(
            self: MockOmieClient, request: IncluirLancCCRequest
        ) -> IncluirLancCCResponse:
            sent.append(request.c_cod_int_lanc or "")
            return IncluirLancCCResponse.model_validate(
                {"nCodLanc": next(contador), "cCodIntLanc": request.c_cod_int_lanc}
            )

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", counting_post)
        await _login(client_with_db, cenario.admin.email)
        url = POSTING_URL.format(session_id=cenario.session.id)

        # 1º lote: só a primeira compra.
        await client_with_db.post(url, json=_body((ja_lancada.id, "2.01.03")))
        assert len(sent) == 1

        # 2º lote: as DUAS. Só a segunda pode chegar ao Omie.
        resp = await client_with_db.post(
            url, json=_body((ja_lancada.id, "2.01.03"), (pendente.id, "2.01.03"))
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["lancadas"] == 1
        assert data["bloqueadas"] == 1
        bloqueada = next(ln for ln in data["lines"] if ln["file_entry_id"] == str(ja_lancada.id))
        assert bloqueada["reason"] == "ja_lancada"
        assert len(sent) == 2, (
            f"{len(sent)} POSTs no total — a compra já lançada foi reenviada ao Omie."
        )

    async def test_two_identical_purchases_still_send_two_posts(
        self,
        client_with_db: AsyncClient,
        cenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """O caso oposto — e o mais fácil de quebrar sem ninguém notar.

        Duas compras REAIS idênticas (mesma data, mesmo valor, mesma descrição)
        têm de virar DOIS lançamentos, com chaves distintas. Uma chave derivada
        do conteúdo colapsaria as duas: dinheiro FALTANDO, que o critério de
        rollback da sprint (só vigia duplicado) não pegaria.
        """
        um = await _seed_entry(db_session, sess=cenario.session, amount="-12.00", day=15)
        outro = await _seed_entry(db_session, sess=cenario.session, amount="-12.00", day=15)
        sent: list[str] = []
        contador = iter(range(980_000_001, 980_000_010))

        async def counting_post(
            self: MockOmieClient, request: IncluirLancCCRequest
        ) -> IncluirLancCCResponse:
            sent.append(request.c_cod_int_lanc or "")
            return IncluirLancCCResponse.model_validate(
                {"nCodLanc": next(contador), "cCodIntLanc": request.c_cod_int_lanc}
            )

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", counting_post)
        await _login(client_with_db, cenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=cenario.session.id),
            json=_body((um.id, "2.01.03"), (outro.id, "2.01.03")),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["lancadas"] == 2
        assert len(sent) == 2, "duas compras idênticas viraram um lançamento só"
        assert len(set(sent)) == 2, f"as duas linhas usaram a MESMA cCodIntLanc: {sent}"
