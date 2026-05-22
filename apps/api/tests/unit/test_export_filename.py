"""Sanitização do nome de arquivo do export Excel (S14 BACK 10.1)."""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.reconciliations.export.service import build_filename


@pytest.mark.unit
class TestBuildFilename:
    def test_simple_ascii_names(self) -> None:
        name = build_filename(
            client_name="Cliente Teste",
            account_name="Sicredi 91263-1",
            reference_month=date(2026, 4, 1),
        )
        assert name == "Conciliacao_Cliente_Teste_Sicredi_91263-1_04-2026"

    def test_strips_accents(self) -> None:
        name = build_filename(
            client_name="Padaria São João Ltda.",
            account_name="Conta Corrente Itaú",
            reference_month=date(2026, 12, 1),
        )
        # Acentos viram ASCII; ponto se preserva (não é caractere inválido NTFS).
        assert "Sao_Joao" in name
        assert "Itau" in name
        assert name.endswith("_12-2026")

    @pytest.mark.parametrize(
        "client_name",
        [
            "Cli/ent/e",
            "Cli\\ent\\e",
            'Cli"ent"e',
            "Cli:ent:e",
            "Cli*ent*e",
            "Cli?ent?e",
            "Cli<ent>e",
            "Cli|ent|e",
        ],
    )
    def test_replaces_invalid_filename_chars(self, client_name: str) -> None:
        """Todos os 9 caracteres inválidos para NTFS viram underscore."""
        name = build_filename(
            client_name=client_name,
            account_name="Conta",
            reference_month=date(2026, 1, 1),
        )
        for forbidden in r'\/:*?"<>|':
            assert forbidden not in name

    def test_collapses_whitespace(self) -> None:
        name = build_filename(
            client_name="A    B   C",
            account_name="Conta\tTeste",
            reference_month=date(2026, 6, 1),
        )
        # Whitespace → underscore; sem underscores duplicados resultantes da
        # interação dos espaços com a substituição.
        assert "__" not in name.split("Conciliacao_", 1)[1]
        assert "A_B_C" in name

    def test_strips_control_chars(self) -> None:
        name = build_filename(
            client_name="Cliente\x00Teste\x1f",
            account_name="Conta",
            reference_month=date(2026, 1, 1),
        )
        assert "\x00" not in name
        assert "\x1f" not in name

    def test_month_padded(self) -> None:
        """Janeiro vira "01-..." (zero-padded), não "1-...".  Dezembro fica "12-".."""
        jan = build_filename(client_name="X", account_name="Y", reference_month=date(2026, 1, 1))
        dez = build_filename(client_name="X", account_name="Y", reference_month=date(2026, 12, 1))
        assert jan.endswith("_01-2026")
        assert dez.endswith("_12-2026")

    def test_empty_part_falls_back(self) -> None:
        """Se a sanitização zerar um pedaço (ex: nome com só caracteres
        inválidos), usa-se 'sem_nome' como fallback para não gerar
        nome do tipo "Conciliacao__Conta_04-2026" com underscore duplo."""
        name = build_filename(
            client_name='/:*"',
            account_name="Conta",
            reference_month=date(2026, 4, 1),
        )
        # Não deve haver underscores duplicados após "Conciliacao".
        assert "__" not in name
        assert "sem_nome" in name
