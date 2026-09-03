"""Testes unitários do OmieClientesCache (86e33bmkb).

Foco: chave por (client_id, codigo) — isolamento entre tenants —, cache
negativo separado do positivo, e TTLs independentes.
"""

from __future__ import annotations

from uuid import uuid4

from app.integrations.omie.clientes_cache import OmieClientesCache


def test_get_name_miss_returns_none() -> None:
    cache = OmieClientesCache()
    assert cache.get_name(client_id=uuid4(), codigo=1) is None


def test_set_and_get_name() -> None:
    cache = OmieClientesCache()
    client_id = uuid4()
    cache.set_name(client_id=client_id, codigo=100001, name="MOINHO PRADO S.A.")
    assert cache.get_name(client_id=client_id, codigo=100001) == "MOINHO PRADO S.A."


def test_client_isolation_names_do_not_leak_between_tenants() -> None:
    """Mesmo código em tenants diferentes — o nome de um NUNCA vaza pro outro
    (§3.11/§3.15: fornecedor é dado do cliente final)."""
    cache = OmieClientesCache()
    a, b = uuid4(), uuid4()
    cache.set_name(client_id=a, codigo=100001, name="FORNECEDOR DO TENANT A")
    assert cache.get_name(client_id=b, codigo=100001) is None


def test_unresolved_is_separate_from_names() -> None:
    """Marcar irresolúvel não inventa nome; setar nome não limpa o marcador —
    quem consulta o positivo primeiro (o resolver) sempre vê o nome novo."""
    cache = OmieClientesCache()
    client_id = uuid4()
    cache.mark_unresolved(client_id=client_id, codigo=42)
    assert cache.known_unresolved(client_id=client_id, codigo=42)
    assert cache.get_name(client_id=client_id, codigo=42) is None

    cache.set_name(client_id=client_id, codigo=42, name="AGORA EXISTE")
    assert cache.get_name(client_id=client_id, codigo=42) == "AGORA EXISTE"
