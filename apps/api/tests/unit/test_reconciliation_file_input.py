"""Unit — regras de entrada do multi-arquivo (Sprint 4 / BACK 04.2).

Exercita a BORDA de validação de `POST /reconciliations` e
`POST /reconciliations/{id}/files` sem tocar o banco. É aqui que se garante:

    - a forma legada (1 arquivo solto) normaliza para uma lista de 1 parte;
    - `files` e a forma legada são mutuamente exclusivos;
    - cada parte é OU extraída (`statement`) OU falha (`error_code`);
    - `error_code` sai de um enum canônico (nunca texto livre → nunca PII);
    - partes repetidas na mesma request morrem antes de tocar o banco.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.reconciliations.schemas import (
    MAX_FILES_PER_REQUEST,
    AttachFilesRequest,
    CreateReconciliationRequest,
)

_CLIENT_ID = uuid4()


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


def _statement() -> dict[str, Any]:
    return {
        "bank_name": "Itau",
        "account_type": "credit_card",
        "period_start": "2026-06-01",
        "period_end": "2026-06-10",
        "opening_balance": "0.00",
        "closing_balance": "100.00",
        "transactions": [
            {
                "date": "2026-06-02",
                "description": "Compra",
                "amount": "-50.00",
                "balance": None,
            }
        ],
    }


def _create(**overrides: Any) -> CreateReconciliationRequest:
    body: dict[str, Any] = {
        "client_id": _CLIENT_ID,
        "omie_conta_id": 42,
        "reference_month": "2026-06-01",
    }
    body.update(overrides)
    return CreateReconciliationRequest(**body)


class TestFormaLegada:
    def test_par_solto_vira_uma_parte(self) -> None:
        req = _create(file_hash=_hex64("a"), statement=_statement())
        assert len(req.files) == 1
        assert req.files[0].file_hash == _hex64("a")
        assert req.files[0].parsed_ok

    def test_hash_normalizado_para_minusculas(self) -> None:
        req = _create(file_hash=_hex64("a").upper(), statement=_statement())
        assert req.files[0].file_hash == _hex64("a")

    def test_reference_month_normalizado_para_dia_1(self) -> None:
        req = _create(reference_month="2026-06-15", file_hash=_hex64("a"), statement=_statement())
        assert req.reference_month == date(2026, 6, 1)

    def test_forma_legada_junto_com_files_e_rejeitada(self) -> None:
        with pytest.raises(ValidationError):
            _create(
                file_hash=_hex64("a"),
                statement=_statement(),
                files=[{"file_hash": _hex64("b"), "statement": _statement()}],
            )

    def test_sem_files_e_sem_par_legado_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            _create()

    def test_par_legado_incompleto_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            _create(file_hash=_hex64("a"))


class TestListaDePartes:
    def test_n_partes_aceitas(self) -> None:
        req = _create(
            files=[{"file_hash": _hex64(f"p{i}"), "statement": _statement()} for i in range(3)]
        )
        assert len(req.files) == 3

    def test_mistura_de_parte_ok_e_parte_com_falha(self) -> None:
        """O upload de 3 PDFs em que um falha continua criando a conciliação."""
        req = _create(
            files=[
                {"file_hash": _hex64("ok"), "statement": _statement()},
                {"file_hash": _hex64("ruim"), "error_code": "PARSE_ERROR"},
            ]
        )
        assert [f.parsed_ok for f in req.files] == [True, False]

    def test_todas_as_partes_com_falha_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            _create(files=[{"file_hash": _hex64("ruim"), "error_code": "PARSE_ERROR"}])

    def test_partes_repetidas_na_mesma_request_sao_rejeitadas(self) -> None:
        with pytest.raises(ValidationError):
            _create(
                files=[
                    {"file_hash": _hex64("mesmo"), "statement": _statement()},
                    {"file_hash": _hex64("mesmo"), "statement": _statement()},
                ]
            )

    def test_acima_do_teto_de_partes_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            _create(
                files=[
                    {"file_hash": _hex64(f"p{i}"), "statement": _statement()}
                    for i in range(MAX_FILES_PER_REQUEST + 1)
                ]
            )


class TestParteIndividual:
    @pytest.mark.parametrize(
        "part",
        [
            pytest.param({"file_hash": _hex64("a")}, id="sem-statement-e-sem-error"),
            pytest.param(
                {
                    "file_hash": _hex64("a"),
                    "statement": _statement(),
                    "error_code": "PARSE_ERROR",
                },
                id="statement-e-error-juntos",
            ),
            pytest.param(
                {"file_hash": _hex64("a"), "error_code": "Falha do CNPJ 12.345/0001-99"},
                id="error-code-texto-livre",
            ),
            pytest.param(
                {"file_hash": "nao-e-sha256", "statement": _statement()}, id="hash-invalido"
            ),
            pytest.param(
                {"file_hash": _hex64("a"), "statement": _statement(), "dono": "Fulano"},
                id="chave-desconhecida",
            ),
        ],
    )
    def test_parte_invalida_e_rejeitada(self, part: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            _create(files=[part])

    def test_statement_sem_transacoes_e_rejeitado(self) -> None:
        st = _statement()
        st["transactions"] = []
        with pytest.raises(ValidationError):
            _create(files=[{"file_hash": _hex64("a"), "statement": st}])

    def test_filename_acima_do_limite_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            _create(
                files=[
                    {
                        "file_hash": _hex64("a"),
                        "filename": "x" * 256,
                        "statement": _statement(),
                    }
                ]
            )


class TestAnexo:
    def test_anexo_exige_ao_menos_uma_parte(self) -> None:
        with pytest.raises(ValidationError):
            AttachFilesRequest(files=[])

    def test_anexo_aceita_so_registro_de_falha(self) -> None:
        """Diferente do create: anexar só a notícia de que uma parte falhou é
        legítimo (a sessão já tem linhas das partes anteriores)."""
        req = AttachFilesRequest(
            files=[{"file_hash": _hex64("ruim"), "error_code": "PARSE_ERROR"}]  # type: ignore[list-item]
        )
        assert req.files[0].parsed_ok is False

    def test_anexo_rejeita_partes_repetidas(self) -> None:
        with pytest.raises(ValidationError):
            AttachFilesRequest(
                files=[  # type: ignore[list-item]
                    {"file_hash": _hex64("m"), "statement": _statement()},
                    {"file_hash": _hex64("m"), "statement": _statement()},
                ]
            )
