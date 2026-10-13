"""A chave `cCodIntLanc` é POR-LINHA e cabe em `string20` (BACK 07.2).

Os dois modos de falha que estes testes existem para impedir:

  - **chave por conteúdo** → duas compras idênticas na mesma fatura colapsariam
    numa chave só e a segunda **nunca seria lançada**. Dinheiro faltando na
    contabilidade do cliente — e o critério de rollback da sprint só vigia
    duplicado, então isso passaria despercebido;
  - **encoding que trunca demais** → duas linhas distintas com a mesma chave,
    mesmo problema por outro caminho.
"""

from __future__ import annotations

import string
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.db.models import COD_INT_LANC_MAX_LENGTH
from app.modules.reconciliations.omie_posting.keys import (
    COD_INT_LANC_PREFIX,
    PAYABLE_KEY_PREFIX,
    PAYMENT_KEY_PREFIX,
    SUPPLIER_KEY_PREFIX,
    derive_cod_int_lanc,
    derive_payable_key,
    derive_payment_key,
    derive_supplier_key,
)

#: base32 (RFC 4648) — o que a chave pode conter depois do prefixo.
_BASE32_ALPHABET = set(string.ascii_uppercase + "234567")


@pytest.mark.unit
class TestCodIntLancEncoding:
    def test_fits_in_omie_string20(self) -> None:
        for _ in range(1000):
            key = derive_cod_int_lanc(uuid4())
            assert len(key) <= COD_INT_LANC_MAX_LENGTH, key

    def test_uses_the_full_budget(self) -> None:
        """Se sobrar caractere, sobra entropia jogada fora — e o cálculo de
        colisão do módulo deixa de valer."""
        assert len(derive_cod_int_lanc(uuid4())) == COD_INT_LANC_MAX_LENGTH

    def test_is_prefixed_for_the_operator(self) -> None:
        """O operador localiza o lançamento no Omie por esta chave."""
        assert derive_cod_int_lanc(uuid4()).startswith(COD_INT_LANC_PREFIX)

    def test_alphabet_is_safe_for_an_external_text_field(self) -> None:
        suffix = derive_cod_int_lanc(uuid4())[len(COD_INT_LANC_PREFIX) :]
        assert set(suffix) <= _BASE32_ALPHABET, suffix
        assert "=" not in suffix, "padding do base32 vazou para a chave"

    def test_is_deterministic(self) -> None:
        """O caminho de timeout (07.4) reconsulta a Omie pela MESMA chave sem
        depender de ter conseguido gravar algo entre o envio e a falha."""
        entry_id = uuid4()
        assert derive_cod_int_lanc(entry_id) == derive_cod_int_lanc(entry_id)

    def test_does_not_leak_the_primary_key(self) -> None:
        entry_id = UUID("2f9d6c1a-7b4e-4c8a-9f31-0d5e2a8b7c64")
        key = derive_cod_int_lanc(entry_id)
        assert entry_id.hex[:8].upper() not in key
        assert str(entry_id).replace("-", "").upper()[:8] not in key


@pytest.mark.unit
class TestKeyIsPerLineNotPerContent:
    def test_identical_purchases_get_different_keys(self) -> None:
        """Dois cafés de R$ 12,00 no mesmo dia, no mesmo lugar: DUAS chaves.

        O conteúdo é literalmente o mesmo — só a identidade da linha difere. É
        exatamente o caso que um hash de data+valor+descrição colapsaria.
        """
        same_content = {
            "transaction_date": date(2026, 4, 15),
            "amount": Decimal("-12.00"),
            "description": "CAFETERIA DO LARGO",
        }
        line_a, line_b = uuid4(), uuid4()
        assert same_content  # o conteúdo não entra na derivação — de propósito
        assert derive_cod_int_lanc(line_a) != derive_cod_int_lanc(line_b)

    def test_no_collision_across_many_lines(self) -> None:
        """50 mil linhas de um cliente, nenhuma chave repetida.

        Não prova ausência de colisão (85 bits de digest — a chance existe, e é
        por isso que o `UNIQUE(client_id, cod_int_lanc)` está no banco). Prova
        que o encoding não trunca a ponto de colidir na escala real de uso.
        """
        keys = {derive_cod_int_lanc(uuid4()) for _ in range(50_000)}
        assert len(keys) == 50_000


@pytest.mark.unit
class TestChavesDaProvisao:
    """86e3n70q0 (bloco B): uma chave por passo da provisão, todas derivadas."""

    def test_as_quatro_chaves_de_uma_linha_sao_distintas_e_estaveis(self) -> None:
        entry = UUID("12345678-1234-5678-1234-567812345678")
        cnpj = "12345678000195"
        keys = [
            derive_cod_int_lanc(entry),
            derive_payable_key(entry),
            derive_payment_key(entry),
            derive_supplier_key(cnpj),
        ]
        assert len(set(keys)) == 4
        assert keys == [
            derive_cod_int_lanc(entry),
            derive_payable_key(entry),
            derive_payment_key(entry),
            derive_supplier_key(cnpj),
        ]
        prefixes = {k[:3] for k in keys}
        assert prefixes == {
            COD_INT_LANC_PREFIX,
            PAYABLE_KEY_PREFIX,
            PAYMENT_KEY_PREFIX,
            SUPPLIER_KEY_PREFIX,
        }

    def test_cabem_no_teto_mais_apertado_da_omie(self) -> None:
        """`codigo_baixa_integracao` é `string20`: todas cabem nos 20."""
        for _ in range(500):
            entry = uuid4()
            for key in (derive_payable_key(entry), derive_payment_key(entry)):
                assert len(key) == COD_INT_LANC_MAX_LENGTH
                assert set(key[3:]) <= _BASE32_ALPHABET
        assert len(derive_supplier_key("12345678000195")) == COD_INT_LANC_MAX_LENGTH

    def test_titulo_e_baixa_compartilham_o_sufixo_da_linha(self) -> None:
        """A pessoa acha título e baixa de uma compra no Omie pelo mesmo final."""
        entry = uuid4()
        assert derive_payable_key(entry)[3:] == derive_payment_key(entry)[3:]
        assert derive_payable_key(entry)[3:] == derive_cod_int_lanc(entry)[3:]

    def test_linhas_distintas_dao_chaves_distintas(self) -> None:
        keys = {derive_payable_key(uuid4()) for _ in range(2000)}
        assert len(keys) == 2000

    def test_fornecedor_e_um_por_cnpj_nao_por_linha(self) -> None:
        assert derive_supplier_key("12345678000195") == derive_supplier_key("12345678000195")
        assert derive_supplier_key("12345678000195") != derive_supplier_key("98765432000198")
        # O sufixo do fornecedor não coincide com o de nenhuma linha por construção
        # (outro `person`), então não há como confundir as duas famílias.

    @pytest.mark.parametrize("raw", ["12.345.678/0001-95", "123", "1234567800019X"])
    def test_fornecedor_exige_o_cnpj_normalizado(self, raw: str) -> None:
        with pytest.raises(ValueError, match="normalizado"):
            derive_supplier_key(raw)
