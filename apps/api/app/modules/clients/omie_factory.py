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

from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    field_locator,
)
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.crypto import ClientCipher
    from app.db.models import Client


def build_omie_client(
    client: Client,
    settings: Settings,
    cipher: ClientCipher,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """Descriptografa as credenciais do `client` e retorna um `OmieClient` pronto.

    Args:
        client: ORM `Client` carregado (precisa dos 4 campos `*_encrypted`/`*_iv`).
        settings: `Settings` da aplicação.
        cipher: `ClientCipher` do cliente (DEK já desembrulhada) — construído no
            caller com `crypto_service.load_client_cipher`. Decifra tanto o novo
            envelope `v<n>:` (DEK + AAD) quanto linhas bare legadas (chave global).
        http_client: `AsyncClient` opcional. Use em testes pra que o `respx` capte
            as chamadas; em produção, omitir → o `OmieClient` cria o seu próprio
            (com fechamento automático em `aclose()`).

    Returns:
        `OmieClient` pronto pra chamar `listar_contas_correntes()`, etc.

    Raises:
        CryptoError: DEK ausente, chave inválida ou ciphertext adulterado/fora de
            contexto (exception handler global converte em 500 INTERNAL_ERROR).
    """
    app_key = cipher.decrypt(
        client.omie_app_key_encrypted,
        client.omie_app_key_iv,
        field_locator(AAD_CLIENT_APP_KEY, client.id),
    )
    app_secret = cipher.decrypt(
        client.omie_app_secret_encrypted,
        client.omie_app_secret_iv,
        field_locator(AAD_CLIENT_APP_SECRET, client.id),
    )
    creds = OmieCredentials(
        app_key=SecretStr(app_key),
        app_secret=SecretStr(app_secret),
    )

    # Heurística de cliente-demo: credenciais geradas por `seed_demo_client.py`
    # começam com `FAKE_DEMO_OMIE_`. Esse prefixo é improvável em key real e o
    # Omie de produção nunca o aceitaria — usá-lo como flag implícita evita
    # adicionar coluna/migration no DB e mantém o seed como a única fonte de
    # ativação. NUNCA usar prefixo similar em credencial real.
    if app_key.startswith(FAKE_DEMO_KEY_PREFIX):
        return MockOmieClient(creds, settings)

    return OmieClient(creds, settings, http_client=http_client)
