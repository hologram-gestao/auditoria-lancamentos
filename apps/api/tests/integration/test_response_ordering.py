"""Prova end-to-end a ordem commit-antes-da-resposta em todo write da API
(86e3fxqqa).

`get_db_session` (`app/db/session.py`) faz `commit()` no pós-`yield` de uma
FastAPI dependency, e o FastAPI instalado só fecha esse `AsyncExitStack`
DEPOIS de a resposta já ter sido despachada (ver o docstring de
`app/core/response_ordering.py` para a análise do fonte). Sem a
`CommitBeforeResponseMiddleware`, o cliente recebia sucesso antes do dado
estar durável no banco — 45 de 100 criações neste mesmo teste, e 14 de 15 na
sonda da validação humana da Sprint 16 (CLAUDE.md v1.57).

**O terceiro caso deste módulo é o que reprovou a primeira tentativa desta
task**: um middleware que bufferizava a resposta inteira também zerava as
leituras ausentes, mas segurava a resposta até a BackgroundTask terminar (o
Starlette roda `await self.background()` dentro de `Response.__call__`). Nos 4
endpoints de conciliação isso seria o cliente esperando o processamento inteiro
— teto de 900 s — em vez do 201. Nenhum outro teste da suíte pega isso: todos
substituem `_schedule_reconciliation_processing` por um stub.

⚠️ **Por que este teste sobe um servidor uvicorn REAL, em vez de usar
`httpx.ASGITransport`** (o padrão do resto da suíte, via `client`/
`client_with_db`): `ASGITransport` chama a app ASGI diretamente, na MESMA
coroutine do "cliente" — `await self.app(scope, receive, send)` só retorna
quando o app INTEIRO termina, commit incluso, com ou sem o middleware. A
corrida original só existe porque um servidor ASGI real (uvicorn) escreve
os bytes no socket TCP assim que `send()` é chamado, e o processo do
CLIENTE (do outro lado do socket) pode ler esses bytes e seguir em frente
ANTES do processo do servidor terminar de rodar o resto da coroutine
(commit incluso) — dois processos/tasks concorrentes de verdade, não uma
chamada síncrona. Confirmado empiricamente: rodar a versão sem o
middleware contra `ASGITransport` passa 100/100 mesmo COM o bug presente
(ver HANDOFF/relato da task) — só o servidor real expõe a corrida. A prova
do MECANISMO em si (que o `send` real só recebe algo depois do app
terminar) está no teste unitário, `tests/unit/test_response_ordering.py`.

`client_with_db` (o fixture usado pela maioria da suíte) também não serve
aqui por outro motivo: sua sessão de teste sobrescreve `get_db_session` com
uma generator que só faz `yield`, sem `commit()`/`rollback()` nenhum (ver
docstring de `client_with_db` em `tests/conftest.py`).
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from fastapi import APIRouter, BackgroundTasks, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import DbSessionDep
from app.core.response_ordering import CommitBeforeResponseMiddleware
from app.db.models import Organization
from app.db.session import close_db, init_db
from app.main import CorrelationIdMiddleware, SecurityHeadersMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


def _build_probe_app() -> FastAPI:
    """App FastAPI mínima, com o middleware e a dependency REAIS de produção.

    Usa `Organization` (id UUID + `name` + `active`, sem FK nenhuma) como
    "o recurso" — não precisa de autenticação nem de tenant para existir, o
    que mantém o teste focado só na ordem commit-antes-da-resposta.
    """
    probe_app = FastAPI()
    # A MESMA pilha de `main.py::create_app`, na mesma ordem: o commit é o mais
    # INTERNO e os dois `BaseHTTPMiddleware` ficam por fora. Sem eles o teste
    # provaria o middleware isolado, não como ele roda em produção — e é
    # justamente o `BaseHTTPMiddleware` (que reempacota o `send` num stream
    # anyio) o vizinho capaz de mudar a ordem de entrega dos bytes.
    probe_app.add_middleware(CommitBeforeResponseMiddleware)
    probe_app.add_middleware(CorrelationIdMiddleware)
    probe_app.add_middleware(SecurityHeadersMiddleware, is_production=False)
    router = APIRouter()

    @router.post("/organizations")
    async def create_organization(name: str, db: DbSessionDep) -> dict[str, str]:
        org = Organization(name=name, active=True)
        db.add(org)
        await db.flush()
        return {"id": str(org.id)}

    @router.post("/with-background", status_code=202)
    async def with_background(
        background_tasks: BackgroundTasks, db: DbSessionDep, name: str
    ) -> dict[str, str]:
        """O molde de `POST /reconciliations`: grava e agenda um trabalho longo.

        A BackgroundTask REAL roda aqui (nenhum stub) — é o que faltava na
        primeira tentativa desta task.
        """
        org = Organization(name=name, active=True)
        db.add(org)
        await db.flush()
        background_tasks.add_task(_slow_background_job)
        return {"id": str(org.id)}

    probe_app.include_router(router)
    return probe_app


#: Quanto a BackgroundTask "demora". Precisa ser MUITO maior que o tempo de um
#: request local para a medição distinguir as duas coisas sem flakiness.
BACKGROUND_SECONDS = 3.0


async def _slow_background_job() -> None:
    await asyncio.sleep(BACKGROUND_SECONDS)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _run_real_server(app: FastAPI) -> AsyncGenerator[str, None]:
    """Sobe um uvicorn de verdade (socket TCP real) num processo/task
    separada, só para este teste. Precisa ser um servidor real — ver o
    docstring do módulo.
    """
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        # `uvicorn.Server` expõe só o booleano `started`, sem `asyncio.Event`
        # para aguardar — por isso o polling (ASYNC110 não se aplica aqui).
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.005)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serve_task


@pytest.fixture
async def _real_db_session_global(db_url: str) -> AsyncGenerator[None, None]:
    """Aponta o engine GLOBAL de `app.db.session` pro Postgres efêmero de
    teste — o que o `lifespan` faz em produção, e que este servidor uvicorn
    de teste não dispara sozinho (a app de teste não usa o `lifespan` da
    app pública).
    """
    settings = get_settings().model_copy(update={"DATABASE_URL": db_url})
    init_db(settings)
    try:
        yield
    finally:
        await close_db()


@pytest.mark.usefixtures("_real_db_session_global")
class TestLeituraImediataAposCriar:
    """Critério de aceite: 100/100 leituras enxergam o dado recém-criado."""

    async def test_conexao_nova_sempre_ve_o_dado_recem_criado(self, db_engine: AsyncEngine) -> None:
        app = _build_probe_app()
        ausencias: list[str] = []

        async with (
            _run_real_server(app) as base_url,
            httpx.AsyncClient(base_url=base_url) as ac,
        ):
            for i in range(100):
                marker = f"probe-{uuid.uuid4().hex}"
                resp = await ac.post("/organizations", params={"name": marker})
                assert resp.status_code == 200, f"iteração {i}: {resp.status_code} {resp.text}"

                # Conexão NOVA — nunca a que a request usou — pra provar
                # visibilidade cross-connection (só commit() garante isso;
                # flush() sozinho não seria visto daqui).
                async with db_engine.connect() as conn:
                    row = await conn.execute(
                        select(Organization.id).where(Organization.name == marker)
                    )
                    if row.first() is None:
                        ausencias.append(marker)

        assert not ausencias, (
            f"{len(ausencias)}/100 leituras imediatas não viram o dado recém-criado "
            f"(a resposta chegou antes do commit): {ausencias[:5]}"
        )


@pytest.mark.usefixtures("_real_db_session_global")
class TestFalhaNoCommitViraErro:
    """Critério de aceite: falha no commit() nunca vira sucesso, e nada é
    gravado.
    """

    async def test_commit_falhando_devolve_erro_e_nao_grava_nada(
        self,
        db_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _raising_commit(self: AsyncSession) -> None:
            raise RuntimeError("commit simulado falhando (teste)")

        monkeypatch.setattr(AsyncSession, "commit", _raising_commit)

        app = _build_probe_app()
        marker = f"probe-fail-{uuid.uuid4().hex}"

        async with (
            _run_real_server(app) as base_url,
            httpx.AsyncClient(base_url=base_url) as ac,
        ):
            resp = await ac.post("/organizations", params={"name": marker})

        assert resp.status_code >= 500, (
            f"esperava erro (commit falhou), recebeu {resp.status_code}: {resp.text}"
        )

        monkeypatch.undo()  # AsyncSession.commit real de novo antes de ler
        async with db_engine.connect() as conn:
            row = await conn.execute(select(Organization.id).where(Organization.name == marker))
            assert row.first() is None, (
                "a linha foi gravada mesmo com commit() falhando — a resposta de erro "
                "não pode conviver com escrita persistida"
            )


@pytest.mark.usefixtures("_real_db_session_global")
class TestRespostaNaoEsperaBackgroundTask:
    """O que reprovou a 1ª tentativa desta task (buffer da resposta inteira).

    O Starlette roda `await self.background()` DENTRO de `Response.__call__`,
    depois dos `send` e antes de a coroutine do app retornar. Qualquer solução
    que segure os bytes até o app terminar segura a resposta até a
    BackgroundTask acabar — nos 4 endpoints de conciliação, até 900 s.
    """

    async def test_resposta_chega_antes_da_background_terminar(
        self, db_engine: AsyncEngine
    ) -> None:
        app = _build_probe_app()
        marker = f"probe-bg-{uuid.uuid4().hex}"

        async with (
            _run_real_server(app) as base_url,
            httpx.AsyncClient(base_url=base_url, timeout=BACKGROUND_SECONDS * 5) as ac,
        ):
            started = time.perf_counter()
            resp = await ac.post("/with-background", params={"name": marker})
            elapsed = time.perf_counter() - started

            assert resp.status_code == 202, resp.text
            assert elapsed < BACKGROUND_SECONDS / 2, (
                f"a resposta demorou {elapsed:.2f}s com uma BackgroundTask de "
                f"{BACKGROUND_SECONDS}s — ela está esperando a task terminar"
            )

            # E o commit continua garantido: o dado já está visível de outra conexão.
            async with db_engine.connect() as conn:
                row = await conn.execute(select(Organization.id).where(Organization.name == marker))
                assert row.first() is not None, (
                    "a resposta chegou antes do commit no caminho com BackgroundTask"
                )
