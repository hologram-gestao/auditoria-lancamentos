"""Gera as figuras do manual a partir de `screenshots/`.

Recorta a barra superior (que traz o nome antigo do produto e a marca) e apara
o vazio do rodapé. Rodar da raiz do repositório:

    uv run --with pillow python Docs/manual/fonte/recortar-imagens.py
"""

from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parents[3]
SRC = RAIZ / "screenshots"
OUT = Path(__file__).resolve().parent / "img"
OUT.mkdir(parents=True, exist_ok=True)

# nome de saída -> (caminho em screenshots/, corte no topo, corte à esquerda, apara embaixo)
# As sete de `pr-shots-86e3gqfmj/` foram recapturadas na v1.1 com dados de demonstração limpos
# (banco local isolado, nomes fictícios desde a criação; roteiro no README).
NOVAS = "pr-shots-86e3gqfmj"
JOBS = {
    "clientes": ("pr-shots-86e3fr9pd/lista-clientes-acoes-desktop.png", 176, 0, True),
    "painel": (f"{NOVAS}/painel.png", 64, 0, True),
    "conciliacao": ("pr-shots-86e3fr9pd/detalhe-conciliacao-desktop.png", 176, 0, False),
    "anomalias": ("pr-shots-86e2n39hb/dark-revisao-veredito-desktop.png", 1290, 614, True),
    "lancamento": ("pr-shots-86e2n39hb/dark-lancamento-selecao-desktop.png", 990, 614, False),
    "plano-de-contas": (
        "pr-shots-listas-carteira/1-plano-de-contas-filtro-ativo-desktop.png", 176, 0, True,
    ),
    "plano-contabil": (f"{NOVAS}/plano-contabil.png", 64, 0, False),
    "de-para": ("pr-shots-listas-carteira/2-de-para-filtro-ativo-desktop.png", 176, 0, True),
    "previa": (f"{NOVAS}/previa.png", 64, 0, False),
    "arquivo-contabil": (f"{NOVAS}/arquivo-contabil.png", 0, 0, True),
    "carteira": ("pr-shots-86e3fr9pd/carteira-linha-unica-desktop.png", 176, 0, True),
    "recebiveis": (f"{NOVAS}/recebiveis.png", 64, 0, True),
    # 70 e não 64: a gaveta cobre a barra, e o corte em 64 partia ao meio a 1ª linha do texto dela.
    "mapeamento": (f"{NOVAS}/mapeamento.png", 70, 0, False),
    "layouts": (f"{NOVAS}/layouts.png", 64, 0, True),
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


for nome, (rel, topo, esquerda, apara) in JOBS.items():
    if not (SRC / rel).exists():
        # `screenshots/` não é versionado: um worktree novo só tem as capturas que ele mesmo tirou.
        print(f"{nome:18} fonte ausente ({rel}); figura atual mantida")
        continue
    im = Image.open(SRC / rel).convert("RGB")
    w, h = im.size
    im = im.crop((esquerda, topo, w, h))
    if apara:
        im = trim_bottom(im)
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    im.save(OUT / f"{nome}.png", optimize=True)
    print(f"{nome:18} {im.size[0]}x{im.size[1]}")
