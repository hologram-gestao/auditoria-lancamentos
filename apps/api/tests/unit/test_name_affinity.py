"""Testes da afinidade de nome usada como desempate no matcher.

Função pura, determinística e sem IA (CLAUDE.md §5.9). O que importa provar:
    - Reconhece o nome apesar do ruído de layout do extrato.
    - Normaliza acento, caixa e pontuação.
    - Conectivos e sufixos societários não contam como evidência.
    - Ausência de sinal devolve 0 — que o caller lê como "não sei", nunca
      como "não é".
"""

from __future__ import annotations

import pytest

from app.modules.reconciliations.processing.name_affinity import supplier_affinity


@pytest.mark.unit
class TestSupplierAffinity:
    def test_nome_completo_no_meio_do_ruido_do_extrato(self) -> None:
        """O caso do report da Bruna, com o texto de Pix como ele vem."""
        assert (
            supplier_affinity(
                "Maiane Medrado Silva",
                "Pix enviado: Cp:18236120-Maiane Medrado Silva",
            )
            == 3
        )

    def test_fornecedor_de_outra_pessoa_nao_tem_afinidade(self) -> None:
        assert (
            supplier_affinity(
                "Cleidson Quiteria de Souza",
                "Pix enviado: Cp:18236120-Maiane Medrado Silva",
            )
            == 0
        )

    def test_nome_truncado_no_extrato_ainda_pontua(self) -> None:
        """Extrato bancário corta texto — parcial precisa continuar valendo."""
        assert (
            supplier_affinity(
                "Cleidson Quiteria de Souza",
                "TED: CLEIDSON QUITERIA DE S",
            )
            == 2
        )

    def test_ignora_acento_e_caixa(self) -> None:
        assert supplier_affinity("José António Conceição", "pgto jose antonio conceicao") == 3

    def test_conectivos_e_sufixos_nao_contam(self) -> None:
        """Duas empresas diferentes não podem empatar por 'DE' e 'LTDA'."""
        assert supplier_affinity("Comercio de Pecas LTDA", "PAGAMENTO DE ALIMENTOS LTDA") == 0

    def test_token_curto_nao_conta(self) -> None:
        """Tokens de 1-2 letras coincidem por acaso com facilidade demais."""
        assert supplier_affinity("A B C", "PIX A B C QUALQUER COISA") == 0

    def test_sem_fornecedor_devolve_zero(self) -> None:
        """Títulos a pagar/receber não trazem nome — só o código do cliente."""
        assert supplier_affinity(None, "Pix enviado: Maiane Medrado Silva") == 0

    def test_descricao_vazia_devolve_zero(self) -> None:
        assert supplier_affinity("Maiane Medrado Silva", "") == 0

    def test_e_deterministica(self) -> None:
        """Mesma entrada, mesma saída — o matcher depende disso (§5.9)."""
        args = ("Maiane Medrado Silva", "Pix Cp:1-Maiane Medrado Silva")
        assert len({supplier_affinity(*args) for _ in range(50)}) == 1
