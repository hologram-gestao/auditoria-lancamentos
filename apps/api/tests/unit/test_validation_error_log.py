"""O log de erro de validação não pode carregar o valor rejeitado (86e2rtxcm).

Critério de aceite da task, medido de ponta a ponta: um body inválido com um
valor reconhecível atravessa o handler global de `RequestValidationError` e o
valor NÃO aparece no log capturado — enquanto `loc` e `msg` continuam dizendo
qual campo falhou e por quê (sem cegar o suporte), e a resposta HTTP segue o
envelope 400 de sempre.

`capture_logs` troca a cadeia de processors do structlog, então o que se mede
aqui é a frente 1 (sanitização ANTES do log, no handler) — a frente 2 (redactor
recursivo) tem testes próprios em `test_logging.py`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db_session
from app.main import app as fastapi_app

SECRET = "SENHA-QUE-NAO-PODE-VAZAR-8f3k2"


@pytest.fixture
async def client_sem_db() -> AsyncGenerator[AsyncClient, None]:
    """Client com `get_db_session` neutralizado.

    A dependência de DB do login resolve JUNTO com a validação do body; sem
    override ela levanta "Session factory não inicializada" antes de o handler
    de validação rodar. O dummy nunca é usado de verdade: o body inválido
    derruba o request na validação, e o corpo da rota jamais executa.
    """

    async def _override() -> AsyncGenerator[Any, None]:
        yield None

    fastapi_app.dependency_overrides[get_db_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_rejected_value_never_reaches_the_log(client_sem_db: AsyncClient) -> None:
    # `password` como lista falha `string_type` e o Pydantic põe o VALOR
    # rejeitado em `errors[0]["input"]` — o vetor exato do vazamento original.
    with structlog.testing.capture_logs() as logs:
        response = await client_sem_db.post(
            "/api/v1/auth/login",
            json={"email": "analista@hologram.com.br", "password": [SECRET]},
        )

    # 1) O segredo não aparece em NENHUM evento logado.
    assert SECRET not in str(logs), "valor rejeitado vazou para o log"

    # 2) O evento de validação existe e continua útil para o suporte.
    validation = [e for e in logs if e.get("event") == "validation_error"]
    assert len(validation) == 1
    (event,) = validation
    flat = str(event["errors"])
    assert "password" in flat, "o log perdeu QUAL campo falhou (loc)"
    assert "input" not in {k for err in event["errors"] for k in err}, (
        "a chave `input` (valor rejeitado) voltou ao log"
    )

    # 3) A resposta HTTP não muda: 400 com o envelope genérico.
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert SECRET not in response.text, "o valor rejeitado não pode ecoar na resposta"
