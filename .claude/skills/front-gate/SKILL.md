---
name: front-gate
description: >
  Roteiro OBRIGATÓRIO ao tocar apps/web/src/components/ ou apps/web/src/app/.
  Gatilhos literais: "componente novo", "tela nova", "conteúdo cortado", "passando por
  cima", "violação de a11y", "esconder botão por perfil", "tooltip", "cor do tema",
  "gaveta", "tabela nova", "rodar o a11y". Três defeitos se repetem no front e cada um
  já tem solução decidida (ScrollRegion, lib/authz.ts, gate de a11y em browser real nos
  3 temas) — e gate verde NÃO prova layout: toda task de UI termina com o PNG aberto.
---

# /front-gate — padrões de UI, autorização na tela e gate de a11y

Junta num roteiro só o que vivia espalhado entre CLAUDE.md §7, `.claude/design-system.md`,
o cabeçalho de `scripts/a11y-gate.sh` e o próprio `apps/web/e2e/a11y-mocked.spec.ts`.
Caminhos e números de linha conferidos em 10/09/2026 (Next 14, Playwright 1.59.1) — se um
grep não bater, o código andou: releia o arquivo. Caminhos abaixo são relativos a
`apps/web/src/` salvo quando dito o contrário.

## 1. Área rolável: `<ScrollRegion>` para card, `<TableCard>` + `<Table fill>` para tabela

`min-h-0 flex-1` AUTORIZA o container a encolher abaixo da altura do conteúdo; sem
`overflow` o conteúdo é pintado FORA da caixa e o que vier depois (barra de paginação,
rodapé) cobre o que vazou — foi a Lista de Conciliações transbordando por baixo da
paginação (86e2u4nxg). E um `div` com `overflow` sem `tabIndex`/`role`/`aria-label`
reprova `scrollable-region-focusable` (SERIOUS) no gate `web_a11y`. A decisão mora em
componente, nunca copiada na tela:

- **Card / conteúdo não-tabular** → `components/ui/scroll-region.tsx` (`:30-41`):
  `overflow-auto` + `tabIndex={0}` + `role="region"` + `aria-label` — a prop `label`
  é obrigatória (`:27`). A ALTURA vem de quem chama (`min-h-0 flex-1` num flex column).
- **Tabela** → `<TableCard>` (`components/ui/table.tsx:73-82`, `flex max-h-full
flex-col overflow-hidden`) + `<Table fill>` (`:27`, `:43`). Nunca `overflow` no
  container de fora: dois scrollers aninhados espremem as colunas em 390px em vez de
  rolar (ADR-007-FE).
- **Shell**: só o `<main>` rola — `app/(app)/layout.tsx:121` (`h-dvh overflow-hidden`)
  e `:168` (`main … overflow-y-auto`). Proibido `h-screen`/`min-h-screen`/`100vh`.
- Travas no browser: "a barra de paginação NUNCA cobre um card" (`e2e/a11y-mocked.spec.ts:1081`)
  e "a tabela rola dentro da própria área" (`:1336`).

```bash
grep -rn "overflow-auto\|overflow-y-auto" apps/web/src/components apps/web/src/app --include=*.tsx | grep -v "ui/scroll-region.tsx\|ui/table.tsx\|ui/combobox.tsx\|ui/select.tsx\|ui/dropdown-menu.tsx\|ui/sheet.tsx\|__tests__"
# esperado hoje: 4 linhas justificadas — notification-bell (lista max-h-80), trocar-lancamento-modal (lista max-h-80), layout.tsx aside e main. Linha NOVA aqui = usar ScrollRegion/TableCard.
```

## 2. Autorização na tela: `lib/authz.ts`, nunca `role ===` para esconder ação

O espelho da `PERMISSION_MATRIX` do backend é `lib/authz.ts` (`PERMISSION_MATRIX`,
`:83`, **13 permissões × 5 papéis** desde a camada de organizações; indexada por papel:
papel novo no contrato quebra a compilação até alguém decidir o que ele vê — foi assim
que `platform_admin` entrou em 86e36ecwa). **Isto não é segurança** (`:12-16`) — a
autoridade é o backend, pela linha do usuário a cada request. O que o helper evita é o
defeito de mostrar botão que devolve 403 (CLAUDE.md §4.9: cada ❌ da matriz = bloqueio no
backend E ação oculta na tela).

- `hasPermission(user, permission)` (`:150`) — ação por papel; `isStaff` (`:175`, plataforma
  OU organização); `canAccessClient` (`:188`); `canSeeSystemArea` (`:202`);
  `canManageSystemUsers` (`:213`, hoje é `hasPermission('manage_org_users')`);
  `homePathFor` (`:225`); `roleLabel` (`:242`, nunca o enum cru na tela);
  `organizationLabel` (`:256`, "Plataforma" ou o nome da organização, para o header).
- Copie de: `components/features/navigation/nav-items.tsx:97` (Configurações item a item
  pela matriz) e `:201`, `client-users/client-users-screen.tsx:59`,
  `clients/client-shell.tsx:119,125`, `glossary/glossary-screen.tsx:68`. Deep link negado
  degrada para `components/shared/access-denied.tsx` (mensagem + caminho de volta), nunca
  tela branca.
- `role ===` fora do helper só para RÓTULO/badge ou filtro de dados — hoje 6 ocorrências
  conhecidas (`users/user-badges.tsx:18`, `client-users/client-user-badges.tsx:25`,
  `client-users/client-user-form-drawer.tsx:290`, o filtro de candidatos em
  `clients/client-managers-section.tsx:93` — que vira `?role=&organizationId=` na
  86e36ed1d — e a trava de auto-rebaixamento em `users/edit-user-modal.tsx:99,162`).
  Ocorrência nova que MOSTRA/ESCONDE uma ação é defeito.
- Travas no browser: "operador do cliente não vê a tela nem o item de menu" (`spec:1604`)
  e "gerente da ORGANIZAÇÃO gere os usuários do tenant da carteira" (`:1622` — a D2
  inverteu este caso em 86e36ecjp; o negativo desta tela é o operador).

```bash
grep -rn "role ===" apps/web/src --include=*.tsx --include=*.ts | grep -v "lib/authz.ts\|__tests__" | grep -v ":\s*\(\*\|//\)"   # esperado: as 6 acima, nenhuma nova
grep -rn "hasPermission(\|canAccessClient(\|canSeeSystemArea(" apps/web/src/components/features/<sua-pasta>/   # a sua tela consulta o helper
```

## 3. Gate de a11y: como rodar de verdade

**O que é.** `scripts/a11y-gate.sh` é espelho 1:1 do job `web_a11y` do
`.github/workflows/ci.yml` (matrix `theme: [light, dark, hologram]`): build standalone
do Next → servidor de produção em `127.0.0.1:3100` (`A11Y_PORT` muda) →
`e2e/a11y-mocked.spec.ts` com a API interceptada no browser (`page.route('**/api/v1/**')`,
`spec:1040`) → `--retries=0` → guard por tema (`expected > 0`, `skipped = 0`,
`flaky = 0`; `a11y-gate.sh:129-152`). NÃO precisa de Postgres, seed, API nem credencial.
Roda em DOIS viewports (`playwright.config.ts:36-39`: desktop e Pixel 5 — o
`scrollable-region-focusable` só existia em 390px). Relatório em
`apps/web/test-results/a11y-report-<tema>.json` (diretório ignorado na raiz,
`.gitignore:94`); screenshots só com `E2E_SHOTS=1`, em `apps/web/a11y-shots/<tema>/`
(`spec:211-216`, ignorado em `apps/web/.gitignore:14`).

**Não confunda** com a suíte irmã `e2e/a11y.spec.ts` (ambiente completo): sem
`E2E_PASSWORD`/`E2E_CLIENT_ID` ela faz `test.skip` (`:40`) e deixaria o gate verde sem
medir nada — é exatamente o modo de falha que o guard existe para pegar.

**Passo 0 — `docker ps` ANTES de dizer que não dá para medir.** O Chromium do host não
sobe (`libnspr4.so: cannot open shared object file`) e `playwright install --with-deps`
exige root. A disponibilidade do Docker já oscilou quatro vezes; a imagem
`mcr.microsoft.com/playwright:v1.59.1-noble` (casa com o `@playwright/test` 1.59.1
instalado) costuma estar em cache. Docker desligado aparece como "The command 'docker'
could not be found in this WSL 2 distro": é ligar o Docker Desktop, não desistir.

```bash
docker ps && docker images --format '{{.Repository}}:{{.Tag}}' | grep playwright
```

**A linha literal do cabeçalho do script NÃO funciona no container** (medido em
10/09/2026): `docker run … bash scripts/a11y-gate.sh` morre em `pnpm: command not found`
sem corepack e, com corepack, no 1º passo (`a11y-gate.sh:73`): `Switching to root user
to install dependencies… su: Authentication failure` (exit 1 em 6 s). O cabeçalho ainda
cita `v1.48.0-jammy`; a imagem provada é a `v1.59.1-noble`. O caminho que funciona é o
espelho SEM esse passo — build no host, servidor E suíte dentro de UM container
(`--network host` não alcança o host no Docker Desktop):

```bash
# 1) build standalone no HOST (mesmos passos do script, linhas 77-87)
INTERNAL_API_URL="http://127.0.0.1:8000" pnpm --filter @auditoria/web build
mkdir -p apps/web/.next/standalone/apps/web/.next/static apps/web/.next/standalone/apps/web/public
cp -r apps/web/.next/static/. apps/web/.next/standalone/apps/web/.next/static/
cp -r apps/web/public/. apps/web/.next/standalone/apps/web/public/
# 2) UM container, não-root (nada sai root-owned): servidor 3100 + suíte por tema + guard
docker run --rm --ipc=host -u "$(id -u):$(id -g)" -v "$PWD":"$PWD" -w "$PWD" -e HOME=/tmp \
  -e E2E_BASE_URL=http://127.0.0.1:3100 mcr.microsoft.com/playwright:v1.59.1-noble bash -c '
  PORT=3100 HOSTNAME=127.0.0.1 NODE_ENV=production node apps/web/.next/standalone/apps/web/server.js >/tmp/web.log 2>&1 &
  for _ in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:3100/login && break; sleep 1; done
  cd apps/web && for THEME in light dark hologram; do
    E2E_THEME=$THEME PLAYWRIGHT_JSON_OUTPUT_NAME=test-results/a11y-report-$THEME.json \
      node node_modules/@playwright/test/cli.js test e2e/a11y-mocked.spec.ts --retries=0 --trace=retain-on-failure --reporter=list,json | tail -3
    node -e "const s=JSON.parse(require(\"fs\").readFileSync(\"test-results/a11y-report-$THEME.json\",\"utf8\")).stats;console.log(\"$THEME\",s);process.exit(s.expected>0&&!s.skipped&&!s.flaky?0:1)"
  done'
```

Confira o `pwd` antes (o `cd` vaza entre comandos): `$PWD` errado constrói em
`apps/web/apps/web`. Um tema só: rode o loop com `THEME=hologram`.

**Medido em 10/09/2026 por esse caminho** (não-root, imagem em cache, sem rede além do
registry): `198 passed` por tema — claro em 2,7 min, escuro em 2,3 min, Hologram em
2,4 min — e o guard `expected=198 unexpected=0 skipped=0 flaky=0` nos três;
`FINAL_EXIT=0`. Total ~7,5 min depois do build. Só o relatório do ÚLTIMO tema sobrevive
(o Playwright limpa `test-results/` a cada run) — o guard lê cada um logo após o seu run.

**Como ler o resultado.**

- `--retries=0` é de propósito: com retry, violação que aparece na 1ª tentativa e some
  na 2ª vira `flaky` e o Playwright sai 0 — gate verde com `serious` real. A11y é
  determinístico: intermitente é violação (o guard reprova `flaky > 0`).
- **Verde NÃO valida layout.** O axe mede árvore de acessibilidade e contraste; não mede
  transbordo, corte nem quebra de linha. Na Sprint 7 os três gates passaram com o botão
  "Confirmar e lançar" clipado em 390px. Toda task de UI termina com o PNG desktop E
  390px ABERTO e conferido contra: ação primária cortada na borda, elemento pintando
  fora do card, gaveta cortada, coluna espremida, valor monetário quebrado após o hífen
  (`-R$ 150,50` lido como crédito → `whitespace-nowrap`).
- Corte corrigido é travado com MEDIDA, não com olho: `boundingBox().x + width <=
viewportSize().width` (padrão em `spec:1638-1643` e `:1838-1853`). Antes de medir,
  `aguardarAnimacao()` (`:915`) — a gaveta do Radix entra deslizando; e antes de medir
  toast, `aguardarToastEstavel()` (`:931`) — o Sonner entra em fade e o axe mede cor
  mesclada (4,25:1 num par que dá 4,75:1). **Visível não é estável.**
- `formatBRL` usa espaço NÃO-quebrável: locator com espaço normal nunca casa (use
  `/R\$\s*150,50/`).
- Camada rápida (roda no `pnpm test`, sem browser): `src/test/a11y.ts`
  (`assertNoA11yViolations`, `:34`) com `color-contrast` DESLIGADO (`:37`, jsdom não
  computa cor) — por isso `app/__tests__/theme-contrast.test.ts` trava os pares de
  token nos três blocos de tema. Uma não substitui a outra.

## 4. Padrões de componente (cada um com o grep)

- **Cor só por token semântico** (`success`/`warning`/`info`/`destructive` + `-foreground`/
  `-muted`, neutros `muted`/`border`/`input`), definidos nos três blocos de
  `app/globals.css` (`:root` `:6`, `.dark` `:69`, `.hologram` `:115`). Nada de
  `emerald-100`/`zinc-700` nem `dark:` em componente. Pareamento que não inverte:
  sobre o SÓLIDO usa-se `-foreground`; sobre `-muted` o texto é o SÓLIDO.
  ```bash
  grep -rnE "\b(text|bg|border|ring|from|to|via)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}\b|\bdark:" apps/web/src/components --include=*.tsx   # esperado: 0
  ```
- **Tema**: `next-themes` no layout RAIZ (`app/providers.tsx:106-115`,
  `defaultTheme="hologram"`, `themes={['light','dark','hologram']}`); toggle em
  `components/shared/theme-toggle.tsx`. Dropdown que abre sobre a página usa
  `modal={false}` (`theme-toggle.tsx:55`, mesmo padrão do sino) — o modo modal do Radix
  marca o fundo com `aria-hidden` mantendo focáveis (`aria-hidden-focus`). O `Select`
  do Radix não tem `modal={false}`: no e2e, exercite-o aberto e rode `analyze()`
  (`spec:888`) com ele FECHADO (86e34jd8m).
- **TypeScript strict, com `noUncheckedIndexedAccess`** (`apps/web/tsconfig.json`): indexar
  array ou dicionário devolve `T | undefined`, então acesso por índice pede verificação
  antes do uso. É o que impede um `.map()` sobre resultado de API vir a explodir em runtime.
  O gate que prova é o `pnpm type-check:web` da skill `gate`.
- **Server component por padrão; `"use client"` só quando precisa** de estado, efeito,
  evento ou hook de browser. Componente que só renderiza dado não leva a diretiva — ela
  empurra o componente e toda a sua árvore para o bundle do cliente.
  ```bash
  grep -rln '"use client"' apps/web/src/components | wc -l   # cresce só quando há interação nova
  ```
- **Fetch client-side só via TanStack Query**: hooks em `hooks/use-*.ts` (11 arquivos),
  fetchers em `lib/api/*.ts`. Nunca `useEffect + fetch`.
  ```bash
  grep -rnE "(^|[^a-zA-Z_.])fetch\(" apps/web/src --include=*.tsx --include=*.ts | grep -v "lib/api/\|__tests__\|lib/contracts"   # esperado: nada
  ```
- **Form sempre `react-hook-form + zod`** (`zodResolver`; schemas em
  `lib/validation/*.ts`, ex.: `glossary.ts`; forms em
  `components/features/glossary/glossary-form-drawer.tsx`,
  `anomaly-types/anomaly-type-create-dialog.tsx`). Botão de ação async: `disabled` +
  spinner, reabilita em sucesso OU erro.
- **Tooltip NUNCA é `title` nativo** (não aparece no toque, não alcança teclado, leitor
  ignora): `components/ui/tooltip.tsx` com `role="img"` + `aria-label` com a explicação
  INTEIRA + `tabIndex={0}` — copie de `reconciliations/review/situation-badge.tsx`,
  `reconciliations/review/qualification-cell.tsx`, `reconciliations/author-label.tsx`.
  Hoje restam 5 `title` nativos legados (texto truncado em `file-input-field.tsx:106`,
  `upload-item-row.tsx:52`, `anomaly-types-table.tsx:109`; badge em
  `glossary-badges.tsx:47`; input em `anomaly-type-edit-dialog.tsx:132`) — não crie
  o 6º; migrar é task própria. (`category-badge.tsx:43` passa `title={undefined}` de
  propósito: entra no grep, não é tooltip.)
  ```bash
  grep -rnE "<(p|span|div|button|a|td|th|svg|input)[^>]*\btitle=" apps/web/src/components --include=*.tsx | grep -v __tests__ | wc -l   # hoje 3 inline (1 é title={undefined}) + 3 em linha própria
  ```
- **`cursor-pointer` no componente-base**, não tela a tela (`components/ui/button.tsx:8-11`);
  secundário com cor da paleta (`variant="secondary"` → `bg-secondary`, `:18`), nunca
  cinza indistinguível.
- **Gaveta (Sheet)**: `SheetHeader`/`SheetBody`/`SheetFooter` — header e rodapé fixos,
  miolo rola, **Cancelar à esquerda** e primária à direita (`justify-between`,
  `components/ui/sheet.tsx:91-96`); exemplo `glossary-form-drawer.tsx:227-241`, ambos
  os botões `disabled` no loading. Nunca modal fullscreen para criar/editar.
- **Estados loading / vazio / erro em todo dado assíncrono.** Não há `EmptyState`
  compartilhado: três telas definem o seu — copie o de `glossary-screen.tsx:269`
  (mensagem + ação sugerida) em vez de inventar um quarto. `loading.tsx`/`error.tsx`
  de rota existem só em `app/(app)/clientes/[clientId]/{glossario,usuarios}/`.
- **Data**: não existe date picker no `ui/`. Mês de referência é `<input type="month"
lang="pt-BR">` (`reconciliations/list/reconciliations-list.tsx:184-189`) ou `Select`
  na gaveta de criação. Precisou de DIA: adicione `ui/calendar` (shadcn + `date-fns`
  `ptBR`) — nunca `<input type="date">` cru (design-system).
  ```bash
  grep -rn 'type="date"' apps/web/src --include=*.tsx | wc -l   # esperado: 0
  ```
- **Tabela > 100 linhas virtualizada** (`@tanstack/react-virtual` está instalado e HOJE
  não é usado): toda lista é paginada com `pageSize ≤ 100` (teto do backend), então
  nenhuma tabela ultrapassa. Tabela nova que renderize mais que 100 linhas sem paginação
  vira o primeiro uso — não a primeira exceção.
- **Listas**: estado na URL, `components/ui/pagination-bar.tsx` FIXA no rodapé mesmo com
  1 página (`:3-9`); dropdown/combobox com altura máxima e scroll (`combobox.tsx:206`).

## 5. Fechamento

1. Screenshot desktop E 390px aberto e conferido (lista do §3); prints de PR vão em
   `screenshots/pr-shots-<task>/` (pasta ignorada; nunca em `test-results/`).
2. Rode a skill `gate` — tocou `apps/web/src`? O `web_a11y` faz parte do portão (§3
   acima é o "como"). Feche com a skill `entrega`.
3. Mudou token, tema, componente-base ou regra de layout? CLAUDE.md §7 e
   `.claude/design-system.md` mudam na MESMA entrega (§13).
