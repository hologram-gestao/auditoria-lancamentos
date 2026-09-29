"""Unit test isolado (sem DB) para `CommitBeforeResponseMiddleware` (86e3fxqqa).

Prova o mecanismo em si, na camada mais barata: o middleware só encaminha
mensagens ASGI ao `send` real DEPOIS que o app envolvido (dependências com
`yield` incluídas) tiver terminado por completo — nunca antes, nem em caso
de exceção. A prova end-to-end contra Postgres real (leitura imediata por
conexão nova, falha de commit vira erro) está em
`tests/integration/test_response_ordering.py`.
"""

from __future__ import annotations

from typing import Any

from app.core.response_ordering import CommitBeforeResponseMiddleware


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.disconnect"}


async def test_so_encaminha_ao_send_real_depois_do_app_terminar() -> None:
    """Simula o commit() do pós-yield rodando DEPOIS do app já ter enviado a
    resposta pro `send` interno — e prova que o `send` REAL só vê algo
    depois que essa etapa (representada por `ordem.append(...)` no fim do
    app) já aconteceu.
    """
    ordem: list[str] = []

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})
        # Representa o `request_stack.__aexit__()` do FastAPI, onde o
        # `commit()` do pós-yield de `get_db_session` roda de verdade.
        ordem.append("exit_stack_fechou_commit_incluso")

    middleware = CommitBeforeResponseMiddleware(inner_app)

    async def real_send(message: dict[str, Any]) -> None:
        ordem.append(f"send_real:{message['type']}")

    await middleware({"type": "http"}, _noop_receive, real_send)

    assert ordem == [
        "exit_stack_fechou_commit_incluso",
        "send_real:http.response.start",
        "send_real:http.response.body",
    ]


async def test_excecao_no_app_nunca_encaminha_nada_ao_send_real() -> None:
    """Se o `commit()` falhar (exceção durante o fechamento do exit stack, já
    depois do app ter "enviado" a resposta de sucesso pro `send` interno), o
    `send` real nunca recebe nada — é isso que impede o cliente de ver um
    2xx que na verdade não foi persistido.
    """

    async def inner_app_falha(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})
        raise RuntimeError("commit simulado falhando")

    middleware = CommitBeforeResponseMiddleware(inner_app_falha)
    enviados: list[dict[str, Any]] = []

    async def real_send(message: dict[str, Any]) -> None:
        enviados.append(message)

    try:
        await middleware({"type": "http"}, _noop_receive, real_send)
    except RuntimeError as exc:
        assert str(exc) == "commit simulado falhando"
    else:
        raise AssertionError("esperava RuntimeError propagando do app")

    assert enviados == [], "nada deveria ter chegado ao send real"


async def test_websocket_e_outros_scopes_passam_direto_sem_buffer() -> None:
    """WebSocket não é bufferizado — quebraria tempo real. Guard defensivo:
    esta API não expõe WebSocket hoje, mas o middleware não deve assumir
    isso silenciosamente.
    """
    chamadas: list[tuple[str, ...]] = []

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        chamadas.append((scope["type"],))
        await send({"type": "probe"})

    middleware = CommitBeforeResponseMiddleware(inner_app)

    enviados: list[dict[str, Any]] = []

    async def real_send(message: dict[str, Any]) -> None:
        enviados.append(message)

    await middleware({"type": "websocket"}, _noop_receive, real_send)

    assert chamadas == [("websocket",)]
    # Passou direto: o `real_send` recebeu a mensagem IMEDIATAMENTE, sem
    # passar pelo buffer (não há como observar isso via timing num teste
    # determinístico, então a prova é indireta: chegou, e só há uma
    # chamada a `inner_app`, sem nenhuma camada de buffer no meio).
    assert enviados == [{"type": "probe"}]
