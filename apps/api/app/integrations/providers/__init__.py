"""Provedores de origem — o contrato único e seus adaptadores (Sprint 9, R3; Sprint 14, arquivo)."""

from app.integrations.providers.base import (
    Capability,
    OriginProvider,
    ProviderAccount,
    ProviderCredentials,
    ProviderEntry,
)
from app.integrations.providers.file_adapter import (
    FILE_CAPABILITIES,
    FileProvider,
    build_file_provider,
)
from app.integrations.providers.omie_adapter import (
    OMIE_CAPABILITIES,
    OMIE_CREDENTIAL_KEYS,
    OmieProvider,
    build_omie_raw_client,
    omie_credentials_from,
    omie_credentials_payload,
)
from app.integrations.providers.registry import (
    capabilities_for,
    get_provider,
    requires_credentials,
    supported_provider_types,
)

__all__ = [
    "FILE_CAPABILITIES",
    "OMIE_CAPABILITIES",
    "OMIE_CREDENTIAL_KEYS",
    "Capability",
    "FileProvider",
    "OmieProvider",
    "OriginProvider",
    "ProviderAccount",
    "ProviderCredentials",
    "ProviderEntry",
    "build_file_provider",
    "build_omie_raw_client",
    "capabilities_for",
    "get_provider",
    "omie_credentials_from",
    "omie_credentials_payload",
    "requires_credentials",
    "supported_provider_types",
]
