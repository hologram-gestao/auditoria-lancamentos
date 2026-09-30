# Manual do Hologram OS (PDF)

**[Manual-Hologram-OS.pdf](Manual-Hologram-OS.pdf)**: versão 1.1, 37 páginas, A4, com 14
capturas de tela do produto. Material de apoio para apresentação a escritórios contábeis e BPOs,
e manual de operação para quem usa o sistema. Produto: **Hologram OS**; empresa: Hologram Gestão.

## O que mudou na 1.1 (30/09/2026, task 86e3gqfmj)

- **Nome.** O produto passa a se chamar Hologram OS (decisão do Pedro, 30/09/2026). Capa com o
  nome acima do título, cabeçalho das páginas, e "o Hologram OS" na primeira menção de cada
  capítulo; nas seguintes, "a plataforma". O box "Uma observação sobre o nome" saiu.
- **Cap. 10, segurança.** Saiu a frase "não existe uma chave mestra que abra tudo", que era
  falsa: a chave de cada cliente é guardada trancada por uma chave-mestra num cofre de chaves
  gerenciado (Cloud KMS). Entrou o bloco "Processamento por inteligência artificial": o arquivo
  da conciliação é lido pela Anthropic; a análise de classificação envia a descrição, o valor, o
  fornecedor e a categoria de cada movimentação conciliada, mais o glossário do cliente; o
  cruzamento e a origem por arquivo não usam IA. Nada é afirmado sobre retenção ou treinamento
  do provedor (task 86e3anx75 aberta).
- **Cap. 3, perfis.** A tabela foi refeita linha a linha contra `PERMISSION_MATRIX`
  (`apps/api/app/core/authz.py`): "Sincronizar dados da origem" virou "Sincronizar contas
  bancárias" (todos) e "Sincronizar plano de contas, títulos e movimentos" (todos menos o
  operador do cliente), e entraram mapeamento do arquivo, contexto de título, usuários do cliente
  e redefinição de senha. 19 linhas.
- **Cap. 4, temas.** O padrão é o tema Hologram, não o escuro; a escolha fica no navegador; as
  figuras de 6.5 e 6.6 são do tema escuro e as demais do Hologram.
- **Três erros factuais que a conferência achou:** o cadastro de cliente não tem CNPJ (5.1 dizia
  que era obrigatório; o cap. 9 dizia que o encerramento o removia), e a conciliação não aceita
  `.xls` (o servidor recusa e pede `.xlsx` ou `.csv`).
- **Sete figuras recapturadas** com dados de demonstração limpos (antes traziam nomes de teste
  como "Cliente Sem Sistema S9" e "Domínio S13 d3656c"): `painel`, `recebiveis`,
  `plano-contabil`, `previa`, `mapeamento`, `arquivo-contabil` e `layouts`.

## Decisões deste documento

- **A barra superior das telas foi recortada** de todas as capturas: é onde aparece o nome
  antigo ("Auditoria de Lançamentos").
- **Dados das capturas são fictícios** ("Padaria Aurora Ltda", "Comercial Horizonte Ltda",
  "Cliente Exemplo Ltda", "Ana Souza"). Nenhuma captura veio de cliente real; o plano contábil e
  o extrato do Comercial Horizonte são a amostra anonimizada de
  `apps/api/tests/fixtures/accounting_sample/`.
- **Público:** equipe do escritório (sócio, gerente, analista), com um capítulo sobre o
  acesso concedido ao cliente final.
- O conteúdo reflete o produto na `origin/develop` em 30/09/2026, incluindo as Sprints 13 e 16
  e o épico de layout das telas do cliente.

## Como regerar

```bash
# 1. figuras (recorta a barra superior dos prints em screenshots/; fonte ausente = figura mantida)
uv run --with pillow python Docs/manual/fonte/recortar-imagens.py

# 2. PDF (WeasyPrint em container; não há toolchain de PDF no host)
docker run --rm -v "$PWD/Docs/manual/fonte":/w minidocks/weasyprint \
  weasyprint /w/manual.html /w/manual.pdf
mv Docs/manual/fonte/manual.pdf Docs/manual/Manual-Hologram-OS.pdf
```

Para conferir o resultado sem abrir o PDF:

```bash
uv run --with pypdfium2 --with pillow python -c "
import pypdfium2 as pdfium
d = pdfium.PdfDocument('Docs/manual/Manual-Hologram-OS.pdf')
[d[i].render(scale=1.4).to_pil().save(f'/tmp/p{i+1:02d}.png') for i in range(len(d))]"
```

### Recapturar as figuras da 1.1

`screenshots/` não é versionado; o roteiro que produziu as sete figuras novas fica em
`fonte/capturas/`. Os caminhos absolutos dentro dos dois scripts são desta máquina: ajuste antes.

1. Banco isolado: `createdb adl_manual`, `alembic upgrade head` e `scripts/seed_dev.py`, com a
   API apontada para ele por variável de ambiente (chaves de desenvolvimento descartáveis, nunca
   o `.env` real). Nomes fictícios para os dois usuários do seed, por SQL.
2. API em `127.0.0.1:8031` e `next dev -H 0.0.0.0 -p 3031` com `INTERNAL_API_URL` apontando
   para ela.
3. `uv run --directory apps/api python Docs/manual/fonte/capturas/dados_demo.py`: cria a
   Padaria Aurora (Omie simulado, carteira com dois contextos), o Comercial Horizonte (origem
   por arquivo, plano contábil, 23 decisões, materialização), os dois layouts, a gerente Ana
   Souza e três gerações do arquivo.
4. Playwright em container, 1440x900, tema Hologram (o padrão). No Docker Desktop a rede
   `host` não alcança o WSL: use `host.docker.internal` e `--add-host`.

```bash
docker run --rm --add-host=host.docker.internal:host-gateway --user $(id -u):$(id -g) \
  -e HOME=/tmp -e SHOTS_BASE=http://host.docker.internal:3031 -v <web>:<web>:ro \
  -v <screenshots>:<screenshots> -v <capturas>:<capturas>:ro \
  mcr.microsoft.com/playwright:v1.59.1-noble node <capturas>/shots.mjs <padariaId> <horizonteId>
```

## Arquivos

| Caminho | O que é |
| --- | --- |
| `Manual-Hologram-OS.pdf` | O documento final |
| `fonte/manual.html` | Todo o texto do manual |
| `fonte/manual.css` | Folha de estilo de impressão (capa, cabeçalhos, sumário com páginas, figuras) |
| `fonte/img/` | As 14 figuras já recortadas |
| `fonte/fonts/` | IBM Plex Serif/Mono e Fira Sans (licença OFL), embutidas no PDF |
| `fonte/recortar-imagens.py` | Gera `fonte/img/` a partir de `screenshots/` |
| `fonte/capturas/` | Roteiro das capturas da 1.1: dados de demonstração pela API e Playwright |
