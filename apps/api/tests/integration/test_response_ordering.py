"""Prova end-to-end a ordem commit-antes-da-resposta em todo write da API
(86e3fxqqa).

`get_db_session` (`app/db/session.py`) faz `commit()` no pós-`yield` de uma
FastAPI dependency, e o FastAPI instalado só fecha esse `AsyncExitStack`
DEPOIS de a resposta já ter sido despachada (ver o docstring de
`app/core/response_ordering.py` para a análise do fonte). Sem a
`CommitBeforeResponseMiddleware`, o cliente recebia sucesso antes do dado
estar durável no banco — em até 14 de 15 criações, segundo a sonda da
validação humana da Sprint 16 (CLAUDE.md v1.57).

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
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from fastapi import APIRouter, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import DbSessionDep
from app.core.response_ordering import CommitBeforeResponseMiddleware
from app.db.models import Organization
from app.db.session import close_db, init_db

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
    probe_app.add_middleware(CommitBeforeResponseMiddleware)
    router = APIRouter()

    @router.post("/organizations")
    async def create_organization(name: str, db: DbSessionDep) -> dict[str, str]:
        org = Organization(name=name, active=True)
        db.add(org)
        await db.flush()
        return {"id": str(org.id)}

    probe_app.include_router(router)
    return probe_app


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
        while not server.started:
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


class TestLeituraImediataAposCriar:
    """Critério de aceite: 100/100 leituras enxergam o dado recém-criado."""

    async def test_conexao_nova_sempre_ve_o_dado_recem_criado(
        self, _real_db_session_global: None, db_engine: AsyncEngine
    ) -> None:
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


class TestFalhaNoCommitViraErro:
    """Critério de aceite: falha no commit() nunca vira sucesso, e nada é
    gravado.
    """

    async def test_commit_falhando_devolve_erro_e_nao_grava_nada(
        self,
        _real_db_session_global: None,
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
