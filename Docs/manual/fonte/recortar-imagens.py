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

# nome de saída -> (caminho em screenshots/, topo, esquerda, apara embaixo[, base])
# Na 1.2 (86e3mz74x) as 14 foram recapturadas no tema Hologram, em DPR 2 (2880 px de largura),
# então os cortes são o dobro dos pixels CSS: a barra superior tem 64 px CSS = 128 aqui, e o
# menu lateral 223 px CSS = 446. `mock/` vem do e2e com a API interceptada
# (`apps/web/e2e/a11y-mocked.spec.ts`, cliente fictício "Cliente Exemplo Ltda"); `app/` vem da
# tela real com os dados de `capturas/dados_demo.py` (roteiro no README). `base`, quando há, é
# a última linha da figura.
PASTA = "pr-shots-86e3mz74x"
MOCK = f"{PASTA}/mock"
APP = f"{PASTA}/app"
JOBS: dict[str, tuple[str, int, int, bool] | tuple[str, int, int, bool, int]] = {
    "clientes": (f"{MOCK}/lista-clientes-acoes-desktop.png", 128, 0, True),
    # Só o fechamento do mês e a carteira: o fluxo e a atividade ficam abaixo, fora da figura.
    "painel": (f"{APP}/painel.png", 128, 0, False, 2090),
    "conciliacao": (f"{MOCK}/detalhe-conciliacao-desktop.png", 128, 0, False),
    "anomalias": (f"{MOCK}/revisao-veredito-desktop.png", 860, 446, True),
    "lancamento": (f"{MOCK}/lancamento-selecao-desktop.png", 712, 446, False),
    "plano-de-contas": (f"{MOCK}/plano-de-contas-filtro-ativo-desktop.png", 128, 0, True),
    "plano-contabil": (f"{APP}/plano-contabil.png", 128, 0, False),
    "de-para": (f"{MOCK}/de-para-filtro-ativo-desktop.png", 128, 0, True),
    "previa": (f"{APP}/previa.png", 128, 0, False),
    "arquivo-contabil": (f"{APP}/arquivo-contabil.png", 0, 0, True),
    "carteira": (f"{MOCK}/carteira-linha-unica-desktop.png", 128, 0, True),
    "recebiveis": (f"{APP}/recebiveis.png", 128, 0, True),
    # A gaveta cobre a barra (escurecida) e o título dela começa no alto: sem corte.
    "mapeamento": (f"{APP}/mapeamento.png", 0, 0, False),
    "layouts": (f"{APP}/layouts.png", 128, 0, True),
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
    if not (SRC / rel).exists():
        # `screenshots/` não é versionado: um worktree novo só tem as capturas que ele mesmo tirou.
        print(f"{nome:18} fonte ausente ({rel}); figura atual mantida")
        continue
    im = Image.open(SRC / rel).convert("RGB")
    w, h = im.size
    im = im.crop((esquerda, topo, w, resto[0] if resto else h))
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
