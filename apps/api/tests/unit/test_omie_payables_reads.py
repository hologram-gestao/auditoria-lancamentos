"""CNPJ, mock do Contas a Pagar e as leituras da tela (86e3n70q0, bloco B)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.cnpj import format_cnpj, normalize_cnpj
from app.core.config import get_settings
from app.core.exceptions import OmieFaultError, OmieTimeoutError, SupplierNotFoundError
from app.integrations.omie import mock_client
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.departamentos_cache import OmieDepartamentosCache
from app.integrations.omie.mock_client import MockOmieClient, reset_mock_payables_state
from app.integrations.omie.schemas import (
    ClienteOmie,
    DepartamentoOmie,
    IncluirClienteRequest,
    IncluirContaPagarRequest,
    LancarPagamentoRequest,
)
from app.modules.omie_data.payables_service import (
    OmieDepartamentosService,
    OmieFornecedorLookupService,
    pick_supplier,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fast_mock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(mock_client, "_DELAY_LISTAR_CONTAS_SECONDS", 0.0)
    reset_mock_payables_state()
    yield
    reset_mock_payables_state()


def _mock() -> MockOmieClient:
    return MockOmieClient(
        OmieCredentials(app_key=SecretStr("FAKE_DEMO_OMIE_x"), app_secret=SecretStr("s")),
        get_settings(),
    )


class TestCnpj:
    @pytest.mark.parametrize(
        "raw", ["12.345.678/0001-95", "12345678000195", " 12.345.678/0001-95 "]
    )
    def test_valid_with_or_without_mask(self, raw: str) -> None:
        assert normalize_cnpj(raw) == "12345678000195"

    @pytest.mark.parametrize(
        "raw",
        [
            "12.345.678/0001-96",  # dígito verificador
            "1234567800019",  # 13 dígitos
            "11.111.111/1111-11",  # todos iguais
            "12a45678000195",  # caractere fora da máscara
            "",
        ],
    )
    def test_invalid_is_refused_without_echoing_the_value(self, raw: str) -> None:
        with pytest.raises(ValueError, match="CNPJ") as exc:
            normalize_cnpj(raw)
        assert "12345678" not in str(exc.value)

    def test_format_is_the_mask_the_capture_sent(self) -> None:
        assert format_cnpj("99999001000159") == "99.999.001/0001-59"

    def test_format_refuses_non_normalized_input(self) -> None:
        with pytest.raises(ValueError, match="14 dígitos"):
            format_cnpj("12.345.678/0001-95")


class TestMockIsCoherent:
    async def test_lookup_finds_seed_and_misses_unknown(self) -> None:
        omie = _mock()
        assert [
            c.codigo_cliente_omie
            for c in await omie.listar_clientes_por_cnpj(cnpj_cpf="12.345.678/0001-95")
        ] == [700_000_001]
        assert await omie.listar_clientes_por_cnpj(cnpj_cpf="11.222.333/0001-81") == []

    async def test_created_supplier_is_found_and_creation_is_idempotent(self) -> None:
        omie = _mock()
        request = IncluirClienteRequest(
            codigo_cliente_integracao="HFNABC",
            razao_social="NOVO LTDA",
            cnpj_cpf="11.222.333/0001-81",
            email="a@b.com",
        )
        first = await omie.incluir_cliente(request)
        second = await omie.incluir_cliente(request)
        assert first.codigo_cliente_omie == second.codigo_cliente_omie
        found = await omie.listar_clientes_por_cnpj(cnpj_cpf="11222333000181")
        assert [c.codigo_cliente_omie for c in found] == [first.codigo_cliente_omie]

    async def test_supplier_without_email_is_refused_like_the_real_one(self) -> None:
        with pytest.raises(OmieFaultError):
            await _mock().incluir_cliente(
                IncluirClienteRequest(
                    codigo_cliente_integracao="HFNX",
                    razao_social="X",
                    cnpj_cpf="11.222.333/0001-81",
                    email=" ",
                )
            )

    async def test_title_and_payment_ids_are_stable_by_key(self) -> None:
        omie = _mock()
        title = IncluirContaPagarRequest(
            codigo_lancamento_integracao="HCPAAA",
            codigo_cliente_fornecedor=1,
            data_vencimento="10/11/2026",
            valor_documento=Decimal("1.00"),
            codigo_categoria="2.01.03",
            id_conta_corrente=900_000_003,
        )
        assert (await omie.incluir_conta_pagar(title)).codigo_lancamento_omie == (
            await omie.incluir_conta_pagar(title)
        ).codigo_lancamento_omie
        baixa = LancarPagamentoRequest(
            codigo_lancamento=1,
            codigo_baixa_integracao="HBXAAA",
            codigo_conta_corrente=900_000_003,
            valor=Decimal("1.00"),
            data="10/10/2026",
        )
        assert (await omie.lancar_pagamento(baixa)).codigo_baixa == (
            await omie.lancar_pagamento(baixa)
        ).codigo_baixa


class _FakeOmie:
    """Só o que os serviços chamam, contando as chamadas e o `aclose`."""

    def __init__(
        self,
        *,
        departamentos: list[DepartamentoOmie] | None = None,
        cadastros: list[ClienteOmie] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.departamentos = departamentos or []
        self.cadastros = cadastros or []
        self.error = error
        self.calls = 0
        self.closed = 0
        self.last_cnpj: str | None = None

    async def listar_departamentos(self) -> list[DepartamentoOmie]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.departamentos

    async def listar_clientes_por_cnpj(self, *, cnpj_cpf: str) -> list[ClienteOmie]:
        self.calls += 1
        self.last_cnpj = cnpj_cpf
        if self.error is not None:
            raise self.error
        return self.cadastros

    async def aclose(self) -> None:
        self.closed += 1


def _factory(fake: _FakeOmie):  # type: ignore[no-untyped-def]
    async def build() -> OmieClient:
        return fake  # type: ignore[return-value]

    return build


def _dep(codigo: str, descricao: str, estrutura: str, inativo: str = "N") -> DepartamentoOmie:
    return DepartamentoOmie(
        codigo=codigo, descricao=descricao, estrutura=estrutura, inativo=inativo
    )


class TestDepartamentosService:
    async def test_only_active_in_tree_order_and_cached(self) -> None:
        fake = _FakeOmie(
            departamentos=[
                _dep("3", "Loja", "001.002"),
                _dep("1", "z[Sintético] Op", "001"),
                _dep("9", "Velho", "000", inativo="S"),
                _dep("2", "Produção", "001.001"),
            ]
        )
        service = OmieDepartamentosService(OmieDepartamentosCache())
        client_id = uuid4()
        first = await service.list_departamentos(
            client_id=client_id, omie_client_factory=_factory(fake)
        )
        second = await service.list_departamentos(
            client_id=client_id, omie_client_factory=_factory(fake)
        )
        assert [d.codigo for d in first] == ["1", "2", "3"]
        assert second == first
        assert fake.calls == 1
        assert fake.closed == 1

    async def test_refresh_goes_back_to_the_origin(self) -> None:
        fake = _FakeOmie(departamentos=[_dep("1", "A", "001")])
        service = OmieDepartamentosService(OmieDepartamentosCache())
        client_id = uuid4()
        await service.list_departamentos(client_id=client_id, omie_client_factory=_factory(fake))
        await service.list_departamentos(
            client_id=client_id, omie_client_factory=_factory(fake), refresh=True
        )
        assert fake.calls == 2

    async def test_timeout_is_not_an_empty_list(self) -> None:
        fake = _FakeOmie(error=OmieTimeoutError("t"))
        service = OmieDepartamentosService(OmieDepartamentosCache())
        with pytest.raises(OmieTimeoutError) as exc:
            await service.list_departamentos(client_id=uuid4(), omie_client_factory=_factory(fake))
        assert "departamentos" in exc.value.user_message
        assert fake.closed == 1


class TestFornecedorLookup:
    async def test_found_prefers_the_active_and_sends_the_mask(self) -> None:
        fake = _FakeOmie(
            cadastros=[
                ClienteOmie(codigo_cliente_omie=1, razao_social="Velho", inativo="S"),
                ClienteOmie(codigo_cliente_omie=2, razao_social="Ativo", inativo="N"),
            ]
        )
        item = await OmieFornecedorLookupService().find_by_cnpj(
            client_id=uuid4(), cnpj_digits="12345678000195", omie_client_factory=_factory(fake)
        )
        assert item.codigo == 2
        assert fake.last_cnpj == "12.345.678/0001-95"

    async def test_not_found_is_a_typed_404(self) -> None:
        with pytest.raises(SupplierNotFoundError):
            await OmieFornecedorLookupService().find_by_cnpj(
                client_id=uuid4(),
                cnpj_digits="12345678000195",
                omie_client_factory=_factory(_FakeOmie()),
            )

    def test_pick_supplier_falls_back_to_the_first(self) -> None:
        only_inactive = [ClienteOmie(codigo_cliente_omie=5, inativo="S")]
        assert pick_supplier(only_inactive) is only_inactive[0]
        assert pick_supplier([]) is None
