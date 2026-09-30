# Manual da plataforma (PDF)

**[Manual-da-Plataforma.pdf](Manual-da-Plataforma.pdf)** — 37 páginas, A4, com 14 capturas
de tela do produto. Material de apoio para apresentação a escritórios contábeis e BPOs, e
manual de operação para quem usa o sistema.

## Decisões deste documento

- **Sem nome próprio.** O produto é tratado como "a plataforma" o tempo todo, porque o
  nome novo ainda não estava definido em 29/09/2026 (task de nome e domínio do épico
  `86e3fr9tj`). Quando o nome sair, é substituir a string no HTML e regerar.
- **A barra superior das telas foi recortada** de todas as capturas: é onde aparecem o
  nome antigo ("Auditoria de Lançamentos") e a marca Hologram.
- **Dados das capturas são fictícios** ("Cliente Exemplo Ltda", "Padaria Aurora"). Nenhuma
  captura veio de cliente real.
- **Público:** equipe do escritório (sócio, gerente, analista), com um capítulo sobre o
  acesso concedido ao cliente final.
- O conteúdo reflete o produto na `origin/main` em 29/09/2026, **incluindo a Sprint 13**
  (exportador para sistema contábil), a Sprint 16 (plano contábil e partida) e o épico de
  layout das telas do cliente.

## Como regerar

```bash
# 1. figuras (recorta a barra superior dos prints em screenshots/)
uv run --with pillow python Docs/manual/fonte/recortar-imagens.py

# 2. PDF (WeasyPrint em container; não há toolchain de PDF no host)
docker run --rm -v "$PWD/Docs/manual/fonte":/w minidocks/weasyprint \
  weasyprint /w/manual.html /w/manual.pdf
mv Docs/manual/fonte/manual.pdf Docs/manual/Manual-da-Plataforma.pdf
```

Para conferir o resultado sem abrir o PDF:

```bash
uv run --with pypdfium2 --with pillow python -c "
import pypdfium2 as pdfium
d = pdfium.PdfDocument('Docs/manual/Manual-da-Plataforma.pdf')
[d[i].render(scale=1.4).to_pil().save(f'/tmp/p{i+1:02d}.png') for i in range(len(d))]"
```

## Arquivos

| Caminho | O que é |
| --- | --- |
| `Manual-da-Plataforma.pdf` | O documento final |
| `fonte/manual.html` | Todo o texto do manual |
| `fonte/manual.css` | Folha de estilo de impressão (capa, cabeçalhos, sumário com páginas, figuras) |
| `fonte/img/` | As 14 figuras já recortadas |
| `fonte/fonts/` | IBM Plex Serif/Mono e Fira Sans (licença OFL), embutidas no PDF |
| `fonte/recortar-imagens.py` | Gera `fonte/img/` a partir de `screenshots/` |
