# Manual do Hologram OS (PDF)

**[Manual-Hologram-OS.pdf](Manual-Hologram-OS.pdf)**: versão 1.4, 54 páginas, A4, com 24
capturas de tela do produto, todas no tema Hologram. Material de apoio para apresentação a
escritórios contábeis e BPOs, e manual de operação para quem usa o sistema. Produto:
**Hologram OS**; empresa: Hologram Gestão. A landing serve uma cópia idêntica byte a byte em
`apps/web/public/manual-hologram-os.pdf`, com o tamanho ao lado do botão (`content.ts`, travado
por teste): ao regerar, copie o PDF e confira o tamanho.

## O que mudou na 1.4 (21/10/2026, task 86e3qhhtj)

Os capítulos que o épico da reunião com o Gabriel (86e3qhhr0, PRs #338 a #356) mudou ou não
cobria, mais duas entregas de fora do épico, posteriores à 1.3, que entraram de carona: o selo
"No Omie" na linha conciliada (86e3t4vpq) e o cartão cadastrado no Omie num tipo que a
plataforma não reconhece (86e3qyu04). O que estava certo ficou como estava.

- **Cap. 2:** o caminho de volta para a Omie (a planilha do Painel do Contador e o guia das
  categorias).
- **5.5:** a reimportação que só adiciona e atualiza, com "Revisar mudanças" antes de gravar, o
  selo "Não veio na última importação" e "Baixar planilha"; a natureza devedora/credora; a aba
  "Clientes e fornecedores" em duas listas (importar as relações do Domínio, o que recusa o
  arquivo e o que só fica sem vínculo, vincular e remover à mão, o documento cifrado); "Gerar
  planilha da Omie" (documento sem cadastro, contas a corrigir, a importação no Painel do
  Contador como passo do escritório).
- **6.1:** o cartão cadastrado em outro tipo no Omie, em que o arquivo decide.
- **6.2:** o pagamento e os encargos do RESUMO da fatura não viram compra.
- **6.4:** o selo "No Omie" e o que ele não decide.
- **6.6:** o fornecedor pelo nome ou pela sugestão da compra e o diretório guardado por algumas
  horas; a compra parcelada (vencimentos, a fatura seguinte que encontra o título, o aviso da
  parcela que não veio e que a plataforma não exclui título, a data impressa pela Cora).
- **7.3:** o guia para configurar as categorias na tela da Omie (três blocos e o comportamento
  padrão).
- **Cap. 11 e apêndices:** dois limites (plano e categorias chegam na Omie pela mão do
  escritório; comportamento por categoria não guardado), quatro recusas, quatro passos no
  checklist do cliente novo e dois no mensal, quatro termos no glossário (guia das categorias,
  natureza, parcela futura, vínculo). O checklist é o Apêndice B, não o cap. 11 como a
  descrição da task dizia.
- **Figuras:** seis novas, recortadas dos prints das subtasks pelo `recortar-imagens.py`
  (`vinculos`, `planilha-omie`, `guia-omie`, `parcelas`, `fornecedor-nome`, `situacao-omie`).
  Esses prints são do mesmo e2e mockado, mas em DPR 2,75, e os cortes estão nessa escala. O
  `plano-contabil` foi recapturado pelo mesmo cenário de antes ("nova conta: gaveta, código
  repetido…", DPR 2), agora com as abas, "Baixar planilha", "Gerar planilha da Omie" e o selo; o
  print novo substituiu o antigo em `screenshots/pr-shots-86e3ndj0p/mock/` com o mesmo nome.
- O PDF passou de 4,9 para 6,4 MB (nove páginas e seis figuras a mais); o botão da landing
  diz "PDF, 6 MB".

## O que mudou na 1.3 (13/10/2026, task 86e3ndj0p)

Os capítulos que o épico de ajustes da reunião com o Murilo (86e3n70nv, PRs #309 a #331) e a
exclusão de layout (#323) mudaram. O que estava certo ficou como estava.

- **5.1:** o interruptor "Conectar com o Omie" e os três campos que só existem com ele:
  "Compras do cartão no Omie" (na data da compra ou no vencimento da fatura), "Exigir rateio
  por departamento nos títulos" e "Fornecedor padrão no Omie" (só no editar, pela busca por
  CNPJ), com um quadro do passo a passo do cliente que lança no vencimento.
- **5.4** passou a se chamar "Categorias do Omie: o plano da origem", como a tela.
- **5.5:** o `.xls` do Domínio sem edição, a ordem da classificação, o cabeçalho pela grafia
  comum e "Nova conta"/editar, com o que a edição não deixa fazer.
- **6.1 a 6.4:** o bloco "Fatura do cartão" da criação e a recusa sem vencimento; o lote da
  fatura no cruzamento e por que o cabeçalho diz "Lote da fatura"; os selos da linha
  ("Lançado no Omie", "Provisionado", "Lançamento parado") e "Reprocessar com o Omie".
- **6.6 reescrita:** provisionar no Contas a Pagar (o padrão, com ou sem baixa conforme o
  processo) ou lançar direto; fornecedor por CNPJ ou o padrão, cadastro na hora (que não se
  desfaz), rateio, compra dividida em categorias (o caso do IOF), retomada e os limites.
- **7.3:** destino, alvo, decisão e herdada ("Como funciona"); o catálogo em Configurações >
  Destinos do de-para e a criação dos alvos a partir da origem.
- **Cap. 2, 3, 9, 11 e apêndices:** o nome da tela do plano da origem; a linha do catálogo
  de destinos na tabela de perfis; excluir layout nunca usado; dois limites novos; nove
  recusas novas; os passos do cartão e da provisão no checklist; três termos no glossário.
- **Figuras:** sete do e2e de novo (`screenshots/pr-shots-86e3ndj0p/mock/`): `conciliacao`
  (com "Reprocessar com o Omie"), `lancamento` (a gaveta na provisão, no lugar da barra de
  seleção), `de-para` (com "Como funciona"), `plano-contabil` (na ordem da classificação, com
  "Nova conta"; antes vinha da tela real) e `layouts` (com "Excluir"; antes vinha da tela
  real), mais quatro novas: `editar-cliente`, `fatura-cartao`, `selos` e `destinos-de-para`.
  Gaveta e diálogo recortados sozinhos saem mais estreitos na página (`figure.narrow` e
  `figure.slim` no CSS).
- O PDF passou de 3,9 para 4,9 MB (oito páginas e quatro figuras a mais); o botão da landing
  diz "PDF, 5 MB".

## O que mudou na 1.2 (08/10/2026, task 86e3mz74x)

- **As 14 figuras recapturadas no tema Hologram** com a estética atual (verde de ação, cards,
  menu do cliente em Operação, Cadastros e Acesso, contadores no menu, painel novo). As de 6.5 e
  6.6 eram do tema escuro. Saem em DPR 2 e são reduzidas a 2000 px com paleta de 256 cores.
- **Texto que as telas novas desmentiam:** a nota de temas do cap. 4 (agora todas no Hologram); o
  menu do cliente em três grupos (cap. 4); a origem se configura em Contas Bancárias, não no
  painel (5.2 e a tabela de recusas); o painel descrito como ele é hoje (6.7), e a figura dele
  saiu de 5.2 para 6.7, onde o texto fala dele.
- **Entrou no PDF o bloco de IA do cap. 10 revisado em 05/10/2026** (task 86e3anx75), que estava
  só na fonte: conteúdo não usado para treino, apagado pelo provedor em até 30 dias e processado
  fora do Brasil, com as fontes em [../seguranca/SUBPROCESSADORES.md](../seguranca/SUBPROCESSADORES.md).
- O PDF passou de 3,0 para 3,9 MB: o WeasyPrint embute as figuras em RGB (a paleta não chega ao
  PDF) e as do app real agora têm o dobro da resolução.

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

- **A barra superior das telas foi recortada** das capturas (na 1.1 ela trazia o nome antigo;
  hoje traz o e-mail e o papel de quem capturou). A exceção é a gaveta de mapeamento, que a cobre.
- **Dados das capturas são fictícios** ("Padaria Aurora Ltda", "Comercial Horizonte Ltda",
  "Cliente Exemplo Ltda", "Ana Souza"). Nenhuma captura veio de cliente real; o plano contábil e
  o extrato do Comercial Horizonte são a amostra anonimizada de
  `apps/api/tests/fixtures/accounting_sample/`.
- **Público:** equipe do escritório (sócio, gerente, analista), com um capítulo sobre o
  acesso concedido ao cliente final.
- O conteúdo reflete o produto na `origin/develop` em 21/10/2026 (texto revisto na 1.1, em
  30/09; na 1.2, só o que as telas novas desmentiam; na 1.3, os capítulos tocados pelo épico
  86e3n70nv; na 1.4, os tocados pelo épico 86e3qhhr0).

## Como regerar

```bash
# 1. figuras (recorta e reduz os prints de screenshots/pr-shots-86e3mz74x/, …86e3ndj0p/ e das
#    pastas das subtasks do épico 86e3qhhr0 listadas no JOBS; fonte ausente = figura mantida). Num worktree, SHOTS_DIR
#    aponta a pasta screenshots/ do checkout principal.
SHOTS_DIR=<checkout>/screenshots uv run --with pillow python Docs/manual/fonte/recortar-imagens.py

# 2. PDF (WeasyPrint em container; não há toolchain de PDF no host) e a cópia da landing
docker run --rm -u $(id -u):$(id -g) -v "$PWD/Docs/manual/fonte":/w minidocks/weasyprint \
  weasyprint /w/manual.html /w/manual.pdf
mv Docs/manual/fonte/manual.pdf Docs/manual/Manual-Hologram-OS.pdf
cp Docs/manual/Manual-Hologram-OS.pdf apps/web/public/manual-hologram-os.pdf
```

O tour da landing (`apps/web/public/landing/tour/`) são cinco destas figuras reduzidas a
1600 px e quantizadas com `sharp` (instalado fora do repo: o web não tem `sharp` no lockfile);
largura e altura ficam em `landing-tour.tsx`, conferidas por teste.

Para conferir o resultado sem abrir o PDF:

```bash
uv run --with pypdfium2 --with pillow python -c "
import pypdfium2 as pdfium
d = pdfium.PdfDocument('Docs/manual/Manual-Hologram-OS.pdf')
[d[i].render(scale=1.4).to_pil().save(f'/tmp/p{i+1:02d}.png') for i in range(len(d))]"
```

### Recapturar as figuras

Duas fontes, as duas em 1440x900 com DPR 2 e tema Hologram, mais os prints das subtasks do
épico 86e3qhhr0 e da 86e3t4vpq (pastas `pr-shots-<id>/` no `JOBS`), tirados pelo próprio gate de
a11y de cada entrega em DPR 2,75: para refazer uma dessas, rode o cenário citado no README da
pasta com a config abaixo e ajuste o corte do `JOBS` para DPR 2.

**Treze do e2e com a API interceptada** ("Cliente Exemplo Ltda"): em
`pr-shots-86e3mz74x/mock/`, clientes, anomalias, plano de contas e carteira; em
`pr-shots-86e3ndj0p/mock/`, editar cliente, fatura do cartão, conciliação, selos, lançamento,
plano contábil, de-para, destinos do de-para e layouts. `next build` do web, `.next/static` e
`public/` copiados para o standalone, e no container Playwright o `server.js` em 3100 mais os
cenários do `apps/web/e2e/a11y-mocked.spec.ts` com `E2E_THEME=hologram E2E_SHOTS=1`, uma config
local com um projeto só e `deviceScaleFactor: 2` (`apps/web/a11y-shots/shots.config.ts`,
gitignorada) e `--grep` com os títulos dos cenários. Os prints saem em
`apps/web/a11y-shots/hologram/` com os nomes que o `recortar-imagens.py` espera
(`*-desktop.png`, a origem de cada figura está no `JOBS` dele).

**Cinco da tela real** (`app/`: painel, recebíveis, prévia, arquivo contábil, mapeamento), pelo
roteiro de `fonte/capturas/` (que ainda captura também plano contábil e layouts, hoje vindos do
e2e):

1. Banco NOVO (`createdb`, `alembic upgrade head`, `scripts/seed_dev.py`), com a API apontada
   para ele por variável de ambiente e as chaves fake do CI (`.github/workflows/ci.yml`), nunca
   o `.env` real. Um banco antigo não serve: o dado cifrado só abre com as chaves que o gravaram.
   Nomes fictícios para os dois usuários do seed, por SQL (Rafael Mendes e Marina Alves).
2. API em `127.0.0.1:8031` e `next dev -H 0.0.0.0 -p 3031` com `INTERNAL_API_URL` apontando
   para ela.
3. `uv run --directory apps/api python Docs/manual/fonte/capturas/dados_demo.py`: cria a
   Padaria Aurora (Omie simulado, carteira com dois contextos), o Comercial Horizonte (origem
   por arquivo, plano contábil, 23 decisões, materialização), os dois layouts, a gerente Ana
   Souza e três gerações do arquivo, e imprime os IDs.
4. Playwright em container. No Docker Desktop a rede `host` não alcança o WSL: use
   `host.docker.internal` e `--add-host`. `LANG=pt_BR.UTF-8` e o Chromium completo (o roteiro
   já pede `channel: 'chromium'`), senão o campo de competência sai "August 2026".

```bash
docker run --rm --ipc=host --add-host=host.docker.internal:host-gateway -u $(id -u):$(id -g) \
  -e HOME=/tmp -e LANG=pt_BR.UTF-8 -e LANGUAGE=pt_BR \
  -e SHOTS_BASE=http://host.docker.internal:3031 -e WEB_DIR=<repo>/apps/web \
  -e SHOTS_OUT=<checkout>/screenshots/pr-shots-86e3mz74x/app -v <repo>:<repo> -v <saída>:<saída> \
  mcr.microsoft.com/playwright:v1.59.1-noble \
  node <repo>/Docs/manual/fonte/capturas/shots.mjs <padariaId> <horizonteId>
```

## Arquivos

| Caminho                     | O que é                                                                        |
| --------------------------- | ------------------------------------------------------------------------------ |
| `Manual-Hologram-OS.pdf`    | O documento final                                                              |
| `fonte/manual.html`         | Todo o texto do manual                                                         |
| `fonte/manual.css`          | Folha de estilo de impressão (capa, cabeçalhos, sumário com páginas, figuras)  |
| `fonte/img/`                | As 24 figuras já recortadas                                                    |
| `fonte/fonts/`              | IBM Plex Serif/Mono e Fira Sans (licença OFL), embutidas no PDF                |
| `fonte/recortar-imagens.py` | Gera `fonte/img/` a partir das pastas de `screenshots/` listadas no `JOBS`     |
| `fonte/capturas/`           | Roteiro das capturas da tela real: dados de demonstração pela API e Playwright |
