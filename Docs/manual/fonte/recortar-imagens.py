"""Gera as figuras do manual a partir de `screenshots/`.

Recorta a barra superior (que traz o nome antigo do produto e a marca) e apara
o vazio do rodapé. Rodar da raiz do repositório:

    uv run --with pillow python Docs/manual/fonte/recortar-imagens.py
"""

import os
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parents[3]
# `screenshots/` não é versionado; num worktree, aponte SHOTS_DIR para a do checkout principal.
SRC = Path(os.environ.get("SHOTS_DIR", RAIZ / "screenshots"))
OUT = Path(__file__).resolve().parent / "img"
OUT.mkdir(parents=True, exist_ok=True)

# nome de saída -> (caminho em screenshots/, topo, esquerda, apara embaixo[, base[, direita]])
# Desde a 1.2 (86e3mz74x) as figuras são do tema Hologram, em DPR 2 (2880 px de largura),
# então os cortes são o dobro dos pixels CSS: a barra superior tem 64 px CSS = 128 aqui, e o
# menu lateral 223 px CSS = 446. `mock/` vem do e2e com a API interceptada
# (`apps/web/e2e/a11y-mocked.spec.ts`, cliente fictício "Cliente Exemplo Ltda"); `app/` vem da
# tela real com os dados de `capturas/dados_demo.py` (roteiro no README). `base`, quando há, é
# a última linha da figura; `direita`, a última coluna (gaveta e diálogo recortados sozinhos).
# Na 1.3 (86e3ndj0p) sete figuras saíram do e2e de novo (`NOVO`), quatro delas novas; as
# demais continuam as da 1.2. Na 1.4 (86e3qhhtj) entraram seis figuras dos prints das
# subtasks do épico 86e3qhhr0 e do selo "No Omie" (86e3t4vpq): são do mesmo e2e, mas em DPR
# 2,75 (3960 px de largura), então ali a barra superior tem 176 px e o menu lateral 614. O
# `plano-contabil` foi recapturado em DPR 2 pelo mesmo cenário de antes, com as abas novas.
PASTA = "pr-shots-86e3mz74x"
MOCK = f"{PASTA}/mock"
APP = f"{PASTA}/app"
NOVO = "pr-shots-86e3ndj0p/mock"
FORNECEDOR = "pr-shots-86e3qhhrd"
VINCULOS = "pr-shots-86e3tttaf"
PLANILHA_OMIE = "pr-shots-86e3qhhrr"
GUIA_OMIE = "pr-shots-86e3qhht4"
PARCELAS = "pr-shots-86e3qhhtb"
SITUACAO_OMIE = "pr-shots-86e3t4vpq"
Job = (
    tuple[str, int, int, bool]
    | tuple[str, int, int, bool, int | None]
    | tuple[str, int, int, bool, int | None, int]
)
JOBS: dict[str, Job] = {
    "clientes": (f"{MOCK}/lista-clientes-acoes-desktop.png", 128, 0, True),
    # O diálogo de editar sozinho: processo do cartão, rateio e fornecedor padrão.
    "editar-cliente": (f"{NOVO}/pr-editar-cliente-cartao-desktop.png", 32, 992, False, 1768, 1888),
    # Só o fechamento do mês e a carteira: o fluxo e a atividade ficam abaixo, fora da figura.
    "painel": (f"{APP}/painel.png", 128, 0, False, 2090),
    # A gaveta de criação sozinha, no passo 2, com o bloco "Fatura do cartão".
    "fatura-cartao": (f"{NOVO}/pr-gaveta-fatura-vencimento-desktop.png", 0, 1856, False),
    "conciliacao": (f"{NOVO}/detalhe-conciliacao-desktop.png", 128, 0, False),
    "anomalias": (f"{MOCK}/revisao-veredito-desktop.png", 860, 446, True),
    # As linhas da revisão com o selo que vem do servidor (provisionado, parado).
    "selos": (f"{NOVO}/pr-revisao-selo-servidor-desktop.png", 700, 446, True),
    # A gaveta "Lançar no Omie" sozinha: provisão com duas categorias, fornecedor e rateio.
    "lancamento": (f"{NOVO}/pr-gaveta-provisao-completa-desktop.png", 0, 1536, False),
    # Daqui até "guia-omie", DPR 2,75. A gaveta sozinha, até a lista de cadastros achados.
    "fornecedor-nome": (f"{FORNECEDOR}/pr-gaveta-fornecedor-por-nome-desktop.png", 0, 2112, False, 1795),
    # A gaveta sozinha, até a lista das parcelas futuras (o rodapé fica fora).
    "parcelas": (f"{PARCELAS}/pr-gaveta-parcelas-futuras-desktop.png", 0, 2112, False, 1735),
    # Sem o menu: do bloco explicativo até a linha com o selo e o tooltip.
    "situacao-omie": (f"{SITUACAO_OMIE}/revisao-situacao-no-omie-desktop.png", 1285, 614, False, 2295),
    # As duas listas da aba "Clientes e fornecedores", com o menu, até a paginação.
    "vinculos": (f"{VINCULOS}/plano-contabil-vinculos-desktop.png", 176, 0, False, 2310),
    # O diálogo da planilha da Omie sozinho.
    "planilha-omie": (f"{PLANILHA_OMIE}/plano-contabil-planilha-omie-pronta-desktop.png", 605, 1185, False, 1870, 2775),
    # O diálogo do guia das categorias sozinho.
    "guia-omie": (f"{GUIA_OMIE}/de-para-conta-contabil-guia-omie-desktop.png", 440, 1270, False, 2035, 2690),
    "plano-de-contas": (f"{MOCK}/plano-de-contas-filtro-ativo-desktop.png", 128, 0, True),
    "plano-contabil": (f"{NOVO}/plano-contabil-conta-manual-lista-desktop.png", 128, 0, True),
    "de-para": (f"{NOVO}/de-para-filtro-ativo-desktop.png", 128, 0, True),
    "destinos-de-para": (f"{NOVO}/destinos-de-para-desktop.png", 128, 0, True),
    "previa": (f"{APP}/previa.png", 128, 0, False),
    "arquivo-contabil": (f"{APP}/arquivo-contabil.png", 0, 0, True),
    "carteira": (f"{MOCK}/carteira-linha-unica-desktop.png", 128, 0, True),
    "recebiveis": (f"{APP}/recebiveis.png", 128, 0, True),
    # A gaveta cobre a barra (escurecida) e o título dela começa no alto: sem corte.
    "mapeamento": (f"{APP}/mapeamento.png", 0, 0, False),
    # Só a lista (um layout, com "Excluir"); o vazio abaixo dela fica fora.
    "layouts": (f"{NOVO}/layouts-exportacao-lista-desktop.png", 128, 0, False, 860),
}

MAX_W = 2000


def trim_bottom(im: Image.Image) -> Image.Image:
    """Corta as linhas finais que são só fundo, deixando uma folga."""
    w, h = im.size
    px = im.convert("RGB").load()
    bg = px[w - 8, h - 8]
    tol = 10
    last = h - 1
    while last > 100:
        uniforme = all(
            all(abs(px[x, last][c] - bg[c]) <= tol for c in range(3))
            for x in range(0, w, max(1, w // 120))
        )
        if not uniforme:
            break
        last -= 1
    return im.crop((0, 0, w, min(h, last + int(h * 0.02))))


for nome, (rel, topo, esquerda, apara, *resto) in JOBS.items():
    base = resto[0] if resto else None
    direita = resto[1] if len(resto) > 1 else None
    if not (SRC / rel).exists():
        # `screenshots/` não é versionado: um worktree novo só tem as capturas que ele mesmo tirou.
        print(f"{nome:18} fonte ausente ({rel}); figura atual mantida")
        continue
    im = Image.open(SRC / rel).convert("RGB")
    w, h = im.size
    im = im.crop((esquerda, topo, direita or w, base or h))
    if apara:
        im = trim_bottom(im)
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    # Paleta de 256 cores sem pontilhado: o PDF (que a landing oferece para baixar) fica perto
    # do tamanho da 1.1. MAXCOVERAGE e não MEDIANCUT: o MEDIANCUT funde o âmbar de "atenção"
    # no vermelho (medido no painel); este erra no máximo 9/255 por canal nas 14 figuras.
    im = im.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE)
    im.save(OUT / f"{nome}.png", optimize=True)
    print(f"{nome:18} {im.size[0]}x{im.size[1]}")
