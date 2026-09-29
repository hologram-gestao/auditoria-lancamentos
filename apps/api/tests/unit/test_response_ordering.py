"""Unit test isolado (sem DB) para `CommitBeforeResponseMiddleware` (86e3fxqqa).

Prova o mecanismo na camada mais barata: o commit acontece no
`http.response.start`, ANTES de a mensagem ser encaminhada ao `send` real, e a
mensagem seguinte não repete o commit. A prova end-to-end contra Postgres
(leitura imediata por conexão nova, falha de commit vira erro, resposta que NÃO
espera a BackgroundTask) está em `tests/integration/test_response_ordering.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.response_ordering import CommitBeforeResponseMiddleware


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.disconnect"}


class _FakeSession:
    """Session mínima: registra os commits numa lista de ordem compartilhada."""

    def __init__(self, ordem: list[str]) -> None:
        self._ordem = ordem
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1
        self._ordem.append("commit")


class _FailingSession(_FakeSession):
    async def commit(self) -> None:
        self.commits += 1
        raise RuntimeError("commit simulado falhando")


def _scope(session: object | None) -> dict[str, Any]:
    """Scope HTTP com a session publicada como `get_db_session` publica."""
    state: dict[str, Any] = {}
    if session is not None:
        state["db_session"] = session
    return {"type": "http", "state": state}


async def test_comita_antes_de_encaminhar_o_response_start() -> None:
    ordem: list[str] = []
    session = _FakeSession(ordem)

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})
        # Representa a BackgroundTask: roda DEPOIS dos sends, dentro do app.
        ordem.append("background")

    async def real_send(message: dict[str, Any]) -> None:
        ordem.append(f"send:{message['type']}")

    await CommitBeforeResponseMiddleware(inner_app)(_scope(session), _noop_receive, real_send)

    assert ordem == [
        "commit",
        "send:http.response.start",
        "send:http.response.body",
        "background",
    ]
    assert session.commits == 1


async def test_a_resposta_nao_espera_o_que_vier_depois_dos_sends() -> None:
    """O commit entra ENTRE a resposta pronta e o 1º byte — e nada mais.

    É o que separa esta versão da primeira tentativa (bufferizar tudo até o app
    terminar), que segurava a resposta até a BackgroundTask acabar.
    """
    ordem: list[str] = []
    session = _FakeSession(ordem)

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        ordem.append("background_lenta")

    async def real_send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            ordem.append("cliente_recebeu")

    await CommitBeforeResponseMiddleware(inner_app)(_scope(session), _noop_receive, real_send)

    assert ordem.index("cliente_recebeu") < ordem.index("background_lenta")


async def test_rota_sem_session_no_state_nao_comita_nada() -> None:
    enviados: list[dict[str, Any]] = []

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def real_send(message: dict[str, Any]) -> None:
        enviados.append(message)

    await CommitBeforeResponseMiddleware(inner_app)(_scope(None), _noop_receive, real_send)

    assert [m["type"] for m in enviados] == ["http.response.start"]


async def test_commit_que_falha_nao_encaminha_nenhum_byte() -> None:
    """A exceção sobe ANTES do `send` real: o 2xx montado nunca chega ao cliente."""
    ordem: list[str] = []
    session = _FailingSession(ordem)
    enviados: list[dict[str, Any]] = []

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def real_send(message: dict[str, Any]) -> None:
        enviados.append(message)

    with pytest.raises(RuntimeError, match="commit simulado falhando"):
        await CommitBeforeResponseMiddleware(inner_app)(_scope(session), _noop_receive, real_send)

    assert enviados == [], "nada pode ter chegado ao send real"


async def test_uma_tentativa_de_commit_por_request() -> None:
    """Resposta de erro despachada depois de um commit falho não tenta de novo."""
    ordem: list[str] = []
    session = _FailingSession(ordem)

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        try:
            await send({"type": "http.response.start", "status": 201, "headers": []})
        except RuntimeError:
            # O handler global montando a resposta de erro no mesmo request.
            await send({"type": "http.response.start", "status": 500, "headers": []})
            await send({"type": "http.response.body", "body": b"erro", "more_body": False})

    enviados: list[dict[str, Any]] = []

    async def real_send(message: dict[str, Any]) -> None:
        enviados.append(message)

    await CommitBeforeResponseMiddleware(inner_app)(_scope(session), _noop_receive, real_send)

    assert session.commits == 1
    assert [m.get("status") for m in enviados if m["type"] == "http.response.start"] == [500]


async def test_websocket_e_outros_scopes_passam_direto() -> None:
    chamadas: list[str] = []

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        chamadas.append(scope["type"])
        await send({"type": "probe"})

    enviados: list[dict[str, Any]] = []

    async def real_send(message: dict[str, Any]) -> None:
        enviados.append(message)

    await CommitBeforeResponseMiddleware(inner_app)({"type": "websocket"}, _noop_receive, real_send)

    assert chamadas == ["websocket"]
    assert enviados == [{"type": "probe"}]
