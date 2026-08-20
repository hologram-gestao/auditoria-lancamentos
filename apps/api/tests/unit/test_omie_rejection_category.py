"""`faultstring` → família, sem levar o texto (Sprint 7 / BACK 07.5).

O que estes testes travam: a classificação **nunca devolve trecho do texto**, e
o conjunto de saídas é fechado. É o que permite instrumentar "por que a Omie
está recusando" sem abrir uma porta de texto livre no sink de métrica — que é a
única defesa contra PII entrar lá (a Omie ecoa no `faultstring` valores que
enviamos, inclusive o `cObs`, que carrega a descrição da compra).
"""

from __future__ import annotations

import typing

import pytest

from app.modules.usage_events.omie_rejection import classify_omie_rejection
from app.modules.usage_events.schemas import OmieRejectionCategory

_ALLOWED = set(typing.get_args(OmieRejectionCategory))


@pytest.mark.unit
class TestClassification:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("Categoria [9.99] não cadastrada para este cliente.", "categoria_invalida"),
            ("Conta corrente informada não existe.", "conta_invalida"),
            ("nCodCC inválido.", "conta_invalida"),
            ("Já existe lançamento com este código de integração.", "duplicidade"),
            ("Registro duplicado.", "duplicidade"),
            ("app_key inválida.", "credencial"),
            ("Acesso negado ao recurso.", "credencial"),
            ("Tag não faz parte da estrutura do tipo complexo.", "campo_invalido"),
            ("Campo obrigatório não informado.", "campo_invalido"),
            ("Consumo redundante detectado. Aguarde 58 segundos.", "indisponibilidade"),
            ("Erro interno do servidor XPTO-42.", "outro"),
        ],
    )
    def test_families(self, message: str, expected: str) -> None:
        assert classify_omie_rejection(message) == expected

    def test_accents_and_case_do_not_matter(self) -> None:
        """A Omie mistura acentuação e caixa entre mensagens."""
        assert classify_omie_rejection("CATEGORIA INVÁLIDA") == "categoria_invalida"
        assert classify_omie_rejection("categoria invalida") == "categoria_invalida"

    @pytest.mark.parametrize("message", [None, "", "   "])
    def test_missing_message_is_not_a_crash(self, message: str | None) -> None:
        """A Omie devolve erro sem mensagem — isso é `outro`, não exceção."""
        assert classify_omie_rejection(message) == "outro"


@pytest.mark.unit
class TestNoFreeTextEscapes:
    @pytest.mark.parametrize(
        "message",
        [
            "Categoria não cadastrada para PADARIA PAO QUENTE LTDA (12.345.678/0001-90).",
            "Erro no lançamento 'COMPRA CAFETERIA DO LARGO - JOAO DA SILVA'.",
            "Conta corrente de FULANA PARTICIPACOES não encontrada.",
            "faultstring com CPF 123.456.789-00 no meio",
        ],
    )
    def test_pii_in_the_provider_message_never_reaches_the_output(self, message: str) -> None:
        """O ponto inteiro do módulo: entra texto com PII, sai um enum.

        Se algum dia alguém "melhorar" a função devolvendo o trecho que casou,
        este teste reprova — e é ele que impede a regressão que reabriria a
        porta de PII no sink.
        """
        result = classify_omie_rejection(message)
        assert result in _ALLOWED
        for fragment in ("PADARIA", "CAFETERIA", "SILVA", "FULANA", "12.345", "123.456"):
            assert fragment.lower() not in result.lower()

    def test_output_is_always_from_the_closed_set(self) -> None:
        for message in ("", "qualquer coisa", "categoria", "duplicado", None):
            assert classify_omie_rejection(message) in _ALLOWED
