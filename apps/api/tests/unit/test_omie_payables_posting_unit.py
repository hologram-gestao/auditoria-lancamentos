"""Peças puras da provisão no Contas a Pagar (86e3n70q0, bloco B).

A máquina inteira (cada passo falhando e retomando, timeout com reenvio, os dois
processos) roda contra o banco em `tests/integration/test_omie_payables_posting.py`.
Aqui ficam as regras que decidem DINHEIRO sem precisar de banco: as somas que
fecham o título, a trava de troca de forma e o contrato do pedido.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import ErrorCode
from app.db.models import ReconciliationOmiePosting
from app.modules.reconciliations.omie_posting.keys import derive_payable_key
from app.modules.reconciliations.omie_posting.schemas import (
    OmiePostingLineRequest,
    OmiePostingLineResult,
)
from app.modules.reconciliations.omie_posting.service import (
    MAX_STEP_SENDS,
    _category_split,
    _department_split,
    _kind_switch_allowed,
    _percentuals,
    _ProvisionTally,
    _stuck_message,
)

CNPJ = "12.345.678/0001-95"


def _req(**kwargs: object) -> OmiePostingLineRequest:
    return OmiePostingLineRequest.model_validate({"file_entry_id": str(uuid4()), **kwargs})


def _posting(**kwargs: object) -> ReconciliationOmiePosting:
    defaults: dict[str, object] = {
        "kind": "provisao",
        "step": "fornecedor",
        "status": "pending",
        "attempts": 0,
        "error_code": None,
        "omie_lancamento_id": None,
        "file_entry_id": uuid4(),
    }
    defaults.update(kwargs)
    return ReconciliationOmiePosting(**defaults)


class TestPedido:
    def test_padrao_e_provisionar(self) -> None:
        assert _req(cod_categoria="2.01").modo == "provisionar"

    def test_cnpj_normalizado(self) -> None:
        assert _req(cod_categoria="2.01", cnpj_fornecedor=CNPJ).cnpj_fornecedor == "12345678000195"

    @pytest.mark.parametrize(
        "body",
        [
            {"modo": "direto"},  # direto sem categoria
            {"modo": "direto", "cod_categoria": "1", "cnpj_fornecedor": CNPJ},
            {
                "modo": "direto",
                "cod_categoria": "1",
                "departamentos": [{"codigo": "1", "percentual": "100"}],
            },
            {},  # provisão sem categoria nenhuma
            {"cod_categoria": "1", "categorias": [{"codigo": "1", "valor": "1"}]},  # os dois
            {"cod_categoria": "1", "cnpj_fornecedor": CNPJ, "razao_social": "X"},  # sem e-mail
            {"cod_categoria": "1", "razao_social": "X", "email": "a@b.c"},  # cadastrar sem CNPJ
            {"cod_categoria": "1", "cnpj_fornecedor": "12.345.678/0001-96"},  # DV
            {"cod_categoria": "1", "cnpj_fornecedor": CNPJ, "razao_social": "X", "email": "x"},
            {"categorias": [{"codigo": "1", "valor": "1"}, {"codigo": "1", "valor": "2"}]},
            {
                "cod_categoria": "1",
                "departamentos": [
                    {"codigo": "1", "percentual": "50"},
                    {"codigo": "1", "percentual": "50"},
                ],
            },
            {"cod_categoria": "1", "departamentos": [{"codigo": "1", "percentual": "99.98"}]},
            {"categorias": [{"codigo": "1", "valor": "0"}]},
            {"categorias": [{"codigo": "1", "valor": "1.001"}]},
        ],
    )
    def test_pedido_incoerente_e_recusado(self, body: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            _req(**body)

    def test_percentuais_dentro_do_centavo_passam(self) -> None:
        req = _req(
            cod_categoria="1",
            departamentos=[
                {"codigo": "1", "percentual": "33.33"},
                {"codigo": "2", "percentual": "33.33"},
                {"codigo": "3", "percentual": "33.33"},
            ],
        )
        assert req.departamentos is not None
        assert len(req.departamentos) == 3


class TestSomas:
    def test_uma_categoria_leva_o_total(self) -> None:
        assert _category_split(_req(cod_categoria="2.01"), Decimal("12.34")) == [
            ("2.01", Decimal("12.34"))
        ]

    def test_centavo_de_diferenca_vai_para_a_ultima(self) -> None:
        req = _req(categorias=[{"codigo": "a", "valor": "3.38"}, {"codigo": "b", "valor": "96.61"}])
        split = _category_split(req, Decimal("100.00"))
        assert split == [("a", Decimal("3.38")), ("b", Decimal("96.62"))]
        assert split is not None
        assert sum(v for _, v in split) == Decimal("100.00")

    @pytest.mark.parametrize("valores", [("3.38", "90.00"), ("50.00", "50.02")])
    def test_soma_que_nao_fecha_e_none(self, valores: tuple[str, str]) -> None:
        req = _req(
            categorias=[{"codigo": "a", "valor": valores[0]}, {"codigo": "b", "valor": valores[1]}]
        )
        assert _category_split(req, Decimal("100.00")) is None

    def test_percentuais_somam_exatamente_100(self) -> None:
        parts = _percentuals([Decimal("1"), Decimal("1"), Decimal("1")], Decimal("3"))
        assert parts == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
        assert sum(parts) == Decimal("100")

    def test_percentual_de_total_zero_nao_divide(self) -> None:
        assert _percentuals([Decimal(0)], Decimal(0)) == [Decimal(0)]

    def test_rateio_fecha_valor_e_percentual(self) -> None:
        req = _req(
            cod_categoria="1",
            departamentos=[
                {"codigo": "d1", "percentual": "33.33"},
                {"codigo": "d2", "percentual": "33.33"},
                {"codigo": "d3", "percentual": "33.33"},
            ],
        )
        dist = _department_split(req, Decimal("10.00"))
        assert dist is not None
        assert sum(d.n_per_dep for d in dist if d.n_per_dep is not None) == Decimal(100)
        assert sum(d.n_val_dep for d in dist if d.n_val_dep is not None) == Decimal("10.00")
        assert [d.c_cod_dep for d in dist] == ["d1", "d2", "d3"]

    def test_sem_departamento_nao_ha_distribuicao(self) -> None:
        assert _department_split(_req(cod_categoria="1"), Decimal("1")) is None


class TestTrocaDeForma:
    def test_provisao_no_fornecedor_pode_trocar(self) -> None:
        assert _kind_switch_allowed(_posting(step="fornecedor")) is True

    @pytest.mark.parametrize("step", ["titulo", "baixa"])
    def test_provisao_depois_do_fornecedor_nao_troca(self, step: str) -> None:
        assert _kind_switch_allowed(_posting(step=step, status="failed")) is False

    def test_concluida_nunca_troca(self) -> None:
        assert _kind_switch_allowed(_posting(step="fornecedor", status="confirmed")) is False

    def test_direto_recusado_uma_vez_por_fault_pode_trocar(self) -> None:
        posting = _posting(
            kind="direto",
            step=None,
            status="failed",
            attempts=1,
            error_code=ErrorCode.OMIE_FAULT.value,
        )
        assert _kind_switch_allowed(posting) is True

    @pytest.mark.parametrize(
        ("status", "attempts", "error_code"),
        [
            ("pending", 1, None),  # timeout: pode ter entrado
            ("failed", 2, ErrorCode.OMIE_FAULT.value),  # houve outro envio antes
            ("failed", 1, ErrorCode.CONFLICT.value),  # entrou e o vínculo conflitou
        ],
    )
    def test_direto_que_pode_ter_entrado_nao_troca(
        self, status: str, attempts: int, error_code: str | None
    ) -> None:
        posting = _posting(
            kind="direto", step=None, status=status, attempts=attempts, error_code=error_code
        )
        assert _kind_switch_allowed(posting) is False


class TestContagemDoLote:
    def test_parcial_e_titulo_criado_sem_concluir(self) -> None:
        tally = _ProvisionTally()
        entry = uuid4()
        tally.count(OmiePostingLineResult(file_entry_id=entry, status="lancada", payable_id=1))
        tally.count(OmiePostingLineResult(file_entry_id=entry, status="erro", payable_id=2))
        tally.count(OmiePostingLineResult(file_entry_id=entry, status="bloqueada"))
        assert (tally.linhas, tally.concluidas, tally.com_erro, tally.bloqueadas) == (3, 1, 1, 1)
        assert tally.parciais == 1
        assert tally.wrote_anything is False
        tally.titulos_criados = 1
        assert tally.wrote_anything is True


def test_mensagem_de_linha_parada_traz_a_chave_do_passo() -> None:
    entry = uuid4()
    posting = _posting(step="titulo", attempts=MAX_STEP_SENDS)
    message = _stuck_message(posting, entry)
    assert derive_payable_key(entry) in message
    assert str(MAX_STEP_SENDS) in message
