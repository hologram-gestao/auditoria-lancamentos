"""Unit — renderização do bloco de glossário da qualificação (Sprint 6, BACK 06.4).

Sem DB e sem Anthropic: exercita a função pura que monta o texto. É aqui que
moram as propriedades de que o CACHE depende (determinismo, ordem fixa,
ausência de valor volátil) e o caso negativo do TETO de tokens.

Também guarda o limite duro da task: a EXTRAÇÃO (`app/integrations/anthropic/`)
não pode aprender nada sobre glossário — `grep` no diretório é a asserção.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.db.models import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    GlossaryEntryKind,
)
from app.modules.glossary.schemas import (
    UNDECIPHERABLE,
    GlossaryEntryPlain,
    GlossarySnapshot,
)
from app.modules.reconciliations.qualification.semantic import (
    GLOSSARY_BLOCK_MAX_CHARS,
    render_glossary_block,
)

pytestmark = pytest.mark.unit

CLIENT_A = UUID("3f7b1e2a-0000-4000-8000-0000000000a1")


def _entry(
    kind: GlossaryEntryKind,
    name: str,
    *,
    code: str | None = None,
    description: str | None = None,
    decrypt_failed: bool = False,
) -> GlossaryEntryPlain:
    return GlossaryEntryPlain(
        id=uuid4(),
        kind=kind,
        code=code,
        name=name,
        description=description,
        decrypt_failed=decrypt_failed,
    )


def _snapshot(*entries: GlossaryEntryPlain, version: int = 1) -> GlossarySnapshot:
    return GlossarySnapshot(client_id=CLIENT_A, version=version, entries=tuple(entries))


class TestRenderizacao:
    def test_as_tres_formas_aparecem_com_seus_rotulos(self) -> None:
        block = render_glossary_block(
            _snapshot(
                _entry(
                    GlossaryEntryKind.CATEGORIA,
                    "Taxas bancárias",
                    code="3.1.02",
                    description="Tarifas do banco, nunca juros.",
                ),
                _entry(GlossaryEntryKind.FORNECEDOR, "Moinho Prado Ltda"),
                _entry(GlossaryEntryKind.REGRA, "IOF nunca é juros."),
            )
        )

        assert block is not None
        assert "3.1.02 — Taxas bancárias: Tarifas do banco, nunca juros." in block
        assert "- Moinho Prado Ltda" in block
        assert "- IOF nunca é juros." in block
        assert "Categorias contábeis" in block
        assert "Fornecedores típicos" in block
        assert "Regras de auditoria do cliente" in block

    def test_campos_ausentes_nao_deixam_separador_orfao(self) -> None:
        block = render_glossary_block(_snapshot(_entry(GlossaryEntryKind.FORNECEDOR, "ACME")))

        assert block is not None
        assert "- ACME\n" in block
        assert "— ACME" not in block
        assert "ACME:" not in block

    def test_secao_vazia_nao_e_renderizada(self) -> None:
        block = render_glossary_block(_snapshot(_entry(GlossaryEntryKind.REGRA, "R1")))

        assert block is not None
        assert "Categorias contábeis" not in block
        assert "Fornecedores típicos" not in block

    def test_glossario_vazio_nao_gera_bloco(self) -> None:
        """Cliente sem glossário: nenhum bloco extra — sem regressão."""
        assert render_glossary_block(_snapshot()) is None

    def test_bloco_avisa_que_e_contexto_e_nao_revoga_as_regras(self) -> None:
        """Sem isso, o bloco vira 'marque tudo ok' e a qualificação perde valor."""
        block = render_glossary_block(_snapshot(_entry(GlossaryEntryKind.REGRA, "R1")))

        assert block is not None
        assert "NÃO revoga as regras acima" in block


class TestDeterminismo:
    def test_mesmo_conteudo_gera_string_identica(self) -> None:
        """Condição do cache-hit: nada de timestamp, contador ou id no texto."""
        entries = (
            _entry(GlossaryEntryKind.CATEGORIA, "Taxas", code="3.1"),
            _entry(GlossaryEntryKind.REGRA, "IOF nunca é juros."),
        )

        primeira = render_glossary_block(_snapshot(*entries))
        segunda = render_glossary_block(_snapshot(*entries))

        assert primeira == segunda

    def test_versao_do_snapshot_nao_entra_no_texto(self) -> None:
        """A versão invalida o cache pelo CONTEÚDO das entradas, não por si só.

        Se o número da versão fosse impresso no bloco, qualquer escrita em
        QUALQUER entrada invalidaria o prefixo inteiro — inclusive quando o
        texto renderizado não mudou.
        """
        entry = _entry(GlossaryEntryKind.REGRA, "IOF nunca é juros.")

        assert render_glossary_block(_snapshot(entry, version=1)) == render_glossary_block(
            _snapshot(entry, version=99)
        )

    def test_ordem_das_secoes_e_fixa_independente_da_ordem_de_entrada(self) -> None:
        regra = _entry(GlossaryEntryKind.REGRA, "R")
        categoria = _entry(GlossaryEntryKind.CATEGORIA, "C")
        fornecedor = _entry(GlossaryEntryKind.FORNECEDOR, "F")

        um = render_glossary_block(_snapshot(regra, categoria, fornecedor))
        outro = render_glossary_block(_snapshot(categoria, fornecedor, regra))

        assert um == outro
        assert um is not None
        assert um.index("\n- C") < um.index("\n- F") < um.index("\n- R")


class TestEntradaIndecifravel:
    def test_entrada_indecifravel_e_omitida_do_bloco(self) -> None:
        """Injetar `[indecifrável]` como vocabulário é pior que omitir."""
        block = render_glossary_block(
            _snapshot(
                _entry(GlossaryEntryKind.REGRA, "IOF nunca é juros."),
                _entry(GlossaryEntryKind.REGRA, UNDECIPHERABLE, decrypt_failed=True),
            )
        )

        assert block is not None
        assert UNDECIPHERABLE not in block
        assert "IOF nunca é juros." in block

    def test_glossario_inteiro_indecifravel_nao_gera_bloco(self) -> None:
        block = render_glossary_block(
            _snapshot(_entry(GlossaryEntryKind.REGRA, UNDECIPHERABLE, decrypt_failed=True))
        )

        assert block is None


class TestTetoDeTamanho:
    def test_teto_bate_com_os_limites_validados_na_06_3(self) -> None:
        """ "O mesmo que a 06.4 assume como teto" — não são dois números."""
        assert GLOSSARY_BLOCK_MAX_CHARS > MAX_NAME_CHARS + MAX_DESCRIPTION_CHARS
        # Um glossário DENTRO dos limites da 06.3 nunca deve ser truncado.
        dentro = [
            _entry(
                GlossaryEntryKind.REGRA,
                "n" * MAX_NAME_CHARS,
                description="d" * MAX_DESCRIPTION_CHARS,
            )
            for _ in range(50)
        ]
        block = render_glossary_block(_snapshot(*dentro))
        assert block is not None
        assert "truncado" not in block

    def test_glossario_gigante_e_truncado_com_aviso(self) -> None:
        """O prompt NUNCA cresce sem teto — caso negativo do PRD."""
        gigantes = [
            _entry(
                GlossaryEntryKind.REGRA,
                f"regra {i}",
                description="d" * MAX_DESCRIPTION_CHARS,
            )
            for i in range(MAX_NAME_CHARS * 30)
        ]

        block = render_glossary_block(_snapshot(*gigantes))

        assert block is not None
        assert len(block) <= GLOSSARY_BLOCK_MAX_CHARS
        assert "glossário truncado por tamanho" in block

    def test_truncamento_e_deterministico(self) -> None:
        """Truncar num ponto instável quebraria o cache justamente do maior bloco."""
        gigantes = tuple(
            _entry(
                GlossaryEntryKind.REGRA,
                f"regra {i}",
                description="d" * MAX_DESCRIPTION_CHARS,
            )
            for i in range(MAX_NAME_CHARS * 30)
        )

        assert render_glossary_block(_snapshot(*gigantes)) == render_glossary_block(
            _snapshot(*gigantes)
        )

    def test_corte_nao_parte_uma_entrada_ao_meio(self) -> None:
        gigantes = [
            _entry(GlossaryEntryKind.REGRA, f"regra {i}", description="d" * 400)
            for i in range(MAX_NAME_CHARS * 30)
        ]

        block = render_glossary_block(_snapshot(*gigantes))

        assert block is not None
        corpo = block.split("[glossário truncado")[0]
        # Toda linha de entrada preservada termina no fim da própria descrição.
        assert corpo.endswith("\n")


class TestExtracaoIntocada:
    """Limite duro da task: a EXTRAÇÃO não aprende nada sobre glossário."""

    def test_nenhuma_mencao_a_glossario_na_extracao(self) -> None:
        extracao = Path(__file__).resolve().parents[2] / "app" / "integrations" / "anthropic"
        for arquivo in ("client.py", "prompts.py"):
            conteudo = (extracao / arquivo).read_text(encoding="utf-8").lower()
            assert "glossar" not in conteudo, (
                f"{arquivo} menciona glossário — a injeção é na QUALIFICAÇÃO "
                "(`qualification/semantic.py`), a extração fica intocada."
            )
