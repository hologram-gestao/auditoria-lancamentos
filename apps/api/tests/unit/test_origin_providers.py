"""O contrato de provedor de origem e o adaptador Omie (Sprint 9, BACK 09.2).

Cobre:
    - registry: tipo conhecido resolve; tipo desconhecido → erro de VALIDAÇÃO
      (4xx), nunca 500; `capabilities_for` responde sem credencial nenhuma.
    - adaptador Omie: traduz `ContaCorrente`/`LancamentoExtrato` para os DTOs
      neutros com `respx` (mesmo caminho de `test_omie_client.py`), e mapeia
      `OmieAuthError` → `ProviderAuthError`.
    - cliente-demo: credencial com prefixo `FAKE_DEMO_OMIE_` resolve o
      `MockOmieClient` POR DENTRO do adaptador, SEM tocar a rede (nenhuma rota
      `respx` registrada — qualquer chamada HTTP estouraria).
    - credencial nunca vira texto em log nem em `repr()`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderAuthError, ValidationAppError
from app.db.models.client_connection import ProviderType
from app.integrations.omie.client import OmieClient
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient
from app.integrations.providers import (
    OMIE_CAPABILITIES,
    Capability,
    OmieProvider,
    OriginProvider,
    ProviderCredentials,
    capabilities_for,
    get_provider,
    omie_credentials_payload,
    supported_provider_types,
)

REAL_CREDENTIALS: ProviderCredentials = {
    "app_key": SecretStr("fake-app-key"),
    "app_secret": SecretStr("fake-app-secret"),
}
DEMO_CREDENTIALS: ProviderCredentials = {
    "app_key": SecretStr(f"{FAKE_DEMO_KEY_PREFIX}KEY"),
    "app_secret": SecretStr(f"{FAKE_DEMO_KEY_PREFIX}SECRET"),
}


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture(autouse=True)
def _no_mock_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    """O mock tem latência artificial para a demo — aqui ela só faz o teste lento."""
    from app.integrations.omie import mock_client

    for attr in (
        "_DELAY_LISTAR_CLIENTES_SECONDS",
        "_DELAY_LISTAR_CONTAS_SECONDS",
        "_DELAY_LISTAR_EXTRATO_SECONDS",
        "_DELAY_LISTAR_TITULOS_SECONDS",
    ):
        monkeypatch.setattr(mock_client, attr, 0.0)


def _omie_url(module: str, endpoint: str) -> str:
    return f"https://app.omie.com.br/api/v1/{module}/{endpoint}/"


class TestRegistry:
    def test_tipo_conhecido_resolve_o_adaptador(self, settings: Settings) -> None:
        provider = get_provider(ProviderType.OMIE.value, REAL_CREDENTIALS, settings)
        assert isinstance(provider, OmieProvider)
        assert isinstance(provider, OriginProvider)
        assert provider.provider_type == ProviderType.OMIE.value

    def test_tipo_desconhecido_e_erro_de_validacao_nunca_500(self, settings: Settings) -> None:
        """Entrada inválida é 4xx — um `KeyError` viraria 500 e mentiria sobre a causa."""
        with pytest.raises(ValidationAppError) as exc:
            get_provider("contabilix", REAL_CREDENTIALS, settings)
        assert exc.value.status_code < 500
        assert "contabilix" in exc.value.message

    def test_capabilities_for_nao_exige_credencial(self) -> None:
        """Capacidade é do TIPO — perguntar não pode exigir decifrar segredo."""
        assert capabilities_for(ProviderType.OMIE.value) == OMIE_CAPABILITIES

    def test_capabilities_for_recusa_tipo_desconhecido(self) -> None:
        with pytest.raises(ValidationAppError):
            capabilities_for("contabilix")

    def test_o_registry_tem_um_provedor_hoje(self) -> None:
        """Sem 2º provedor nesta sprint — o objetivo é ele CABER, não existir."""
        assert supported_provider_types() == (ProviderType.OMIE.value,)

    def test_credencial_incompleta_e_erro_de_validacao(self, settings: Settings) -> None:
        with pytest.raises(ValidationAppError) as exc:
            get_provider(ProviderType.OMIE.value, {"app_key": SecretStr("x")}, settings)
        assert "app_secret" in exc.value.message


class TestOmieCapabilities:
    def test_declara_as_quatro(self, settings: Settings) -> None:
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        assert provider.capabilities == frozenset(Capability)

    def test_escrever_esta_declarada_porque_incluirlanccc_existe(self, settings: Settings) -> None:
        """Capacidade diz o que o provedor SABE fazer; se o lançamento pode sair
        é o kill-switch `OMIE_POSTING_ENABLED`, que não mora aqui."""
        assert Capability.ESCREVER in OmieProvider(REAL_CREDENTIALS, settings).capabilities


class TestOmieAdapterTraduz:
    @respx.mock
    async def test_list_accounts_vira_dto_neutro(self, settings: Settings) -> None:
        respx.post(_omie_url("geral", "contacorrente")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ListarContasCorrentes": [
                        {
                            "nCodCC": 4214850,
                            "descricao": "Sicredi 91263-1",
                            "codigo_banco": "748",
                            "tipo_conta_corrente": "CC",
                        }
                    ],
                    "pagina": 1,
                    "total_de_paginas": 1,
                    "registros": 1,
                    "total_de_registros": 1,
                },
            )
        )
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        contas = await provider.list_accounts()

        assert len(contas) == 1
        # `external_id` é TEXTO mesmo vindo de inteiro — id de terceiro não é
        # número nosso.
        assert contas[0].external_id == "4214850"
        assert contas[0].name == "Sicredi 91263-1"
        assert contas[0].bank_code == "748"
        assert contas[0].account_type == "CC"

    @respx.mock
    async def test_list_entries_traz_valor_com_sinal_e_codigo_sem_nome(
        self, settings: Settings
    ) -> None:
        respx.post(_omie_url("financas", "extrato")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "listaMovimentos": [
                        {
                            "nCodLancamento": 777,
                            "cNatureza": "D",
                            "dDataLancamento": "15/04/2026",
                            "nValorDocumento": 250.50,
                            "cSituacao": "Conciliado",
                            "cObservacoes": "Pagamento fornecedor",
                            "cCodCategoria": "2.04.78",
                            "cDesCategoria": "Despesas com IOF",
                            "cRazCliente": "MOINHO PRADO LTDA",
                            "nCodCliente": 100001,
                        }
                    ]
                },
            )
        )
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        entries = await provider.list_entries(
            account_external_id="4214850", start=date(2026, 4, 1), end=date(2026, 4, 30)
        )

        assert len(entries) == 1
        entry = entries[0]
        assert entry.external_id == "777"
        assert entry.entry_date == date(2026, 4, 15)
        # Débito vira negativo aqui — a convenção de natureza morre no adaptador.
        assert entry.amount == Decimal("-250.50")
        assert entry.status == "Conciliado"
        # CÓDIGO viaja; NOME não (§4.5 — nome resolve em runtime, com cache TTL).
        assert entry.category_code == "2.04.78"
        assert entry.supplier_code == "100001"
        payload = entry.model_dump_json()
        assert "MOINHO PRADO" not in payload
        assert "Despesas com IOF" not in payload

    async def test_conta_nao_numerica_e_erro_de_validacao(self, settings: Settings) -> None:
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        with pytest.raises(ValidationAppError):
            await provider.list_entries(
                account_external_id="conta-x", start=date(2026, 4, 1), end=date(2026, 4, 30)
            )

    @respx.mock
    async def test_auth_do_omie_vira_provider_auth_error(self, settings: Settings) -> None:
        """A taxonomia neutra é o que a 09.3/09.6 tratam — não o erro do Omie."""
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "faultstring": "SOAP-ENV:Client-101: App Key inválido",
                    "faultcode": "SOAP-ENV:Client-101",
                },
            )
        )
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        with pytest.raises(ProviderAuthError):
            await provider.verify_credentials()

    @respx.mock
    async def test_verify_credentials_ok_nao_levanta(self, settings: Settings) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json={"pagina": 1, "total_de_registros": 0})
        )
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        assert await provider.verify_credentials() is None

    def test_provider_auth_error_e_5xx_e_nao_vaza_a_credencial(self) -> None:
        exc = ProviderAuthError("recusada")
        assert exc.status_code == 502
        assert "fake-app-key" not in exc.user_message


class TestClienteDemoNaoTocaARede:
    """O prefixo `FAKE_DEMO_OMIE_` resolve o mock POR DENTRO do adaptador.

    Sem `@respx.mock` e sem rota registrada de propósito: se qualquer um destes
    métodos tentasse falar com a Omie, a chamada HTTP real falharia o teste.
    """

    async def test_verifica_lista_contas_e_lista_lancamentos_sem_http(
        self, settings: Settings
    ) -> None:
        provider = OmieProvider(DEMO_CREDENTIALS, settings)
        assert isinstance(provider.raw_client, MockOmieClient)

        assert await provider.verify_credentials() is None

        contas = await provider.list_accounts()
        assert [c.external_id for c in contas] == ["900000001", "900000002", "900000003"]
        assert contas[2].account_type == "CR"  # cartão — `CA` é Conta Aplicação

        entries = await provider.list_entries(
            account_external_id="900000001", start=date(2026, 4, 1), end=date(2026, 4, 30)
        )
        assert entries
        assert all(e.external_id for e in entries)

        await provider.aclose()

    def test_credencial_real_nao_resolve_o_mock(self, settings: Settings) -> None:
        provider = OmieProvider(REAL_CREDENTIALS, settings)
        assert isinstance(provider.raw_client, OmieClient)
        assert not isinstance(provider.raw_client, MockOmieClient)


class TestDocDoContratoBateComAsChavesReais:
    """A descrição de `credentials` é CONTRATO — vai para o OpenAPI e daí para o front.

    Ela já saiu errada uma vez (`appKey`/`appSecret` em camelCase contra
    `OMIE_CREDENTIAL_KEYS` em snake_case) e o front precisou escrever um
    comentário corrigindo o backend. Aqui as duas fontes se olham.
    """

    def test_a_descricao_cita_exatamente_as_chaves_do_adaptador(self) -> None:
        from app.integrations.providers.omie_adapter import OMIE_CREDENTIAL_KEYS
        from app.modules.client_connections.schemas import CreateConnectionRequest

        descricao = CreateConnectionRequest.model_fields["credentials"].description or ""
        for chave in OMIE_CREDENTIAL_KEYS:
            assert f"`{chave}`" in descricao, f"a descrição não cita `{chave}`"

    def test_a_descricao_nao_cita_chave_em_camelcase(self) -> None:
        from app.modules.client_connections.schemas import CreateConnectionRequest

        descricao = CreateConnectionRequest.model_fields["credentials"].description or ""
        assert "appKey" not in descricao
        assert "appSecret" not in descricao


class TestCredencialNaoVazaEmTexto:
    def test_secretstr_mascara_o_repr(self) -> None:
        creds = omie_credentials_payload("segredo-app-key", "segredo-app-secret")
        assert "segredo-app-key" not in repr(creds)
        assert "segredo-app-secret" not in repr(creds)
        # E o texto continua recuperável por quem realmente precisa dele.
        assert creds["app_key"].get_secret_value() == "segredo-app-key"

    async def test_construir_o_adaptador_nao_loga_a_credencial(
        self, settings: Settings, capsys: pytest.CaptureFixture[str]
    ) -> None:
        provider = OmieProvider(DEMO_CREDENTIALS, settings)
        await provider.verify_credentials()
        captured = capsys.readouterr()
        saida = captured.out + captured.err
        assert FAKE_DEMO_KEY_PREFIX + "KEY" not in saida
        assert FAKE_DEMO_KEY_PREFIX + "SECRET" not in saida
