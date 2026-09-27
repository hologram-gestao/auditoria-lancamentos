"""O provedor `arquivo` cumpre o contrato da Sprint 9 (Sprint 14, BACK 14.1 — R1).

Cobre:
    - `ProviderType.ARQUIVO` existe e o registry resolve o tipo nas DUAS portas;
    - `FileProvider` cumpre `OriginProvider` (`runtime_checkable`);
    - as capacidades são EXATAMENTE `{listar_lancamentos}` — sem escrever, sem
      listar contas, sem verificar credencial, sem títulos em aberto (nominal de
      propósito: derivar de `FILE_CAPABILITIES` faria o teste concordar com
      qualquer mudança);
    - `list_entries` devolve o que o adaptador recebeu em memória, recortado pelo
      período; `list_accounts` é vazio; `verify_credentials` é no-op;
    - `requires_credentials`: `omie` sim, `arquivo` não, desconhecido é 4xx.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationAppError
from app.db.models.client_connection import ProviderType
from app.integrations.providers import (
    FILE_CAPABILITIES,
    Capability,
    FileProvider,
    OriginProvider,
    ProviderEntry,
    build_file_provider,
    capabilities_for,
    get_provider,
    requires_credentials,
    supported_provider_types,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def settings() -> Settings:
    return get_settings()


def _entry(day: int, amount: str = "-10.00") -> ProviderEntry:
    return ProviderEntry(
        external_id=f"deadbeef:{day}",
        entry_date=date(2026, 6, day),
        amount=Decimal(amount),
        description="linha",
        category_code="arq-1",
    )


class TestTipoNoRegistry:
    def test_o_tipo_arquivo_existe(self) -> None:
        assert ProviderType.ARQUIVO.value == "arquivo"
        assert "arquivo" in supported_provider_types()

    def test_capabilities_for_arquivo_sem_credencial(self) -> None:
        assert capabilities_for("arquivo") == FILE_CAPABILITIES

    def test_get_provider_arquivo_devolve_adaptador_vazio(self, settings: Settings) -> None:
        provider = get_provider("arquivo", {}, settings)
        assert isinstance(provider, FileProvider)
        assert provider.entries == ()

    def test_factory_ignora_credencial_e_http(self, settings: Settings) -> None:
        provider = build_file_provider({}, settings, http_client=None)
        assert provider.provider_type == "arquivo"


class TestConformidade:
    def test_cumpre_o_protocolo(self) -> None:
        assert isinstance(FileProvider(), OriginProvider)

    def test_capacidades_sao_exatamente_listar_lancamentos(self) -> None:
        """Nominal de propósito — o que a resposta de conexão promete ao front."""
        assert FileProvider().capabilities == frozenset({Capability.LISTAR_LANCAMENTOS})
        assert {c.value for c in capabilities_for("arquivo")} == {"listar_lancamentos"}

    @pytest.mark.parametrize(
        "ausente",
        [
            Capability.ESCREVER,
            Capability.LISTAR_CONTAS,
            Capability.VERIFICAR_CREDENCIAL,
            Capability.LISTAR_TITULOS_EM_ABERTO,
        ],
    )
    def test_nao_declara(self, ausente: Capability) -> None:
        assert ausente not in FileProvider().capabilities


class TestComportamento:
    async def test_list_entries_devolve_o_que_recebeu_no_periodo(self) -> None:
        provider = FileProvider([_entry(5), _entry(20), _entry(30)])
        junho = await provider.list_entries(
            account_external_id="*", start=date(2026, 6, 1), end=date(2026, 6, 30)
        )
        assert [e.entry_date.day for e in junho] == [5, 20, 30]
        metade = await provider.list_entries(
            account_external_id="*", start=date(2026, 6, 1), end=date(2026, 6, 15)
        )
        assert [e.entry_date.day for e in metade] == [5]

    async def test_list_entries_ignora_a_conta(self) -> None:
        """A linha de arquivo carrega (ou não) a própria conta; o recorte não é por conta."""
        provider = FileProvider([_entry(5)])
        a = await provider.list_entries(
            account_external_id="1", start=date(2026, 6, 1), end=date(2026, 6, 30)
        )
        b = await provider.list_entries(
            account_external_id="2", start=date(2026, 6, 1), end=date(2026, 6, 30)
        )
        assert a == b == [_entry(5)]

    async def test_list_accounts_e_vazio_e_verify_e_no_op(self) -> None:
        provider = FileProvider()
        assert await provider.list_accounts() == []
        assert await provider.verify_credentials() is None
        assert await provider.list_open_titles() == []
        await provider.aclose()

    def test_as_linhas_sao_imutaveis_para_quem_le(self) -> None:
        entries = [_entry(1)]
        provider = FileProvider(entries)
        entries.append(_entry(2))
        assert len(provider.entries) == 1


class TestRequiresCredentials:
    def test_omie_exige_e_arquivo_nao(self) -> None:
        assert requires_credentials("omie") is True
        assert requires_credentials("arquivo") is False

    def test_deriva_da_capacidade_verificar_credencial(self) -> None:
        """Uma regra, não uma segunda lista: quem sabe VERIFICAR credencial tem credencial."""
        for provider_type in supported_provider_types():
            assert requires_credentials(provider_type) == (
                Capability.VERIFICAR_CREDENCIAL in capabilities_for(provider_type)
            )

    def test_tipo_desconhecido_e_4xx(self) -> None:
        with pytest.raises(ValidationAppError):
            requires_credentials("contabilix")
