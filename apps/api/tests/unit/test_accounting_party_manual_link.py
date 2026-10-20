"""Vínculo manual conta contábil ↔ cliente/fornecedor (86e3tttaf), sem banco.

O que este módulo afirma:
  - `parse_typed_document` aceita CNPJ (14) e CPF (11) com ou sem máscara, pelo dígito
    verificador, e recusa o resto (tamanho, máscara errada, letra, todos iguais), sem
    passar por `int` (os zeros à esquerda ficam);
  - a recusa do campo é o 422 `CONTA_CONTABIL_INVALIDA` com `field` e `reason` fechados,
    sem o documento na mensagem;
  - o 409 do documento repetido tem o código próprio e não ecoa nada;
  - o body do PUT tem o teto do documento amarrado à constante, e recusa campo extra;
  - as props do evento `plano_contabil_vinculo_editado` só aceitam o vocabulário fechado.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import ErrorCode, PartyDocumentAlreadyLinkedError
from app.modules.client_accounting_chart.parties import parse_typed_document
from app.modules.client_accounting_chart.schemas import (
    MAX_PARTY_DOCUMENT_INPUT_CHARS,
    PartyLinkRequest,
)
from app.modules.client_accounting_chart.service import _party_link_invalid
from app.modules.usage_events.schemas import PlanoContabilVinculoEditadoProps


class TestParseTypedDocument:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("45723174000110", ("45723174000110", "cnpj")),
            ("45.723.174/0001-10", ("45723174000110", "cnpj")),
            (" 45.723.174/0001-10 ", ("45723174000110", "cnpj")),
            ("00.000.001/0001-36", ("00000001000136", "cnpj")),
            ("52998224725", ("52998224725", "cpf")),
            ("529.982.247-25", ("52998224725", "cpf")),
        ],
    )
    def test_aceita_cnpj_e_cpf_com_ou_sem_mascara(
        self, raw: str, expected: tuple[str, str]
    ) -> None:
        assert parse_typed_document(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "45.723.174/0001-19",  # CNPJ com dígito errado
            "529.982.247-26",  # CPF com dígito errado
            "11111111111",  # todos iguais
            "00000000000000",
            "457231740001",  # 12 dígitos
            "5299822472",  # 10 dígitos
            "529.982.247/25",  # barra não é da máscara do CPF
            "45a723174000110",  # letra
            "",
        ],
    )
    def test_recusa_o_resto(self, raw: str) -> None:
        assert parse_typed_document(raw) is None


class TestRecusas:
    @pytest.mark.parametrize(
        ("reason", "field"),
        [
            ("documento_invalido", "document"),
            ("conta_sintetica", "accountId"),
            ("conta_inativa", "accountId"),
        ],
    )
    def test_422_no_campo_com_motivo_fechado(self, reason: str, field: str) -> None:
        exc = _party_link_invalid(reason)  # type: ignore[arg-type]
        assert exc.code is ErrorCode.CONTA_CONTABIL_INVALIDA
        assert exc.status_code == 422
        assert exc.details == {"field": field, "reason": reason}

    def test_409_do_documento_repetido_tem_codigo_proprio(self) -> None:
        exc = PartyDocumentAlreadyLinkedError(
            "x", details={"accountCode": "685", "accountId": str(uuid4())}
        )
        assert exc.code is ErrorCode.DOCUMENTO_JA_VINCULADO
        assert exc.status_code == 409
        assert "685" not in exc.user_message


class TestSchema:
    def test_teto_do_documento_e_a_constante(self) -> None:
        field = PartyLinkRequest.model_fields["document"]
        assert any(
            getattr(m, "max_length", None) == MAX_PARTY_DOCUMENT_INPUT_CHARS for m in field.metadata
        )
        PartyLinkRequest.model_validate(
            {"partyType": "cliente", "document": "4" * MAX_PARTY_DOCUMENT_INPUT_CHARS}
        )
        with pytest.raises(ValidationError):
            PartyLinkRequest.model_validate(
                {"partyType": "cliente", "document": "4" * (MAX_PARTY_DOCUMENT_INPUT_CHARS + 1)}
            )

    def test_recusa_tipo_fora_do_vocabulario_e_campo_extra(self) -> None:
        with pytest.raises(ValidationError):
            PartyLinkRequest.model_validate({"partyType": "socio", "document": "52998224725"})
        with pytest.raises(ValidationError):
            PartyLinkRequest.model_validate(
                {"partyType": "cliente", "document": "52998224725", "clientId": str(uuid4())}
            )


class TestEvento:
    def test_props_so_aceitam_o_vocabulario_fechado(self) -> None:
        props = PlanoContabilVinculoEditadoProps(
            client_id=uuid4(), account_id=uuid4(), party_type="fornecedor", operacao="trocado"
        )
        assert set(props.model_dump()) == {"client_id", "account_id", "party_type", "operacao"}
        with pytest.raises(ValidationError):
            PlanoContabilVinculoEditadoProps(
                client_id=uuid4(), account_id=uuid4(), party_type="cliente", operacao="editado"
            )
        with pytest.raises(ValidationError):
            PlanoContabilVinculoEditadoProps(  # type: ignore[call-arg]
                client_id=uuid4(),
                account_id=uuid4(),
                party_type="cliente",
                operacao="criado",
                document="52998224725",
            )
