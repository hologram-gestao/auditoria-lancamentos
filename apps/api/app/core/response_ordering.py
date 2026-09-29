"""Middleware ASGI que garante commit do banco ANTES da resposta HTTP sair.

Contexto (task 86e3fxqqa): `get_db_session` (`app/db/session.py`) faz
`commit()` no código que vem DEPOIS do `yield` de uma dependency do FastAPI
— e o FastAPI instalado (0.136.1, confirmado no fonte de
`fastapi/routing.py::request_response`) só fecha o `AsyncExitStack` dessas
dependencies DEPOIS de `await response(scope, receive, send)` já ter
despachado a resposta. Ou seja: por design do framework, esse `commit()`
SEMPRE roda depois dos bytes da resposta já terem saído — não é um bug
pontual de uma rota, é o comportamento de toda dependency com `yield`.

Duas alternativas foram descartadas, e o motivo de cada uma importa:

    - `BaseHTTPMiddleware` (como `CorrelationIdMiddleware`/`SecurityHeaders
      Middleware` já usam em `app/main.py`): seu `call_next()` retorna assim
      que o PRIMEIRO chunk (`http.response.start`) chega, via um
      `anyio.create_memory_object_stream()` SEM buffer — o commit ainda
      roda CONCORRENTE ao envio real do corpo, só com uma folga menor.
      Não elimina a corrida, só a estreita (confirmado lendo
      `starlette/middleware/base.py`: `call_next` só espera a PRIMEIRA
      mensagem do stream, o resto — inclusive o fechamento do exit stack —
      continua rodando numa task paralela).
    - Trocar `route_class` por uma `APIRoute` customizada (o jeito
      "oficial" de rodar código entre o handler e o envio): exigiria passar
      `route_class=` em CADA `APIRouter()` dos módulos (`routes.py`, um por
      domínio) — `APIRouter.include_router()` sempre recria a rota com
      `route_class_override=type(route)`, ignorando o `route_class` do
      router de DESTINO (confirmado em `fastapi/routing.py::include_router`,
      linha que monta `route_class_override=type(route)` a partir do objeto
      route já existente). Setar `app.router.route_class` não se propaga
      para dentro dos `include_router()` dos módulos — seria preciso tocar
      cada `routes.py`, o que a task pede pra evitar ("sem espalhar commit
      por rota").

A solução: um middleware ASGI PURO (não `BaseHTTPMiddleware`), registrado
como o mais INTERNO da pilha (mais perto do roteamento — ver
`main.py::create_app`). Um middleware puro não introduz uma task
concorrente: `await self.app(scope, receive, buffering_send)` só retorna
quando TUDO abaixo dele — dependências, exit stack, commit incluso — já
terminou, porque é a MESMA coroutine, sequencial, sem `anyio.TaskGroup`.
Por isso basta reter as mensagens ASGI da resposta (`http.response.start`/
`body`, e qualquer outra) num buffer em memória em vez de repassá-las ao
`send` real, e só despachá-las de fato depois que a chamada retornar.

Efeito colateral aceito: a resposta deixa de ser enviada em streaming
incremental (o corpo inteiro fica retido até o fim do request). Para esta
API isso não muda nada na prática — os `StreamingResponse` existentes
(export Excel, CSV contábil) já constroem o conteúdo inteiro em memória
ANTES de envelopar na resposta (CLAUDE.md §3.10: nunca grava em disco, nunca
processa fora da memória), então não havia entrega incremental real para
perder.

Se o `commit()` falhar, a exceção nunca chega a ser despachada — nada foi
enviado ainda, porque ainda está só no buffer — e sobe até o
`ServerErrorMiddleware` do Starlette, que devolve o 500 padrão do
`@app.exception_handler(Exception)` já registrado em `main.py`. O cliente
nunca vê o 2xx que seria enviado se o commit tivesse dado certo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CommitBeforeResponseMiddleware:
    """Atrasa o envio real da resposta HTTP até a dependency chain terminar.

    Ver o docstring do módulo para o porquê. Só atua em requests HTTP —
    WebSocket passa direto (bufferizar um WebSocket quebraria tempo real;
    esta API não expõe nenhum, mas o guard existe por segurança) e o
    `lifespan` nem chega a passar pela pilha de middlewares HTTP.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        buffered_messages: list[Message] = []

        async def buffering_send(message: Message) -> None:
            buffered_messages.append(message)

        # Não retorna até o app INTEIRO terminar — dependências com `yield`
        # incluídas. Se `commit()` falhar aqui dentro, a exceção propaga sem
        # que nada do buffer tenha sido encaminhado ao `send` real.
        await self.app(scope, receive, buffering_send)

        for message in buffered_messages:
            await send(message)
