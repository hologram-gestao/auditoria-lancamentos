"""Adaptador ARQUIVO — o primeiro `OriginProvider` que não é um ERP (Sprint 14, BACK 14.1 — R1).

A Sprint 9 escreveu o contrato para que o segundo provedor CABESSE; este é ele. A
origem é a planilha (CSV/XLSX) que o cliente manda, lida pelo **mapeamento de
entrada** dele (`client_input_mappings`) — declarativo, sem IA, sem heurística.

**O que este adaptador declara, e o que NÃO declara.** Só `LISTAR_LANCAMENTOS`:

    - `VERIFICAR_CREDENCIAL` — não há credencial para verificar. A conexão do tipo
      `arquivo` nasce sem segredo e `POST …/connections/{id}/test` responde 409
      `CAPACIDADE_AUSENTE` (taxonomia da S9); o front esconde "Testar conexão"
      pela capacidade, como já faz com o resto.
    - `LISTAR_CONTAS` — o arquivo pode trazer uma coluna de conta, mas não existe
      "listar as contas do provedor" antes de ler um arquivo. `list_accounts()`
      devolve `[]` e a capacidade fica de fora, para o predicado da S9
      (`select_capable_connection`) recusar a sincronização de contas com o 409
      certo em vez de gravar um cache vazio como se fosse resposta.
    - `LISTAR_TITULOS_EM_ABERTO` — o arquivo é o realizado, não o compromisso.
    - `ESCREVER` — fora de escopo global da sprint ("escrever de volta em qualquer
      sistema a partir da origem arquivo").

**Como ele é "mais um adaptador" e não um caminho paralelo.** O adaptador não abre
arquivo nenhum: ele recebe, EM MEMÓRIA, as linhas que o leitor da BACK 14.3 já
converteu em `ProviderEntry` (data pelo formato declarado, valor pelo separador
declarado, sinal pela convenção declarada) e as devolve em `list_entries`. É assim
que a ingestão alimenta o MESMO ciclo `_persist` da base de movimentos (S12 R0)
que o Omie alimenta — a base não sabe de onde veio a linha, só o `source_type`.

**No registry ele entra pelas duas portas** (`_PROVIDERS` e
`_CAPABILITIES_BY_TYPE`): `capabilities_for('arquivo')` responde sem credencial,
como o schema de conexão exige; `get_provider('arquivo', …)` devolve um adaptador
VAZIO (nenhuma linha), porque quem tem as linhas é a request de upload, não a
conexão. Um consumidor genérico que chegue aqui pela conexão (o sync de
movimentos, por exemplo) precisa ser barrado ANTES — é o 409 `ORIGEM_POR_ARQUIVO`
da 14.3 — senão leria "zero linhas" e marcaria a base inteira como ausente.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.db.models.client_connection import ProviderType
from app.integrations.providers.base import (
    Capability,
    ProviderAccount,
    ProviderEntry,
    ProviderOpenTitle,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    import httpx

    from app.core.config import Settings
    from app.integrations.providers.base import ProviderCredentials

#: O que a origem por arquivo sabe fazer. Congelado, como `OMIE_CAPABILITIES`.
FILE_CAPABILITIES = frozenset({Capability.LISTAR_LANCAMENTOS})


class FileProvider:
    """`OriginProvider` da origem por arquivo. Devolve o que recebeu — nada mais."""

    def __init__(self, entries: Sequence[ProviderEntry] = ()) -> None:
        self._entries = tuple(entries)

    @property
    def provider_type(self) -> str:
        return ProviderType.ARQUIVO.value

    @property
    def capabilities(self) -> frozenset[Capability]:
        return FILE_CAPABILITIES

    @property
    def entries(self) -> tuple[ProviderEntry, ...]:
        """As linhas em memória, na ordem em que o leitor as entregou."""
        return self._entries

    async def verify_credentials(self) -> None:
        """No-op: não há credencial. A capacidade NÃO é declarada — a rota de
        teste responde 409 antes de chegar aqui."""
        return None

    async def list_accounts(self) -> list[ProviderAccount]:
        """Sempre vazio: o arquivo não tem "contas do provedor" antes de ser lido."""
        return []

    async def list_entries(
        self, *, account_external_id: str, start: date, end: date
    ) -> list[ProviderEntry]:
        """As linhas recebidas, recortadas pelo período pedido.

        `account_external_id` é ignorado de propósito: a linha de arquivo carrega
        (ou não) a própria conta — o recorte por conta não faz sentido aqui, e a
        BACK 14.3 chama este método com o marcador de "todas as contas". O recorte
        por data é mantido para o adaptador honrar o contrato: quem pede junho não
        recebe julho.
        """
        return [entry for entry in self._entries if start <= entry.entry_date <= end]

    async def list_open_titles(
        self, *, known_account_external_ids: Sequence[str] = ()
    ) -> list[ProviderOpenTitle]:
        """Capacidade NÃO declarada; existe só para cumprir o Protocol."""
        return []

    async def aclose(self) -> None:
        return None


def build_file_provider(
    credentials: ProviderCredentials,
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> FileProvider:
    """Factory com a assinatura do registry — devolve um adaptador VAZIO.

    Os três parâmetros são ignorados: não há credencial, não há HTTP e as linhas
    vêm da request de upload (14.3), não da conexão. Existe para
    `get_provider('arquivo', …)` cumprir o contrato do registry sem caso especial.
    """
    return FileProvider()
