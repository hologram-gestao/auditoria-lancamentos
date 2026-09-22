"""Schemas de conexão de origem (Sprint 9, BACK 09.2 + 09.3).

O schema de RESPOSTA carrega o campo `capabilities` — capacidade é **dado
consultável**, não exceção: o front pergunta o que a origem faz e desenha a
tela, em vez de descobrir pelo 409.

⚠️ **Nenhum campo de credencial existe na RESPOSTA, nem mascarado.** Ciphertext
e IV não saem do banco: quem precisa deles é o adaptador, dentro do servidor. Um
`app_key: "****"` na resposta seria um campo que um dia alguém preenche.

Na ENTRADA a credencial chega como um mapa aberto (`credentials`), porque cada
provedor tem o seu shape — quem valida as chaves é o adaptador
(`OMIE_CREDENTIAL_KEYS`), fonte única. Os valores são `SecretStr`: nem o
`repr()` do request nem um log distraído os mostram.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.db.models.client_connection import (
    MAX_CONNECTION_LABEL_CHARS,
    ClientConnection,
    ConnectionStatus,
)
from app.integrations.providers.base import Capability
from app.integrations.providers.registry import capabilities_for


class ClientConnectionResponse(BaseModel):
    """Uma origem do cliente, como a API a devolve."""

    id: UUID
    provider_type: str = Field(description="Tipo da origem (ex.: `omie`).")
    label: str = Field(description="Como o humano chama esta origem.")
    status: ConnectionStatus = Field(
        description="`ativa` opera; `inativa` foi desligada; `erro` teve a credencial recusada."
    )
    last_checked_at: datetime | None = Field(
        default=None,
        description="Quando a credencial foi verificada pela última vez. Nulo = nunca.",
    )
    accounts_synced_at: datetime | None = Field(
        default=None,
        description="Último sync de contas DESTA conexão. Nulo = nunca sincronizou.",
    )
    capabilities: list[Capability] = Field(
        description=(
            "O que esta origem sabe fazer, derivado do adaptador do tipo. É DADO: "
            "a tela pergunta antes de oferecer a ação, em vez de descobrir pelo 409."
        )
    )

    @classmethod
    def from_connection(cls, connection: ClientConnection) -> ClientConnectionResponse:
        """Monta a resposta a partir da linha — capacidade vem do REGISTRY.

        Derivada, e não persistida: gravar capacidade no banco criaria uma
        segunda fonte que envelhece calada no dia em que o adaptador ganhar uma
        operação nova.
        """
        return cls(
            id=connection.id,
            provider_type=connection.provider_type,
            label=connection.label,
            status=ConnectionStatus(connection.status),
            last_checked_at=connection.last_checked_at,
            accounts_synced_at=connection.accounts_synced_at,
            capabilities=sorted(capabilities_for(connection.provider_type)),
        )


class ClientConnectionEnvelope(BaseModel):
    """Body de POST/PATCH/test — `{data: <conexão>}` (§7)."""

    data: ClientConnectionResponse


class ClientConnectionListPayload(BaseModel):
    connections: list[ClientConnectionResponse]


class ClientConnectionListResponse(BaseModel):
    """Body de GET /api/v1/clients/{client_id}/connections."""

    data: ClientConnectionListPayload


class ConnectionDeletedPayload(BaseModel):
    id: UUID
    deleted: bool


class ConnectionDeletedResponse(BaseModel):
    """Body de DELETE — a remoção é DEFINITIVA, não há linha para devolver."""

    data: ConnectionDeletedPayload


def _clean_label(value: str) -> str:
    """`min_length=1` sozinho aceitaria `"   "` — e um rótulo em branco não
    distingue duas conexões do mesmo tipo, que é a única coisa que ele faz."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("O rótulo não pode ser vazio.")
    return stripped


class CreateConnectionRequest(BaseModel):
    """Body de POST /api/v1/clients/{client_id}/connections.

    `extra="forbid"`: campo desconhecido é erro, não é ignorado em silêncio — um
    `client_id` no body, por exemplo, nunca decide tenant (quem decide é a rota).
    """

    model_config = ConfigDict(extra="forbid")

    provider_type: str = Field(
        description="Tipo da origem. Hoje só `omie`; tipo desconhecido é 422."
    )
    label: str | None = Field(
        default=None,
        max_length=MAX_CONNECTION_LABEL_CHARS,
        description=(
            "Como chamar esta origem. Omitir usa o rótulo padrão do tipo quando "
            "o cliente ainda não tem conexão daquele tipo; a partir da segunda, "
            "é obrigatório. Só-espaços é 422."
        ),
    )
    credentials: dict[str, SecretStr] = Field(
        description=(
            "Credenciais do provedor. Para o Omie: `appKey` e `appSecret` — as "
            "chaves aceitas são as do adaptador, e chave faltando é 422."
        )
    )

    @field_validator("label")
    @classmethod
    def _label_nao_vazio(cls, value: str | None) -> str | None:
        return None if value is None else _clean_label(value)


class UpdateConnectionRequest(BaseModel):
    """Body de PATCH /api/v1/clients/{client_id}/connections/{connection_id}.

    Os dois campos são opcionais e independentes: dá para renomear sem mexer na
    credencial, e trocar a credencial sem renomear. Corpo vazio é 422 — um PATCH
    que não pede nada é engano de quem chamou, não no-op silencioso.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(
        default=None,
        max_length=MAX_CONNECTION_LABEL_CHARS,
        description="Novo rótulo. Omitir mantém o atual.",
    )
    credentials: dict[str, SecretStr] | None = Field(
        default=None,
        description=(
            "Credenciais NOVAS, completas. Omitir mantém as atuais. Não é patch "
            "parcial: a credencial é cifrada inteira, então trocar uma chave "
            "isolada exigiria decifrar o resto."
        ),
    )

    @field_validator("label")
    @classmethod
    def _label_nao_vazio(cls, value: str | None) -> str | None:
        return None if value is None else _clean_label(value)
