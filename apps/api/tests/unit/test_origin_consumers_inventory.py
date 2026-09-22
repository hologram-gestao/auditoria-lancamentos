"""O inventário de consumidores de origem é gerado, não escrito (S9, BACK 09.6 — R6).

Três coisas ficam travadas aqui:

    1. **todo call site está classificado** — arquivo novo tocando os símbolos
       rastreados e ausente da tabela reprova, com a mensagem apontando o R6;
    2. **o markdown versionado está em dia** — regenera e compara, para o doc
       não virar ficção;
    3. **`glossary/service.py` é CIFRA DE DADO, não origem** — a interpretação
       registrada em ADR-050-BE, travada em teste para não se perder.

E dois testes protegem o próprio varredor (mutação + entrada morta): um gate que
passa por não olhar nada é pior que gate nenhum.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.gen_origin_consumers_inventory import (
    CLASSIFICATION,
    LEGACY_SYMBOLS,
    OUTPUT,
    TRACKED_SYMBOLS,
    Family,
    render,
    scan,
    stale,
    unclassified,
)

_API_ROOT = Path(__file__).resolve().parents[2]
_BY_PATH = {entry.path: entry for entry in CLASSIFICATION}


class TestInventario:
    def test_todo_call_site_esta_classificado(self) -> None:
        missing = unclassified(scan())
        assert not missing, (
            f"Sprint 9 / R6: call site(s) de origem sem classificação: {missing}.\n"
            "Acrescente cada um em `CLASSIFICATION` (scripts/gen_origin_consumers_inventory.py) "
            "dizendo se é ORIGEM (converte para `client_connections.origin`), CIFRA_DADO "
            "(cifra dado do tenant e NÃO vira dependência de conexão), DEFINICAO ou FALLBACK."
        )

    def test_a_tabela_nao_tem_entrada_morta(self) -> None:
        """Arquivo que parou de tocar os símbolos sai da tabela — senão ela
        deixa de descrever o código e vira história."""
        assert not stale(scan())

    def test_o_markdown_versionado_esta_em_dia(self) -> None:
        assert OUTPUT.exists(), (
            "Rode `uv run python scripts/gen_origin_consumers_inventory.py` e commite o doc."
        )
        assert OUTPUT.read_text(encoding="utf-8") == render(scan()), (
            "docs/origin-consumers-sprint9.md está desatualizado. Regenere com "
            "`uv run python scripts/gen_origin_consumers_inventory.py`."
        )


class TestZeroFabricaForaDaPorta:
    def test_o_factory_antigo_nao_existe_mais(self) -> None:
        """`build_omie_client` ficou sem chamador na 09.6 e o módulo foi removido.

        Se ele voltar, alguém recriou o caminho que assume "cliente ⇒ Omie".
        """
        assert not (_API_ROOT / "app" / "modules" / "clients" / "omie_factory.py").exists()

    def test_so_a_porta_cita_o_factory_antigo(self) -> None:
        """Quem constrói client do provedor hoje é `origin.py`, via adaptador.

        As menções que sobram são docstrings explicando o que MUDOU; a da
        própria porta é a que contrasta o antes e o depois. Qualquer OUTRO
        arquivo de família ORIGEM citando o símbolo antigo é código, não texto.
        """
        chamadores = [path for path, symbols in scan().items() if "build_omie_client" in symbols]
        for path in chamadores:
            if path == "app/modules/client_connections/origin.py":
                continue
            assert _BY_PATH[path].family is not Family.ORIGEM, (
                f"{path} constrói client do provedor fora de `client_connections.origin`"
            )


class TestGlossarioNaoEOrigem:
    def test_classificado_como_cifra_de_dado(self) -> None:
        """A interpretação do planejador, travada (ADR-050-BE).

        Converter o glossário em dependência de origem faria o vocabulário
        contábil de um cliente SEM Omie parar de funcionar — o oposto do que a
        sprint quer.
        """
        entry = _BY_PATH["app/modules/glossary/service.py"]
        assert entry.family is Family.CIFRA_DADO

    def test_o_glossario_nao_fala_com_a_origem(self) -> None:
        """Não é fé na classificação: o arquivo não cita símbolo de origem."""
        text = (_API_ROOT / "app" / "modules" / "glossary" / "service.py").read_text(
            encoding="utf-8"
        )
        for symbol in TRACKED_SYMBOLS:
            if symbol in LEGACY_SYMBOLS:
                continue  # cifra de dado usa os helpers de cipher, e pode
            assert symbol not in text, f"glossary/service.py passou a usar {symbol}"


class TestOVarredorFunciona:
    def test_pega_arquivo_novo(self, tmp_path: Path) -> None:
        """Mutação: uma árvore com um arquivo não classificado precisa reprovar."""
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "consumidor_novo.py").write_text(
            "x = await build_capable_client(db, client, cap, settings=s)\n", encoding="utf-8"
        )
        found = scan(root=tmp_path)
        assert "app/consumidor_novo.py" in found
        assert unclassified(found) == ["app/consumidor_novo.py"]

    def test_ignora_arquivo_sem_os_simbolos(self, tmp_path: Path) -> None:
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "limpo.py").write_text("x = 1\n", encoding="utf-8")
        assert scan(root=tmp_path) == {}

    @pytest.mark.parametrize("entry", CLASSIFICATION, ids=lambda e: e.path)
    def test_toda_entrada_tem_nota_nao_vazia(self, entry: object) -> None:
        assert entry.note.strip()  # type: ignore[attr-defined]
