"""Factory para construir um `OmieClient` a partir de um `Client` persistido.

Este módulo é o **ÚNICO** lugar do projeto onde as credenciais Omie aparecem
em texto plano (CLAUDE.md §3). Encapsular a descriptografia aqui:
    - reduz blast radius (1 ponto pra auditar/rotacionar chaves);
    - garante que o plaintext nunca atravessa as camadas — o `OmieClient`
      recebe `OmieCredentials(SecretStr, SecretStr)`, que mascara `repr()`;
    - permite injeção de `httpx.AsyncClient` para testes (respx) sem precisar
      mockar a descriptografia.

Não loga nada. Não retorna o plaintext. Não persiste em parte alguma.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import SecretStr

from app.core.crypto import decrypt
from app.integrations.omie.client import OmieClient, OmieCredentials

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.db.models import Client


def build_omie_client(
    client: Client,
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """Descriptografa as credenciais do `client` e retorna um `OmieClient` pronto.

    Args:
        client: ORM `Client` carregado (precisa dos 4 campos `*_encrypted`/`*_iv`).
        settings: `Settings` com a `OMIE_ENCRYPTION_KEY`.
        http_client: `AsyncClient` opcional. Use em testes pra que o `respx` capte
            as chamadas; em produção, omitir → o `OmieClient` cria o seu próprio
            (com fechamento automático em `aclose()`).

    Returns:
        `OmieClient` pronto pra chamar `listar_contas_correntes()`, etc.

    Raises:
        CryptoError: chave inválida ou ciphertext adulterado (exception handler
            global converte em 500 INTERNAL_ERROR).
    """
    hex_key = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
    app_key = decrypt(client.omie_app_key_encrypted, client.omie_app_key_iv, hex_key)
    app_secret = decrypt(client.omie_app_secret_encrypted, client.omie_app_secret_iv, hex_key)
    creds = OmieCredentials(
        app_key=SecretStr(app_key),
        app_secret=SecretStr(app_secret),
    )
    return OmieClient(creds, settings, http_client=http_client)
