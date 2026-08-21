"""Montagem do `IncluirLancCC` — forma aninhada e valor (Sprint 7 / BACK 07.4).

⚠️ **O que estes testes provam e o que NÃO provam.** Eles provam que o ADL monta
o request **conforme o contrato corroborado**: forma aninhada (`cCodIntLanc` no
topo + `cabecalho`/`detalhes` — a forma plana foi RECUSADA pela API real em
21/08/2026), valor absoluto com 2 casas, data `dd/mm/aaaa`, uma parcela por
lançamento, e **sem campo de sinal** (`cNatureza` não existe na escrita). Eles
**não** provam os nomes internos nem a direção débito/crédito — isso é S-1, e a
única prova é a fixture de ACEITE da BACK 07.1
(`tests/unit/test_omie_fixtures.py`).

Enquanto a direção do crédito não for verificada, ESTORNO (valor positivo) é
bloqueado em `_eligibility_block` — lançá-lo no palpite poderia registrar o
estorno como uma segunda despesa. Se a fixture um dia mostrar a representação
do crédito, é o bloqueio que cai — e estes testes junto.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.integrations.omie.schemas import IncluirLancCCRequest
from app.modules.reconciliations.omie_posting.service import (
    OmiePostingService,
    _eligibility_block,
)


class _Cipher:
    """`ClientCipher` mínimo — devolve a descrição sem tocar em cripto real."""

    def __init__(self, plaintext: str | None = "COMPRA CAFETERIA DO LARGO") -> None:
        self._plaintext = plaintext

    def decrypt(self, *_args: object, **_kwargs: object) -> str:
        if self._plaintext is None:
            raise ValueError("decrypt falhou")
        return self._plaintext

    def encrypt(self, *_args: object, **_kwargs: object) -> tuple[str, str]:
        return ("ct", "iv")


def _service(cipher: _Cipher | None = None) -> OmiePostingService:
    async def factory() -> object:  # pragma: no cover  -- não usado nestes testes
        raise AssertionError("nenhum destes testes toca a Omie")

    return OmiePostingService(
        db=SimpleNamespace(),  # type: ignore[arg-type]
        settings=SimpleNamespace(OMIE_POSTING_ENABLED=True, OMIE_POSTING_MAX_BATCH=50),  # type: ignore[arg-type]
        omie_client_factory=factory,  # type: ignore[arg-type]
        cipher=cipher or _Cipher(),  # type: ignore[arg-type]
    )


def _entry(*, amount: str, day: int = 15) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        amount=Decimal(amount),
        transaction_date=date(2026, 4, day),
        description_encrypted="ct",
        description_iv="iv",
    )


def _eligibility_entry(
    *,
    amount: str,
    situation: str = "sem_omie",
    omie_lancamento_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        amount=Decimal(amount),
        situation=situation,
        omie_lancamento_id=omie_lancamento_id,
    )


def _session(*, omie_conta_id: int = 900_000_003) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), client_id=uuid4(), omie_conta_id=omie_conta_id)


def _build(service: OmiePostingService, entry: SimpleNamespace) -> IncluirLancCCRequest:
    return service._build_request(
        session=_session(),  # type: ignore[arg-type]
        entry=entry,  # type: ignore[arg-type]
        cod_categoria="2.01.03",
        cod_int_lanc="ADLXXXXXXXXXXXXXXXX",
    )


@pytest.mark.unit
class TestNestedShape:
    def test_param_is_nested_cabecalho_detalhes(self) -> None:
        """A forma plana foi RECUSADA pela API real (5001, 21/08/2026)."""
        request = _build(_service(), _entry(amount="-12.34"))
        param = request.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert set(param) == {"cCodIntLanc", "cabecalho", "detalhes"}
        assert param["cabecalho"]["nCodCC"] == 900_000_003
        assert param["detalhes"]["cCodCateg"] == "2.01.03"
        assert param["cCodIntLanc"] == "ADLXXXXXXXXXXXXXXXX"

    def test_no_sign_field_is_ever_sent(self) -> None:
        """`cNatureza` não existe no contrato de ESCRITA — a direção é a
        incógnita que o readback da captura responde (S-1)."""
        request = _build(_service(), _entry(amount="-50.00"))
        param = request.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert "cNatureza" not in json.dumps(param)

    def test_purchase_value_is_absolute(self) -> None:
        """Compra: valor negativo no extrato → `nValorLanc` ABSOLUTO."""
        request = _build(_service(), _entry(amount="-12.34"))
        assert request.cabecalho.n_valor_lanc == Decimal("12.34")
        assert request.cabecalho.n_valor_lanc > 0


@pytest.mark.unit
class TestRequestShape:
    def test_date_is_brazilian(self) -> None:
        request = _build(_service(), _entry(amount="-10.00", day=3))
        assert request.cabecalho.d_dt_lanc == "03/04/2026"

    def test_amount_has_two_decimals(self) -> None:
        """`Decimal`, nunca float (§3.4) — e quantizado em 2 casas."""
        request = _build(_service(), _entry(amount="-1234.5"))
        assert isinstance(request.cabecalho.n_valor_lanc, Decimal)
        assert str(request.cabecalho.n_valor_lanc) == "1234.50"

    def test_observation_carries_the_purchase_description(self) -> None:
        request = _build(_service(), _entry(amount="-10.00"))
        assert request.detalhes.c_obs == "COMPRA CAFETERIA DO LARGO"

    def test_ctipo_is_sent_with_the_documented_value(self) -> None:
        """A doc trata `cTipo` como essencial (a ausência é suspeita do 3102
        de 21/08/2026); o valor enviado deve ser o MESMO que a fixture de
        aceite prova."""
        request = _build(_service(), _entry(amount="-10.00"))
        param = request.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert param["detalhes"]["cTipo"] == "DIN"

    def test_valor_is_a_json_number_on_the_wire(self) -> None:
        """`nValorLanc` vai como NÚMERO JSON (doc: `decimal`, exemplo `123.46`)
        — a string do Pydantic é suspeita do 3102. Internamente segue Decimal
        (§3.4); o float existe só na borda de serialização."""
        request = _build(_service(), _entry(amount="-12.34"))
        param = request.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert isinstance(param["cabecalho"]["nValorLanc"], float)
        assert param["cabecalho"]["nValorLanc"] == 12.34

    def test_decrypt_failure_does_not_block_the_posting(self) -> None:
        """`cObs` é opcional no Omie: deixar de lançar por causa de um campo de
        texto seria pior que lançar sem observação."""
        request = _build(_service(_Cipher(plaintext=None)), _entry(amount="-10.00"))
        assert request.detalhes.c_obs is None
        assert request.cabecalho.n_valor_lanc == Decimal("10.00")


@pytest.mark.unit
class TestEstornoIsBlocked:
    """Sem representação verificada do crédito, estorno NÃO vira POST."""

    def test_refund_is_blocked_before_any_send(self) -> None:
        blocked = _eligibility_block(_eligibility_entry(amount="89.90"))  # type: ignore[arg-type]
        assert blocked is not None
        assert blocked.status == "bloqueada"
        assert blocked.reason == "estorno_nao_verificado"

    def test_purchase_is_eligible(self) -> None:
        assert _eligibility_block(_eligibility_entry(amount="-89.90")) is None  # type: ignore[arg-type]

    def test_ignored_line_wins_over_estorno(self) -> None:
        """A ordem de precedência decide qual motivo o operador lê."""
        blocked = _eligibility_block(
            _eligibility_entry(amount="89.90", situation="ignorado")  # type: ignore[arg-type]
        )
        assert blocked is not None
        assert blocked.reason == "linha_ignorada"
