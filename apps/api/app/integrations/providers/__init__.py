"""Provedores de origem — o contrato único e seus adaptadores (Sprint 9, R3)."""

from app.integrations.providers.base import (
    Capability,
    OriginProvider,
    ProviderAccount,
    ProviderCredentials,
    ProviderEntry,
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
    supported_provider_types,
)

__all__ = [
    "OMIE_CAPABILITIES",
    "OMIE_CREDENTIAL_KEYS",
    "Capability",
    "OmieProvider",
    "OriginProvider",
    "ProviderAccount",
    "ProviderCredentials",
    "ProviderEntry",
    "build_omie_raw_client",
    "capabilities_for",
    "get_provider",
    "omie_credentials_from",
    "omie_credentials_payload",
    "supported_provider_types",
]
