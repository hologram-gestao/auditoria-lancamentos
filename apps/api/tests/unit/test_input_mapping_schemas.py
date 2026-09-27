"""Validação de FORMA do mapeamento de entrada na borda (Sprint 14, BACK 14.1).

Convenção de 23/09/2026 (§4.8): os validadores levantam `ValueError`, que o handler
global traduz para 400 `VALIDATION_ERROR` genérico. Aqui se prova que cada regra
de coerência do banco tem o espelho na borda — e que o mapeamento VÁLIDO de cada
convenção passa (senão o CHECK nunca seria exercitado).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.client_input_mappings.schemas import InputMappingRequest

pytestmark = pytest.mark.unit


def _csv(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fileFormat": "csv",
        "csvDelimiter": ";",
        "encoding": "utf-8",
        "dateColumn": "Data",
        "descriptionColumn": "Histórico",
        "amountColumn": "Valor",
        "categoryColumn": "Categoria",
        "dateFormat": "dd/mm/yyyy",
        "decimalSeparator": ",",
        "signConvention": "valor_com_sinal",
    }
    base.update(overrides)
    return base


class TestValidos:
    def test_valor_com_sinal(self) -> None:
        req = InputMappingRequest.model_validate(_csv())
        assert req.sign_convention.value == "valor_com_sinal"
        assert req.csv_delimiter is not None

    def test_coluna_natureza(self) -> None:
        req = InputMappingRequest.model_validate(
            _csv(
                signConvention="coluna_natureza",
                natureColumn="D/C",
                debitValue="D",
                creditValue="C",
            )
        )
        assert req.nature_column == "D/C"

    def test_colunas_separadas_sem_coluna_de_valor(self) -> None:
        req = InputMappingRequest.model_validate(
            _csv(
                signConvention="colunas_separadas",
                amountColumn=None,
                debitColumn="Débito",
                creditColumn="Crédito",
            )
        )
        assert req.amount_column is None

    def test_xlsx_sem_delimitador_nem_codificacao(self) -> None:
        req = InputMappingRequest.model_validate(
            _csv(fileFormat="xlsx", csvDelimiter=None, encoding=None)
        )
        assert req.encoding is None

    def test_classificacao_livre_apontando_a_descricao(self) -> None:
        """R4: o cliente que manda só descrição entra no de-para por ela."""
        req = InputMappingRequest.model_validate(
            _csv(categoryColumn="Histórico", categoryMode="classificacao_livre")
        )
        assert req.category_mode.value == "classificacao_livre"

    def test_sem_categoria_alguma(self) -> None:
        req = InputMappingRequest.model_validate(_csv(categoryColumn=None))
        assert req.category_column is None

    def test_nomes_de_coluna_sao_aparados(self) -> None:
        req = InputMappingRequest.model_validate(_csv(dateColumn="  Data  "))
        assert req.date_column == "Data"

    def test_aceita_snake_case_tambem(self) -> None:
        """`populate_by_name`: o serviço monta a partir de nomes internos."""
        req = InputMappingRequest.model_validate(
            {
                "file_format": "xlsx",
                "date_column": "Data",
                "description_column": "Hist",
                "amount_column": "Valor",
                "date_format": "yyyy-mm-dd",
                "decimal_separator": ".",
                "sign_convention": "valor_com_sinal",
            }
        )
        assert req.file_format.value == "xlsx"


class TestFormaInvalida:
    @pytest.mark.parametrize(
        "payload",
        [
            # convenção de sinal ausente — a regra do PRD
            {k: v for k, v in _csv().items() if k != "signConvention"},
            # convenção fora do vocabulário
            _csv(signConvention="inferir"),
            # valor_com_sinal sem coluna de valor
            _csv(amountColumn=None),
            # valor_com_sinal com campos de outra convenção
            _csv(natureColumn="D/C"),
            _csv(debitColumn="Déb", creditColumn="Créd"),
            # coluna_natureza incompleta
            _csv(signConvention="coluna_natureza", natureColumn="D/C", debitValue="D"),
            # coluna_natureza com literais iguais
            _csv(
                signConvention="coluna_natureza",
                natureColumn="D/C",
                debitValue="X",
                creditValue="X",
            ),
            # colunas_separadas com coluna de valor
            _csv(signConvention="colunas_separadas", debitColumn="Déb", creditColumn="Créd"),
            # colunas_separadas com as duas colunas iguais
            _csv(
                signConvention="colunas_separadas",
                amountColumn=None,
                debitColumn="Valor",
                creditColumn="Valor",
            ),
            # csv sem delimitador / sem codificação
            _csv(csvDelimiter=None),
            _csv(encoding=None),
            # xlsx com delimitador
            _csv(fileFormat="xlsx", encoding=None),
            # delimitador/codificação/data/decimal fora do vocabulário
            _csv(csvDelimiter="\t"),
            _csv(encoding="utf-16"),
            _csv(dateFormat="%d/%m/%Y"),
            _csv(decimalSeparator=";"),
            # PDF não tem coluna para mapear
            _csv(fileFormat="pdf", csvDelimiter=None, encoding=None),
            # classificacao_livre sem coluna
            _csv(categoryColumn=None, categoryMode="classificacao_livre"),
            # coluna vazia / só espaços
            _csv(dateColumn="   "),
            _csv(descriptionColumn=""),
            # campo desconhecido (client_id no body nunca decide tenant)
            _csv(client_id="00000000-0000-0000-0000-000000000000"),
        ],
        ids=[
            "sem_sinal",
            "sinal_fora_do_vocabulario",
            "valor_com_sinal_sem_valor",
            "valor_com_sinal_com_natureza",
            "valor_com_sinal_com_colunas_separadas",
            "natureza_incompleta",
            "natureza_literais_iguais",
            "separadas_com_valor",
            "separadas_colunas_iguais",
            "csv_sem_delimitador",
            "csv_sem_codificacao",
            "xlsx_com_delimitador",
            "delimitador_tab",
            "codificacao_fora",
            "data_strftime_livre",
            "decimal_fora",
            "pdf",
            "livre_sem_coluna",
            "coluna_so_espacos",
            "coluna_vazia",
            "campo_desconhecido",
        ],
    )
    def test_recusa(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            InputMappingRequest.model_validate(payload)
