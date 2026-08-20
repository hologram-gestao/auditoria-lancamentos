"""Montagem do `IncluirLancCC` — o sinal e o formato (Sprint 7 / BACK 07.4).

⚠️ **O que estes testes provam e o que NÃO provam.** Eles provam que o ADL monta
o request **conforme o contrato assumido**: compra sai `'D'`, estorno sai `'C'`,
valor absoluto com 2 casas, data `dd/mm/aaaa`, uma parcela por lançamento. Eles
**não** provam que o contrato está certo — isso é S-1, e a única prova é a
fixture real da BACK 07.1 (`tests/unit/test_omie_fixtures.py`), que hoje SKIPA.

Inverter o `cNatureza` reprova aqui. Se a fixture um dia mostrar outro esquema,
é a montagem que muda — e estes testes junto.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.reconciliations.omie_posting.service import (
    NATUREZA_CREDITO,
    NATUREZA_DEBITO,
    OmiePostingService,
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


def _session(*, omie_conta_id: int = 900_000_003) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), client_id=uuid4(), omie_conta_id=omie_conta_id)


@pytest.mark.unit
class TestSignConvention:
    def test_purchase_is_a_debit(self) -> None:
        """Compra: valor negativo no extrato → `cNatureza='D'`, valor ABSOLUTO."""
        request = _service()._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-12.34"),  # type: ignore[arg-type]
            cod_categoria="2.01.03",
            cod_int_lanc="ADLXXXXXXXXXXXXXXXX",
        )
        assert request.c_natureza == NATUREZA_DEBITO
        assert request.n_valor_lanc == Decimal("12.34")
        assert request.n_valor_lanc > 0

    def test_refund_is_a_credit(self) -> None:
        """Estorno: valor positivo → `cNatureza='C'`. Lançá-lo como débito
        registraria uma despesa que nunca existiu."""
        request = _service()._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="89.90"),  # type: ignore[arg-type]
            cod_categoria="2.01.03",
            cod_int_lanc="ADLXXXXXXXXXXXXXXXX",
        )
        assert request.c_natureza == NATUREZA_CREDITO
        assert request.n_valor_lanc == Decimal("89.90")

    def test_the_number_never_carries_the_sign(self) -> None:
        """O par (compra, estorno) do mesmo valor difere SÓ no `cNatureza`."""
        service = _service()
        compra = service._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-50.00"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        estorno = service._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="50.00"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert compra.n_valor_lanc == estorno.n_valor_lanc == Decimal("50.00")
        assert {compra.c_natureza, estorno.c_natureza} == {"D", "C"}


@pytest.mark.unit
class TestRequestShape:
    def test_date_is_brazilian(self) -> None:
        request = _service()._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-10.00", day=3),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert request.d_dt_lanc == "03/04/2026"

    def test_amount_has_two_decimals(self) -> None:
        """`Decimal`, nunca float (§3.4) — e quantizado em 2 casas."""
        request = _service()._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-1234.5"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert isinstance(request.n_valor_lanc, Decimal)
        assert str(request.n_valor_lanc) == "1234.50"

    def test_account_comes_from_the_session(self) -> None:
        request = _service()._build_request(
            session=_session(omie_conta_id=777),  # type: ignore[arg-type]
            entry=_entry(amount="-10.00"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert request.n_cod_cc == 777

    def test_observation_carries_the_purchase_description(self) -> None:
        request = _service()._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-10.00"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert request.c_obs == "COMPRA CAFETERIA DO LARGO"

    def test_ctipo_is_never_sent(self) -> None:
        """`DIN` é palpite (S-1). Um valor inventado faria a Omie recusar a
        linha inteira por causa de um campo OPCIONAL."""
        request = _service()._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-10.00"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert request.c_tipo is None
        assert "cTipo" not in request.model_dump(by_alias=True, exclude_none=True)

    def test_decrypt_failure_does_not_block_the_posting(self) -> None:
        """`cObs` é opcional no Omie: deixar de lançar por causa de um campo de
        texto seria pior que lançar sem observação."""
        request = _service(_Cipher(plaintext=None))._build_request(
            session=_session(),  # type: ignore[arg-type]
            entry=_entry(amount="-10.00"),  # type: ignore[arg-type]
            cod_categoria="X",
            cod_int_lanc="ADL1",
        )
        assert request.c_obs is None
        assert request.n_valor_lanc == Decimal("10.00")
