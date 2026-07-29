# Decisões de arquitetura (ADR) — append-only

> Uma entrada por decisão. Nunca edite entradas antigas; para revisar, adicione
> uma nova com `superseded-by`. Converta datas relativas em absolutas.

---

## ADR-000 — Baseline arquitetural genérico (destilado de projetos anteriores)

> Decisões padrão que já valem do dia 1 para um SaaS FastAPI (`api/`) + Next.js (`web/`). Substitua/supersede por ADRs específicos do produto conforme evoluir. Detalhes acionáveis por papel vivem em `.claude/agents/*.md` e `.claude/design-system.md`.

**Arquitetura**
- asyncpg direto, sem ORM na aplicação; Alembic (SQLAlchemy) só no runner de migrations — controle total do SQL. Pool no `lifespan` com warn gracioso se o banco estiver inacessível (dev sem Docker).
- Camadas router → service → repository: service concentra regra de negócio, decrypt e auditoria; repository é SQL puro e não conhece criptografia. `core/deps.py` centraliza dependencies (`get_conn`, `require_*`).
- Server Components por padrão no Next; Client Component só quando necessário. Separar cliente de API server-only (importa `next/headers`) do cliente para Client Components (importar o server-only no client arrasta `next/headers` p/ o bundle e quebra o build).
- Rotas literais/estáticas antes das rotas com path param (`/x/export` antes de `/x/{id}`). 1 query por conexão asyncpg (sequencial; nunca `gather` na mesma conn). Derivar on-read em vez de materializar+trigger quando a verdade precisa estar fresca.

**Auth / Segurança**
- JWT em cookie httpOnly; middleware injeta `usuario_id`/`tenant_id`/`perfil` em `request.state`. bcrypt custo 12 + dummy check (anti-timing). Rolling refresh (revoga o anterior).
- `tenant_id` SEMPRE do JWT, nunca do body; endpoint `/tenant/{id}/...` confere `JWT.tenant == path.id` → 403 cross-tenant. RBAC `require_*` encodado no service (não só router); negado-por-padrão; matriz perfil×ação com teste parametrizado.
- PII cifrada em repouso (AES-256-GCM, nonce prefixado, BYTEA); lookup de PII enumerável (CPF/e-mail) via HMAC-SHA256 com pepper (nunca SHA puro). Fail-fast de segredo crítico no `lifespan`. Auditoria no service com redação automática de PII no log (coberta por teste). Não confiar só no middleware/proxy p/ gating (CVE-2025-29927) — o JWT é a verdade.

**API / Contrato**
- OpenAPI é a fonte da verdade: gerar tipos TS (`openapi-typescript`); front importa só de `contracts`, nunca `interface` à mão; gate CI bloqueia drift. Mutations via cliente tipado (`apiTyped.METHOD`). Docstring/summary de endpoint É contrato.
- Envelope de paginação `{items, page, page_size, total, total_pages}` + `PageParams` (page≥1, default 25, teto 100, 422 fora do range); `total` = COUNT com os MESMOS filtros; paginar DEPOIS de filtrar.
- Erros de domínio → HTTP (400/404/409/422); violação de constraint (unique/exclude) traduzida p/ 409/422 amigável, nunca 500. Endpoint de checagem/integração retorna 200 com classificação no body (não 5xx). Campos aditivos com defaults ao ampliar response. Pydantic v2: `str | None` sem default é REQUIRED → `= None`; `extra="ignore"` no Settings.

**Dados / Migrations**
- Migrations numeradas; nunca renumerar/alterar migration mergeada; downgrade em ordem reversa; não dropar extensão compartilhada (ex.: `btree_gist`) no downgrade. Validar round-trip upgrade→downgrade→upgrade.
- Integridade no banco + pre-check no service: UNIQUE parcial `WHERE deleted_at IS NULL`, `EXCLUDE USING gist` p/ não-sobreposição de ranges. Soft delete padrão (`ativo=false` + `deleted_at=NOW()`). Idempotência em escrita concorrente via `ON CONFLICT ... DO UPDATE`.
- Migração que cifra/altera tipo faz backfill em Python (crypto), idempotente e reversível; abortar com `RAISE EXCEPTION` se houver dado pré-existente inconsistente. Valores monetários/quantidades como inteiros crus (evitar float); formatação só na exibição; cast 64-bit antes de multiplicar.

**Testes / CI**
- `conftest`: `TestClient` + override de `get_conn` p/ `AsyncMock` + tokens JWT por perfil (cobre 401/403/isolamento sem Postgres). Integração com Postgres real obrigatória p/ matemática/SQL (SUM/GROUP BY/JOIN/timestamps/casts). Gate mínimo por endpoint (feliz + 401/403 + isolamento de tenant). Asserção de contrato explícita (campo esperado presente / inventado ausente). Fixture de transação única tem `NOW()` constante → forçar gap p/ testar timestamp.

**Infra / Deploy**
- Deploy automático só p/ `develop`; `main`/prod é humano. Workload Identity Federation (não chave JSON). SA por serviço (mínimo privilégio); `secretAccessor` no próprio secret. Imagens `:dev` (mutável) + `:SHA` (imutável). Smoke pós-deploy (login inválido → 401). Migration como job ANTES do deploy, com as MESMAS chaves de crypto do serviço. `NEXT_PUBLIC_*` como build ARG; `output: standalone` exige `COPY public/`; Server Actions com encryption key estável via secret.

**Convenções**
- Design system por tokens semânticos (CSS vars OKLCH), sem hex/brand hardcoded; audit por grep no CI. Mobile-first (touch ≥44px). Formulários em Drawer única (create+edit), remount por `key`. Proibido `useEffect`+fetch (Server Component / Server Action + `useTransition`). Estado de filtro/busca/paginação/aba na URL. Toast: sucesso verde, erro vermelho (componente único). Validação client espelha o backend 1:1. Harness de screenshot (Playwright) + a11y (`@axe-core/playwright`). Esconder ação indisponível > mostrar e dar erro (mas o guard do backend é a verdade).

---

> ⚠️ **Nota de procedência (28/07/2026 — 3ª reconstituição).** As ADR-004 … ADR-007
> abaixo descrevem decisões da **Sprint 4**, já commitadas em `3e9fbaa`. Este
> arquivo chegou nesta run **outra vez** só com o stub `ADR-000` — `.claude/` é
> gitignored (`.gitignore:69`) e o worktree é re-semeado a cada run do
> orquestrador. Cada afirmação abaixo foi **reconferida contra o código
> commitado** (docstrings de `usage_events/repository.py`,
> `reconciliations/totals.py`, `db/models/reconciliation_file.py`,
> `db/models/notification.py` e as migrations), **não** contra a memória da
> conversa. Ver `learnings.md` (entrada de 28/07/2026).

## ADR-004 — Instrumentação de outcome: tabela `usage_events` própria, fail-soft e idempotente no banco (Sprint 4 / BACK 04.1)

**Data:** 2026-07-24 · **Status:** ativo · **Escopo:** `apps/api/app/modules/usage_events/`, `app/db/models/usage_event.py`, migration `a3c7e1f95d24`

**Contexto.** A métrica de outcome da sprint (`autor_navegou_fora` ÷ conciliações
criadas, leitura em D+30) precisa de agregação, e `grep track|capture|analytics`
no repo é vazio — não há backend de eventos.

**Decisão.**
1. **Sink próprio** (`usage_events`: `event`, `session_id`, `props` jsonb,
   `created_at`), não um SaaS de analytics: a métrica precisa de SQL em D+30 e o
   PRD proíbe PII (só IDs/enums).
2. **Idempotência mora no banco** — UNIQUE parcial `uq_usage_events_event_session`
   `(event, session_id) WHERE session_id IS NOT NULL` + `ON CONFLICT DO NOTHING`.
   Uma checagem prévia em Python perderia a corrida entre dois requests. Reenvio
   responde `201` com `recorded=false`.
   ⚠️ **O `index_where` do `on_conflict_do_nothing` NÃO é decorativo:** o índice é
   PARCIAL e o Postgres **não** infere índice parcial a partir das colunas — sem
   repetir o predicado, o INSERT morre com `42P10` e o fail-soft engole o erro,
   gravando NADA (foi exatamente a reprovação do QA no retrabalho 1).
3. **Fail-soft com SAVEPOINT** (`begin_nested()`): um erro de instrumentação não
   pode abortar a transação que cria a conciliação. Coberto por teste que executa
   `SELECT 1/0` dentro do savepoint.
4. **O endpoint público NÃO aceita os eventos de backend** (`conciliacao_criada`,
   `conciliacao_concluida`): são o denominador da métrica; aceitá-los do cliente
   permitiria forjar o resultado da sprint.
5. **403 (não 404)** para manager de outra carteira no `POST /usage-events` — é o
   que o critério de aceite pede, e o endpoint não expõe dado do cliente.
6. **`status` do `conciliacao_concluida` é lido do banco**, não deduzido do caminho
   do job (sessão cancelada termina em `error` mesmo no caminho "feliz").

**Consequência.** Nenhum evento do `## Outcome & verificação` fica órfão de
emissor; a leitura D+30 é um `GROUP BY` sobre uma tabela só.

## ADR-005 — O hash muda de nível: `reconciliation_files` 1—N e uma conciliação = conta + mês (Sprint 4 / BACK 04.2)

**Data:** 2026-07-24 · **Status:** ativo · **Escopo:** `app/db/models/reconciliation_file.py`, `modules/reconciliations/`, migration `b8e2d4a71f36`

**Contexto.** Até a Sprint 3 o hash morava na sessão
(`reconciliation_sessions.file_hash NOT NULL`, `UNIQUE(client_id, omie_conta_id,
reference_month, file_hash)`): dois arquivos diferentes criavam DUAS sessões e
uma fatura quebrada em 3 PDFs não tinha resumo consolidado.

**Decisão.**
- **Nova tabela `reconciliation_files`** (1—N por sessão) com o `file_hash` **por
  arquivo**; sessão passa a `UNIQUE(client_id, omie_conta_id, reference_month)`
  (parcial, `deleted_at IS NULL`) e duplicata de arquivo a
  `UNIQUE(session_id, file_hash)`.
- **`filename` é CIFRADO** (mesmo envelope + AAD das descrições): nome de arquivo
  é texto livre digitado por gente e costuma carregar razão social ("Extrato
  Austral Junho.pdf") — CLAUDE.md §4.5. Nullable porque as linhas do backfill não
  têm nome guardado em lugar nenhum (a UI mostra "Arquivo N").
- **Parte que falhou na extração é REGISTRADA** (`status='error'` + código
  canônico, sem linhas). Sem isso, um upload de 3 PDFs em que o 2º falha vira uma
  conciliação silenciosamente incompleta.
- **`file_entries.file_id`** (FK `ON DELETE CASCADE`) torna a remoção de uma parte
  cirúrgica. Remover a **última** parte com linhas é 409 (para descartar tudo,
  exclua a conciliação) — é o que mantém o downgrade possível.
- **Cruzamento Omie roda UMA vez** por criação/anexo, e o **período da sessão
  cobre todas as partes**: pegar só o período do 1º arquivo estreitaria a janela
  Omie e jogaria linhas legítimas em `sem_omie`.
- **A migration ABORTA com mensagem acionável** se houver 2+ conciliações ativas
  para a mesma (cliente, conta, mês) — escolher qual sessão sobrevive é decisão de
  dado, não de migration.

**Consequência / pré-requisito de deploy.** Rodar antes de migrar dev/prod:
`SELECT client_id, omie_conta_id, reference_month, count(*) FROM
reconciliation_sessions WHERE deleted_at IS NULL GROUP BY 1,2,3 HAVING count(*)>1;`
— se retornar linhas, consolidar/soft-deletar antes. O **downgrade** reverte o
schema, mas sessão com mais de uma parte não tem representação no modelo antigo
(preserva a 1ª, as demais somem): não usar como rollback rotineiro.

## ADR-006 — `totals.py` é a fonte única dos totalizadores (Sprint 4 / BACK 04.3)

**Data:** 2026-07-25 · **Status:** ativo · **Escopo:** `app/modules/reconciliations/totals.py`

**Contexto.** O mesmo número ("quantos estão conciliados") aparecia em três telas
— lista, detalhe e abas de revisão — cada uma obtendo do seu jeito. É o learning
"valor derivado calculado em 2 lugares diverge", e **já estava divergindo**: o
`recompute_file_entry_counters` da revisão contava só `situation='conciliado'`,
deixando `conciliado_data_divergente` (FASE 1) de fora e atualizando 2 das 5
colunas — bastava o analista tocar em UMA linha para o `conciliated_count` da
lista cair sozinho.

**Decisão.** A regra vive num módulo só:
- **conciliado** = `situation ∈ CONCILIATED_SITUATIONS` (exato **ou** com data
  divergente — ambos casaram com o Omie por valor, §5.2);
- **sem Omie** = `situation='sem_omie'`; **Omie sem arquivo** =
  `reconciliation_omie_entries`; **anomalias** = `reconciliation_anomalies`.

Consumidores: **detalhe** → `compute_session_counters` deriva das linhas (sempre
fresco, bate com as abas) · **revisão** → `refresh_session_counters` deriva **e**
materializa as 5 colunas · **lista** → lê as colunas (não paga 3 COUNTs por item
paginado — guardrail do PRD). Como as colunas só são escritas por essa função,
lista e detalhe não divergem.

**Saldos não foram tocados:** `compute_balances` já roda uma vez no fim do
processamento e persiste; detalhe e export leem as mesmas colunas. Recalcular na
leitura criaria justamente a segunda fonte que a task manda eliminar.

## ADR-007 — Notificação in-app: só o autor, sem PII por construção, sem dedup por sessão (Sprint 4 / BACK 04.4)

**Data:** 2026-07-25 · **Status:** ativo · **Escopo:** `app/db/models/notification.py`, `app/modules/notifications/`, migration `c4f1a8b62e93`

**Contexto.** "A pessoa sai dessa tela — como é que ela sabe que acabou?"
(reunião 07/07). Ao a sessão atingir `reviewing` ou `error`, o autor precisa ser
avisado; o sino do header faz poll de `/notifications/unread-count` (15 s).

**Decisão.**
- **Só o AUTOR é notificado.** Notificar os gerentes da carteira é explicitamente
  *opcional* no PRD e ficou de fora para o sino não virar ruído antes de a
  suposição S-2 ser testada (sino ruidoso = sino ignorado = o defeito P7 de novo).
- **Sem PII por construção:** o conteúdo são COLUNAS TIPADAS (`omie_conta_id`,
  `reference_month`, tipo, `error_code`). Não existe campo de texto livre onde a
  descrição de um lançamento ou uma razão social caberia; a frase é montada no
  front. **Sem FK** (mesma decisão de `access_audit`/`usage_events`): a trilha é
  append-only e independe do ciclo de vida das linhas que referencia.
- **`unread-count` é barato por construção:** índice PARCIAL
  `ix_notifications_user_unread` (`WHERE read_at IS NULL`) — não cresce com o
  histórico já lido.
- **RBAC em duas camadas, dentro do `WHERE`:** `user_id = eu` **e** cliente na
  carteira. A 2ª cobre carteira reatribuída — o aviso antigo para de aparecer
  para o manager anterior, que senão seguiria vendo conta+mês de um cliente que
  já não é dele.
- **Sem dedup por (sessão, tipo):** um `/reprocess` que falha de novo **deve**
  avisar de novo. (Diferente do `conciliacao_concluida` em `usage_events`, que é
  métrica e conta sessões, não execuções.)
- **Notificação e evento saem da MESMA leitura** do estado terminal
  (`_settle_terminal_side_effects`) — é o que faz o aviso dizer "Erro" quando o
  usuário cancelou no meio, em vez de "Processada".
- **`reconciliation_sessions.error_code`** (novo, exposto no detalhe e na lista) é
  o código que a tela de erro mostra ("cód. X"); `error_message` continua sendo a
  frase PT-BR. Dois códigos novos: `RECONCILIATION_TIMEOUT` e
  `RECONCILIATION_CANCELLED`.

---

> ⚠️ **Nota de contexto (27/07/2026, rodada 4):** as ADRs abaixo foram **reescritas a
> partir do `HANDOFF.md`**, porque `.claude/memory/` está no `.gitignore`
> (`.gitignore:69`) e é re-semeado a cada run — este arquivo chegou de novo só com o
> ADR-000. **`HANDOFF.md` é a fonte durável das decisões de infra**, não este arquivo.
> Task do QA aberta sobre isso: *"[QA] Memória dos agents (.claude/memory) é APAGADA a
> cada run"*. As decisões abaixo estão verificadas contra a árvore commitada
> (`git diff main...HEAD -- .github scripts` → `ci.yml` +157, `scripts/a11y-gate.sh` +125).

---

## ADR-004-INFRA — Gate de a11y NÃO entra no CI na Sprint 4 (26/07/2026)

**status:** superseded-by ADR-005-INFRA

**Contexto:** o DoD da Sprint 4 exige "axe-core via Playwright, 0 violações
critical/serious", e as deps (`@axe-core/playwright`, `@playwright/test`, `axe-core`) +
`apps/web/playwright.config.ts` + `apps/web/e2e/a11y.spec.ts` passaram a existir no
`sprint-04/frontend`.

**Decisão:** não ligar o job de a11y no `ci.yml` nesta sprint, por três bloqueios
verificados: (1) `e2e/a11y.spec.ts` exige a stack inteira (Postgres + migrate + seed +
API + web) e as vars `E2E_PASSWORD`/`E2E_CLIENT_ID` — sem elas faz `test.skip`, e um job
ingênuo ficaria **verde sem medir nada**; (2) ficaria vermelho hoje (lockfile
dessincronizado + violação `critical/label` em `/login`); (3) não havia task de infra.

**Encaminhamento:** task própria com escopo real (`services: postgres`, migrate+seed,
subir API e web, `playwright install chromium`, derivar `E2E_CLIENT_ID` do seed).

---

## ADR-005-INFRA — Job `web_a11y` no CI sobre o harness MOCKADO (26/07/2026)

**status:** aceita · **supersede:** ADR-004-INFRA · **task:** `86e2gjgf2` (`done`)

**Contexto:** o QA escreveu `apps/web/e2e/a11y-mocked.spec.ts`, que intercepta a API **no
browser** (`page.route`) e semeia o cookie `access_token` — o `src/middleware.ts` só olha
a presença do cookie. Isso remove o bloqueio (1) da ADR-004: a suíte roda contra
`next build` + servidor standalone **sem Postgres, seed ou credenciais**.

**Decisão:** job `web_a11y` ("Web (a11y · axe-core)") em `.github/workflows/ci.yml:169`,
com espelho local 1:1 em `scripts/a11y-gate.sh`. Fluxo: `pnpm install --frozen-lockfile`
→ confere que o spec existe → cache de browsers → `playwright install --with-deps
chromium` → `next build` → copia `.next/static` (+ `public/`) → sobe `server.js` em
`127.0.0.1:3100` → roda **só** `e2e/a11y-mocked.spec.ts` → guard anti-verde-falso →
artefato do relatório na falha. Entra no `needs` e no resumo do job `ci`
(`ci.yml:314,323`).

**Três escolhas que valem review:**
1. **Aponta para UM arquivo** (`A11Y_SPEC=e2e/a11y-mocked.spec.ts`, `ci.yml:177`), não
   para o `testDir` — `a11y.spec.ts` faria `test.skip` e devolveria verde sem medir.
   Ela continua existindo como suíte opcional de ambiente completo.
2. **Guard `Assert the gate actually ran`** (`ci.yml:266`): lê o reporter `json` e reprova
   se `stats.expected == 0` **ou** `stats.skipped > 0`.
3. **`ubuntu-latest` + `--with-deps`**, não a imagem `mcr.microsoft.com/playwright`:
   `^1.48.0` pode resolver para 1.5x com revisão de browser diferente da pré-instalada.

**Consequência (pré-condições de merge):** o job reprova sem `e2e/a11y-mocked.spec.ts`
commitado, sem `playwright.config.ts` + as 3 deps do `sprint-04/frontend`, e sem o
`pnpm-lock.yaml` da raiz atualizado. Isso é o gate funcionando.

---

## ADR-006-INFRA — Gate de a11y roda com `--retries=0` e reprova em `flaky` (26/07/2026)

**status:** aceita · **task:** `86e2gjp8t` (`done`)

**Contexto:** o guard da ADR-005 cobria `expected == 0` e `skipped > 0`, mas não `flaky`.
Com `retries: process.env.CI ? 1 : 0` no `apps/web/playwright.config.ts`, uma violação que
aparece na 1ª tentativa e some no retry vira **flaky** — e o Playwright sai **0**, o guard
também: gate verde com violação `serious` real medida. Não é teórico: parte das violações
de 25/07 são de estado de carregamento (`aria-prohibited-attr` nos skeletons), exatamente
o perfil intermitente.

**Decisão:** as duas correções que a task recomendou, nos dois arquivos:
1. **`--retries=0 --trace=retain-on-failure`** no comando (`ci.yml:262`;
   `a11y-gate.sh:94`) — causa raiz, sobrescreve o config sem tocá-lo (`apps/web/**` é
   escopo do frontend). A11y é determinístico: se flakeia, o defeito é do spec.
2. **`stats.flaky > 0` → `exit 1`** no guard (`ci.yml:290`; `a11y-gate.sh:119`) — rede: se
   alguém reintroduzir retry, o job continua vermelho.

O `--trace=retain-on-failure` é consequência do (1): o config só gera trace
`on-first-retry`, então sem retry o artefato de falha viria **sem trace**.

**Verificação:** projeto Playwright temporário com o mesmo `retries` e um teste que falha
na 1ª tentativa e passa na 2ª (sem fixture `page`, logo sem browser): com o `retries` do
config → Playwright **exit 0**, `flaky=1`, guard exit 0 (falso verde); com `--retries=0` →
Playwright **exit 1**; relatório flaky × guard novo → exit 1; suíte saudável → exit 0.

**Limite honesto:** a 1ª execução do gate **com browser real** acontece no próprio job —
não há Chromium executável neste worktree (Docker fora do ar, `--with-deps` exige root,
`libnspr4.so` faltando).

> **Nota do QA (27/07/2026):** este "limite honesto" **deixou de valer**. O job
> `web_a11y` foi reproduzido inteiro pelo QA em `mcr.microsoft.com/playwright:v1.59.1-noble`
> contra a árvore commitada de `eb1d713` + os specs do QA: `Check a11y spec exists` ok →
> `next build` exit 0 → servidor standalone respondendo → **30 passed** →
> guard `expected=30 unexpected=0 skipped=0 flaky=0`. Ver ADR-009-QA.

---

## ADR-004-FE-A11Y-SELECT — Seleção do candidato Omie por `<input type="radio">` nativo, não `role="listbox"` (Sprint 4 / FOLLOW-UP `86e2gy1n0`)

**Data:** 2026-07-27 · **Status:** ativo · **Escopo:** `apps/web/src/components/features/reconciliations/review/trocar-lancamento-modal.tsx` · **Commit:** `d2bc76b`

> Origem: decisão do `agent-frontend`, consolidada aqui pelo QA (single-writer).
> Reconferida contra o arquivo commitado — `type="radio"` em `:205`, `name={radioGroupName}`
> em `:206`, `onKeyDown` em `:214`, docstring do módulo em `:16-26`. Não escrita de memória.

**Contexto.** A escolha do lançamento Omie era um "radio implícito": `onClick` no
`<tr>` + `aria-selected`. Dois defeitos: (1) WCAG 2.1.1 — `<tr>` não é focável,
logo a seleção não existia por teclado; (2) `aria-selected` é proibido em
`role="row"` fora de `grid`/`treegrid`.

**Decisão.** `<input type="radio">` **nativo** na 1ª coluna (`name` escopado por
`entry.id`, `aria-label` com data · descrição · valor), `aria-selected` removido
do `<tr>`, clique na linha mantido como conveniência de mouse. Handler próprio
para `Enter`: o radio nativo responde a Setas/Espaço, mas não a `Enter` fora de
um `<form>` — e `Enter` é a tecla que a pessoa tenta primeiro.

**Desvio da sugestão da task, aceito pelo QA.** A task propunha
`role="listbox"`/`role="option"`. `option` precisa ser *owned* por um `listbox`, e
entre o container e o `<tr>` existem `<table>`/`<tbody>` → violação
`aria-required-children`; só sairia com `role="presentation"` na tabela inteira,
destruindo a semântica tabular. O critério era "operável só por teclado" — o
caminho escolhido atende e é mais simples.

**Consequência (regra que ficou).** Esta classe de defeito **o axe não pega**:
`aria-selected` fora de contexto passa, e "não dá para selecionar por teclado" não
é regra do axe. A trava é comportamental (`vitest`, 8 testes) — a11y automatizada
cobre o markup, não a operabilidade.

---

## ADR-008-QA — Teste novo só conta com executor identificado + prova de vermelho (Sprint 4)

**Data:** 2026-07-27 · **Status:** ativo · **Escopo:** `.claude/agents/qa.md`, `apps/web/e2e/a11y-mocked.spec.ts`

**Contexto.** Três ocorrências da mesma classe numa sprint só: gate de a11y
só-desktop (não media o defeito mobile); spec fora do `gitPaths` do QA (não entra
no commit que o CI mede); cenário de teclado acrescentado a `e2e/a11y.spec.ts`,
que o CI **não roda** (`A11Y_SPEC` fixa `e2e/a11y-mocked.spec.ts`, `ci.yml:177`).
Em todos, o teste existia e ninguém o executava.

**Decisão.** Um teste novo só conta como entrega quando **(a)** mora num arquivo
que alguma esteira executa — e o autor **cita o job** que o roda — e **(b)** já
foi visto **vermelho** contra o código defeituoso (mutação). Se o executor não
existir, o deliverable é o teste **+** o executor.

**Consequência.** O QA valida entrega de teste por mutação, não por leitura. Nesta
sprint isso reprovou/corrigiu, entre outros: o `vitest` do modal (6 de 8 falham
contra `b43f0f5`; os 2 que passam são exatamente os puramente-axe, o que confirma
a docstring do arquivo) e o gate de lockfile (ADR-009-QA).

---

## ADR-009-QA — Job de CI se valida REPRODUZINDO o job, não lendo o YAML (Sprint 4)

**Data:** 2026-07-27 · **Status:** ativo · **Escopo:** `.claude/agents/qa.md` (DoD de infra)

**Contexto.** A Sprint 4 acumulou dois vermelhos de CI que **nenhuma leitura de
YAML pegaria**: `pnpm install --frozen-lockfile` abortando por lockfile fora do
commit, e `test -f apps/web/$A11Y_SPEC` falhando por spec não versionado. Os dois
só aparecem contra a **árvore commitada**, não contra o worktree (onde o arquivo
existe como untracked).

**Decisão.** Antes de aprovar entrega de CI, o QA reproduz o job em container,
sempre a partir de `git archive <commit>` — nunca do worktree:

| Job | Como reproduzir | Resultado (27/07, `eb1d713`) |
| --- | --- | --- |
| `web` (lint·type·test) | `node:20-slim` + `pnpm@9.12.0` → `install --frozen-lockfile`, `lint:web`, `type-check:web`, `test:web` | ✅ 0 · 0 · **189 testes / 22 arquivos** |
| `web_a11y` | `mcr.microsoft.com/playwright:v1.59.1-noble` → `Check a11y spec exists` → `next build` → assets standalone → `server.js:3100` → `playwright test --retries=0` → guard `json` | ✅ **30 passed**, `expected=30 unexpected=0 skipped=0 flaky=0` |

**Mutação obrigatória junto:** o mesmo comando contra o commit **anterior** tem
de falhar. `d2bc76b` → `ERR_PNPM_OUTDATED_LOCKFILE`, exit 1; `eb1d713` → exit 0.
Sem o negativo, "passou" não distingue "o gate funciona" de "o gate não mede nada".

**Consequência.** `git archive <commit> | tar -x` vira o primeiro passo de toda
revisão de CI — é o que expõe untracked que o worktree esconde.
