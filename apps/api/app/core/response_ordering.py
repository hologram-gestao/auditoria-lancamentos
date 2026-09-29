"""Middleware ASGI que comita o banco ANTES de a resposta HTTP sair (86e3fxqqa).

Contexto: `get_db_session` (`app/db/session.py`) faz `commit()` no código que vem
DEPOIS do `yield` de uma dependency do FastAPI — e o FastAPI instalado (0.136.1,
confirmado no fonte de `fastapi/routing.py::request_response`) só fecha o
`AsyncExitStack` dessas dependencies DEPOIS de `await response(scope, receive,
send)` já ter despachado a resposta. Ou seja: por design do framework, esse
`commit()` SEMPRE roda depois dos bytes da resposta já terem saído — não é um bug
pontual de uma rota, é o comportamento de toda dependency com `yield`. Medido em
servidor real: 45 de 100 criações tinham a resposta no cliente ANTES da linha
existir no banco.

**Onde este middleware age.** O `send` que ele entrega ao app é o MESMO que
`Response.__call__` usa lá embaixo. Comitar dentro dele, ao ver o
`http.response.start`, põe o `commit()` exatamente entre "a resposta está pronta"
e "o primeiro byte vai para o socket" — sem buffer, sem task nova, sem tocar em
nenhuma rota. A session vem de `request.state.db_session`, publicada pelo
`get_db_session`; rota que não fala com o banco não tem nada lá e o middleware
não faz nada.

⚠️ **Não bufferize a resposta inteira para conseguir o mesmo efeito.** Foi a
primeira tentativa desta task e ela SEGURA a resposta até a BackgroundTask
terminar: o Starlette roda `await self.background()` DENTRO de
`Response.__call__`, depois dos `send` e antes de a coroutine do app retornar.
Com buffer, o cliente de `POST /reconciliations` esperaria o processamento
inteiro (teto de `RECONCILIATION_TIMEOUT_SECONDS`, 900 s) em vez dos milissegundos
do 201. Medido: 3,00 s de espera com buffer contra 0,00 s aqui, numa task que
dorme 3 s. Nenhum teste pega isso sozinho — todos os testes de conciliação
substituem `_schedule_reconciliation_processing` por um stub.

**Duas outras alternativas, e por que não servem:**

    - `BaseHTTPMiddleware` (como `CorrelationIdMiddleware`/`SecurityHeaders
      Middleware` usam em `app/main.py`): o `call_next()` dele retorna assim que o
      PRIMEIRO chunk chega, via um `anyio.create_memory_object_stream()` SEM
      buffer — o commit continua correndo concorrente ao envio do corpo. Estreita
      a corrida, não a elimina.
    - Trocar `route_class` por uma `APIRoute` customizada (o jeito "oficial" de
      rodar código entre o handler e o envio): exigiria passar `route_class=` em
      CADA `APIRouter()` dos módulos — `APIRouter.include_router()` sempre recria
      a rota com `route_class_override=type(route)`, ignorando o `route_class` do
      router de DESTINO.

**Falha no commit vira erro, nunca 2xx.** A exceção sobe de dentro do `send`
ANTES de qualquer byte ser encaminhado, o `get_db_session` faz `rollback()` no
teardown e limpa `request.state.db_session`, e o `ServerErrorMiddleware` do
Starlette — que nunca viu um `http.response.start` — devolve o 500 do
`@app.exception_handler(Exception)`. A resposta de sucesso que estava montada
nunca chega ao cliente.

**Caminho de erro do handler não comita.** Quando o endpoint levanta, o teardown
do `get_db_session` roda (rollback) ANTES de o handler global montar a resposta
de erro — e ele zera `request.state.db_session`, então o `send` da resposta de
erro não encontra session nenhuma para comitar. Escrita parcial de request que
falhou nunca é persistida por aqui.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CommitBeforeResponseMiddleware:
    """Comita a session do request no `http.response.start`, antes do 1º byte.

    Ver o docstring do módulo para o porquê de cada decisão. Só atua em requests
    HTTP — WebSocket passa direto (esta API não expõe nenhum, mas o middleware não
    deve assumir isso em silêncio) e o `lifespan` nem chega à pilha HTTP.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        attempted = False

        async def committing_send(message: Message) -> None:
            nonlocal attempted
            if message["type"] == "http.response.start" and not attempted:
                # Marcado ANTES de tentar: se o commit falhar, a resposta de erro
                # que vier depois não tenta comitar de novo.
                attempted = True
                session = getattr(request.state, "db_session", None)
                if session is not None:
                    await session.commit()
            await send(message)

        await self.app(scope, receive, committing_send)
