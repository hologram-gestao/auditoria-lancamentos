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

---

# Sprint 5 — Multi-tenancy e usuários por cliente (consolidado pelo QA em 30/07/2026)

> Envelope por agent. Texto preservado como o autor escreveu; o QA só consolida
> (single-writer) e acrescenta o próprio bloco no fim.

<!-- ===== agent-backend ===== -->

---

## ADR-008 — Tenancy por extensão de `users`: `scope` + `client_id` com CHECK no banco (Sprint 5 / BACK 05.1)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/app/db/models/user.py`, migration `d5c81a4e9b27`, `apps/api/app/modules/users/schemas.py`

**Contexto.** `users` só distinguia `admin`/`manager` (equipe Hologram); o escopo
do `manager` vinha de `client_assignments`. Não havia noção de "usuário que
pertence a um cliente". O PRD fechou a decisão: **estender** a tabela, não criar
uma segunda (duplicar dobra a superfície de bug de sessão).

**Decisão.**
1. `scope` (`system|client`, NOT NULL, `server_default 'system'`) + `client_id`
   (FK `clients`, `ON DELETE RESTRICT`, nulável, indexada). Backfill idempotente
   (convergente, não incremental) marca todo usuário existente como `system`.
2. **A integridade mora no banco**: CHECK `ck_users_scope_client_id` garante
   `client ⇒ client_id NOT NULL` e `system ⇒ client_id NULL`. Validação de
   aplicação não substitui — o critério da sprint exige rejeição pelo Postgres.
3. **Enum é fonte única.** `SystemUserRole`/`ClientUserRole` são whitelists que
   REFERENCIAM `UserRole.X.value` (nunca redigitam a string), e
   `SYSTEM_ROLES`/`CLIENT_ROLES` derivam delas. Teste unitário garante que todo
   papel novo caia numa das duas — senão a matriz de permissões nasce incompleta.
4. **Whitelist no request de usuários do SISTEMA.** Ampliar `UserRole` abriu um
   buraco silencioso: `POST /api/v1/users` passaria a aceitar `client_manager`
   com `scope='system'`, estado que a CHECK **não** pega (ela só cruza `scope`
   com `client_id`). O request passou a ser `SystemUserRole`.

**Landmines verificados contra banco real (não presumidos).**
- **Ciclo de FK.** `users.client_id → clients.id` fecha ciclo com
  `clients.created_by → users.id`. Sem `use_alter=True` + nome explícito, o
  `Base.metadata.create_all` dos testes não ordena as tabelas.
- **A NAMING_CONVENTION do `Base` vale DENTRO da migration.** O `alembic/env.py`
  passa `target_metadata`, então `create_check_constraint`/`drop_constraint`
  também expandem `ck_%(table_name)s_%(constraint_name)s`. Passar o nome já
  prefixado gera `ck_users_ck_users_scope_client_id` — e o `downgrade` quebra
  procurando um nome que não existe. Passe o **label**, não o nome final.

**Consequência.** O tenant do usuário passa a ser uma coluna da MESMA linha que
`get_current_user` já lê para checar `active` — base para a 05.3 decidir acesso
sem query extra e sem valor stale.

## ADR-009 — Escape hatch `TEST_DATABASE_URL` no conftest (Sprint 5)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/tests/conftest.py`

**Contexto.** O sandbox do agent não alcança o socket do Docker nem TCP local a
partir do processo Python (`PermissionError` ao criar `AF_UNIX`;
`Connection refused` em `127.0.0.1`). Resultado: **todo** teste de integração era
pulado — inaceitável numa sprint cuja entrega É teste de isolamento.

**Decisão.** Se `TEST_DATABASE_URL` estiver setada, `pg_container`/`db_url` usam
esse Postgres (descartável) em vez de subir um testcontainer. A variável **não
existe no CI**, então lá nada muda. Com ela, dá para rodar o pytest dentro de um
container `--network host` apontando para um Postgres publicado no host.

**Consequência.** O gate de integração volta a ser executável (e verificável) de
dentro do sandbox — sem mascarar falha com skip.

## ADR-010 — Negação cross-tenant tem UM caminho de gravação; `denied` não vira 4ª ação (Sprint 5 / BACK 05.2)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/app/core/audit.py`, `app/core/telemetry.py`, `app/db/models/access_audit.py`, migration `e9a4b71c3d68`

**Contexto.** A `access_audit` (S3) só sabia o tenant ALVO. Com tenants, "negaram
acesso ao cliente B" não responde "quem pediu" — a negação cross-tenant fica cega.

**Decisão.**
1. `user_scope` + `actor_client_id` na linha (sem FK, coerente com o log
   append-only da S3). Backfill convergente marca o histórico como `system`.
2. **`denied` continua sendo a ação.** Cross-tenant é PROPRIEDADE do ator, já
   derivável: `user_scope='client' AND actor_client_id IS DISTINCT FROM client_id`.
   Uma 4ª ação partiria as consultas da S3 em duas e exigiria backfill de
   reclassificação — custo sem ganho.
3. **`record_cross_tenant_denied` é o caminho único**: emite
   `acesso_cross_tenant_negado` (S5, 4 props do PRD) **e** `acesso_negado` (S3 —
   removê-lo zeraria a métrica da sprint anterior) **e** grava 1 linha com
   `commit=True`. Três coisas que só fazem sentido juntas ⇒ uma função, não três
   chamadas por call site.
4. `record_access` exige `user_scope`/`actor_client_id` **sem default**. Default
   faria um call site novo gravar `system` em silêncio — trilha que mente é pior
   que trilha ausente.

**Landmine encontrado.** `review/routes.py` remontava um `CurrentUser` sintético
a partir de `id`+`role` (email/name vazios) só para reusar `require_client_access`.
Com tenancy isso zera `scope`/`client_id`: todo acesso de usuário de cliente
seria auditado — e autorizado — como `system`. **Objeto de identidade nunca se
remonta parcialmente; propaga-se.** Corrigido nos 9 call sites.

**Consequência.** `CurrentUser` passou a carregar `scope`/`client_id` vindos da
MESMA linha que já checa `active` — sem query nova, sem valor stale. É a base que
a 05.3 usa para `resolve_client_access`.

## ADR-011 — `app/core/authz.py`: a decisão de acesso é pura, o efeito colateral é do guard (Sprint 5 / BACK 05.3)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/app/core/authz.py`, `app/core/dependencies.py`, `app/core/security.py`, `app/modules/auth/`

**Contexto.** A sprint exige UMA função de autorização consultada pela rota **e**
pela camada de dados. `dependencies.py` não servia: ele grava auditoria e levanta
HTTP, coisas que um `SELECT` de repositório não pode fazer.

**Decisão.**
1. **Módulo novo `core/authz.py`** com `CurrentUser` + a regra. `dependencies.py`
   importa dele (nunca o contrário) — sem ciclo, e a decisão fica testável sem
   subir FastAPI.
2. **`resolve_client_access` é PURA** (retorna `bool`). Quem nega é o guard
   (`deny_client_access`: 1 linha `denied` + 403). Assim a camada de dados usa a
   mesma função sem auditar cada leitura.
3. **`tenant_filter_client_id`** projeta a MESMA regra em `WHERE` — não é segunda
   implementação, é a mesma decisão em outra forma.
4. **Matriz declarativa** `PERMISSION_MATRIX: dict[Permission, frozenset[UserRole]]`
   + `require_permission(...)` como fábrica de dependency. Zero `if role ==` em
   rota. Teste parametrizado cobre as 24 células, e um teste garante que
   `set(PERMISSION_MATRIX) == set(Permission)` (permissão nova sem célula = KeyError
   em produção).

**Landmine de deploy.** `scope`/`client_id` no `TokenPayload` **precisam de
default**. Claim novo obrigatório invalida todo token já emitido: no instante do
deploy, `model_validate` falharia e **toda** sessão viraria 401. Claim novo em
JWT é sempre aditivo com default.

**Mudança de comportamento declarada.** A matriz do PRD marca ❌ em "editar dados
do cliente" para o `manager` de sistema — antes ele editava os clientes da
carteira. Implementado como o spec manda, **sinalizado no HANDOFF** para
confirmação do stakeholder.

**Consequência.** O guardrail de performance é medido, não afirmado: um teste
conta as sentenças SQL de um request e prova 1 único `SELECT FROM users` e zero
consultas a `client_assignments` para usuário de cliente.

## ADR-012 — Filtro de tenant no SELECT + lista canônica como denominador (Sprint 5 / BACK 05.4)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/app/core/authz.py` (`scoped_by_tenant`), `app/modules/reconciliations/tenant_scope.py`, `app/core/sensitive_endpoints.py`

**Contexto.** Negar na rota é necessário e insuficiente: endpoint novo que
esqueça o guard vazaria. E "100% dos endpoints sensíveis cobertos" não significa
nada sem um denominador escrito.

**Decisão.**
1. **`scoped_by_tenant(stmt, coluna, user)`** projeta a decisão do `authz` em
   `WHERE`. Toda rota com `session_id` passa por UM loader
   (`require_session_access`) cujo `SELECT` já leva `AND client_id = <tenant>`.
2. **Lista canônica em CÓDIGO** (`SENSITIVE_ENDPOINTS`) + a contraparte
   `NON_TENANT_ENDPOINTS`, com a página `docs/endpoints-sensiveis-sprint5.md`
   **gerada** a partir dela. Um teste confronta a lista com `app.routes` e falha
   quando (a) a lista tem endpoint que não existe, (b) existe rota `/api/v1` sem
   classificação, (c) aparece rota com `{client_id}`/`{session_id}` fora da lista.
   Lista que não quebra o CI vira documento morto em duas sprints.
3. **Caso negativo parametrizado** sobre a lista inteira, com **body válido** —
   um 422 de validação passaria sem nunca chegar na autorização e a cobertura
   seria falsa.

**Landmine (achado pela suíte, não por revisão).** O filtro na query **apagou a
auditoria**: com a sessão alheia virando "inexistente", o fluxo nunca chegava a
`require_client_access` e a linha `denied` sumia — R3 desligou R6 em silêncio.
Correção: `audit_session_tenant_miss` faz UMA consulta no caminho de falha; se o
recurso existe em outro tenant, grava a negação e devolve o mesmo 404.
**Sempre que um filtro passa a matar uma busca, confira o que MORREU junto com
ela** (auditoria, métrica, notificação).

**Consequência.** Cobertura medida, não afirmada: 28/34 endpoints com caso
negativo verde nesta task; os 6 restantes (usuários do cliente) fecham na 05.5.

## ADR-013 — Usuários do cliente: 3 travas, e o `AND client_id` mora no SELECT (Sprint 5 / BACK 05.5)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/app/modules/users/` (client_routes/service/repository/schemas)

**Contexto.** O `UserService` da §8 operava só por `user_id`, **sem** noção de
tenant. Expor CRUD ao gerente do cliente em cima disso é IDOR pronto: bastaria
forjar o `user_id` para editar/desativar usuário de outro tenant — ou um admin
do sistema.

**Decisão.**
1. **Estender, não duplicar.** O router novo (`client_routes.py`) só muda o
   PREFIXO; a lógica reusa `UserService`/`UserRepository`.
2. **Três travas, todas necessárias:** matriz (`ManageClientUsersDep`) → tenant
   (`AccessibleClientDep`, a função única do 05.3) → **anti-IDOR**
   (`get_by_id_in_tenant`: `AND client_id = :tenant` **no SELECT**, não numa
   comparação depois de carregar). Alvo alheio não retorna linha; 404 antes de
   qualquer escrita.
3. **Anti-escalação por AUSÊNCIA, não por checagem.** `role` é `ClientUserRole`
   (422 automático) e `client_id`/`scope` **não existem** no schema de entrada,
   com `extra="forbid"` — enviá-los é 4xx, não "ignorado em silêncio". Campo que
   não existe não tem como ser validado errado.
4. **Senha mínima de 10 no schema**, porque `hash_password` só trunca em 72
   bytes e nunca impôs mínimo.

**Detalhe que evita virar oráculo.** `users.email` é único GLOBALMENTE. Colisão
com e-mail de OUTRO tenant devolve 409 com mensagem genérica — dizer de quem é
o e-mail transformaria a criação num enumerador cross-tenant.

**Divergência declarada.** A descrição da task dizia "manager do sistema segue a
carteira"; a matriz do PRD §4 marca ❌ para ele em "gerir usuários do cliente".
Segui a **matriz** (spec explícita) e sinalizei no HANDOFF para confirmação —
reverter é uma linha em `PERMISSION_MATRIX` + o teste.

<!-- ===== agent-frontend ===== -->

---

## ADR-005-FE — Gating de UI numa função só (`lib/authz.ts`), indexada por PAPEL (Sprint 5 / FRONT 05.6)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/web/src/lib/authz.ts`

**Contexto.** A matriz do R4 (PRD §4) tem 6 ações × 4 papéis. O padrão que existia no
front era `user.role === 'admin'` espalhado (`configuracoes/usuarios/page.tsx:64`,
`edit-client-modal.tsx:77`, `(app)/layout.tsx:171`) — e a task proíbe explicitamente
("grep confirma ausência de checagem de papel espalhada por componente").

**Decisão.** Uma função (`hasPermission(user, permission)`) sobre um
`PERMISSION_MATRIX` **indexado por papel**, não por permissão:
`Record<UserRole, readonly Permission[]>`, com `UserRole` vindo do **contrato gerado**.
Assim um papel novo no backend **não compila** até alguém decidir o que ele enxerga —
o inverso (indexar por permissão) deixaria o papel novo passar silenciosamente com
zero permissões, que é o mesmo bug com cara de segurança.

`canAccessClient` espelha `resolve_client_access` só até onde o front consegue: para
`scope='client'` a decisão é completa (é o próprio `client_id`); para `system` devolve
`true`, porque a carteira mora em `client_assignments`, que o front não conhece — quem
nega é o backend e a tela degrada pela resposta, nunca por adivinhação.

**Consequência.** Ampliar `UserRole` no contrato quebrou a compilação em 2 lugares
(`clientes/page.tsx`, `client-shell.tsx`) via a prop `currentUserRole: 'admin' |
'manager'` do `EditClientModal` — exatamente o efeito desejado. A prop foi removida em
favor de `hasPermission(user, 'edit_client')`.

**Isto não é segurança.** A autoridade é `app/core/authz.py` (decide pela LINHA do
usuário a cada request). O middleware do Next também não é barreira (CVE-2025-29927).
O que este módulo evita é o defeito de UX de mostrar botão que devolve 403.

## ADR-006-FE — `AlertDialog` construído sobre o `@radix-ui/react-dialog` instalado (Sprint 5 / FRONT 05.6)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/web/src/components/ui/alert-dialog.tsx`

**Contexto.** O design-system manda confirmação destrutiva em `AlertDialog` (nunca
`<div fixed inset-0>` manual). `@radix-ui/react-alert-dialog` não está instalado, e
adicioná-lo altera o `pnpm-lock.yaml` da **raiz** — fora do `AGENT_PATHS_FRONTEND`
(`apps/web/`). Lockfile fora do commit = `ERR_PNPM_OUTDATED_LOCKFILE` no CI, que é o
vermelho que a Sprint 4 já pagou (ADR-009-QA).

**Decisão.** Implementar a semântica de alertdialog sobre o `@radix-ui/react-dialog`
já presente: `role="alertdialog"` (o Radix aplica `...contentProps` **depois** do seu
`role: "dialog"` — conferido em `dist/index.mjs:223-228`, não presumido), sem dismiss
por clique/foco fora, sem botão "X", foco inicial no **Cancelar** via contexto+ref
(um `data-*` não passaria pelo tipo do `Button`). Foco preso, `aria-modal`, portal e
restauração de foco continuam do Radix.

**Consequência.** Zero dependência nova, zero risco de lockfile. Se o pacote oficial
for preferido, o componente é drop-in (mesma superfície de export) — mas o `pnpm add`
tem de vir acompanhado do commit do lockfile da raiz por quem tem esse escopo.

## ADR-007-FE — Estado inativo se comunica por BADGE, nunca por `opacity` na linha (Sprint 5 / FRONT 05.6)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/web/src/components/features/client-users/`

**Contexto.** O padrão `cn(!u.active && 'opacity-60')` na `<TableRow>` foi copiado de
`configuracoes/usuarios` (S4). Contra o Chromium real (axe via Playwright, container
`mcr.microsoft.com/playwright:v1.59.1-noble`), ele derrubou **3 elementos de uma vez**
para abaixo de 4.5:1 — e-mail `2.57:1`, badge de papel `2.42:1`, badge "Inativo"
`2.81:1`: três violações `serious`. O `opacity` compõe sobre o fundo e some com o
contraste que o token garantia; nem o axe em jsdom (que desliga `color-contrast`) nem
o `theme-contrast.test.ts` (que mede TOKEN, não o composto) veriam isso.

**Decisão.** Nada de `opacity` para estado em linha de tabela. A badge "Inativo"
(`bg-destructive/10` + `text-destructive`, par já travado pelo teste de tokens) é o
sinal. Na mesma verificação: `<Table>` já traz o wrapper rolável **focável** — um
segundo `overflow-x-auto` por fora faz a tabela espremer em 390px em vez de rolar.

**Consequência / follow-up.** `apps/web/src/app/(app)/configuracoes/usuarios/page.tsx:178`
tem o MESMO `opacity-60` e não está sob nenhuma suíte de a11y — defeito real,
pré-existente, fora do escopo da FRONT 05.6/05.7.

## ADR-008-FE — Rota negada degrada com mensagem, e o "voltar" é a casa do PAPEL (Sprint 5 / FRONT 05.7)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/web/src/components/shared/access-denied.tsx`, `(app)/configuracoes/*`, `client-shell.tsx`, `(app)/clientes/page.tsx`

**Contexto.** O padrão que existia era `router.replace('/clientes')` silencioso para
quem não é admin (`configuracoes/usuarios/page.tsx`, `anomalias/anomaly-types-page.tsx`).
Com usuários DE tenant isso quebra duas vezes: (1) o destino do redirect é outra rota
que ele também não vê; (2) redirect silencioso não explica nada — a pessoa acha que
clicou errado.

**Decisão.**
- Rota sem permissão renderiza `AccessDenied`: motivo em PT-BR **sem citar o recurso
  alheio** + um caminho de volta. Nunca tela branca, nunca `error.message` do Next.
- O caminho de volta é `homePathFor(user)`: `/clientes` para a equipe Hologram,
  `/clientes/{client_id}` para usuário de tenant. Mandar todo mundo para a lista
  global daria um segundo beco sem saída.
- **Exceção deliberada — `/clientes`:** ali o usuário de tenant é *redirecionado*
  para a casa dele, não bloqueado. É o destino padrão pós-login
  (`middleware.ts:HOME_PATH`); um `AccessDenied` seria a primeira tela após entrar.
- **Cross-tenant é decidido ANTES do fetch** (`useClientDetail({enabled: canAccess})`):
  o request nem sai. Deixar o backend responder 403/404 mostraria "não foi possível
  carregar o cliente" — mensagem tecnicamente verdadeira e errada para quem lê.

**Não é segurança.** O `AccessDenied` é UX; a autoridade é o backend, e o middleware
do Next não é barreira (CVE-2025-29927). O que ele impede é o defeito de UX.

## ADR-009-FE — Gerente do SISTEMA perde "Editar cliente": mudança declarada, não regressão (Sprint 5 / FRONT 05.7)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `client-shell.tsx`, `edit-client-modal.tsx`

**Contexto.** Até a S4 o botão "Editar cliente" aparecia para admin **e** manager de
sistema (o modal só escondia a atribuição de gerente do não-admin). A matriz do PRD §4
diz "Editar dados do cliente (§9): admin ✅, gerente sistema ❌".

**Decisão.** Seguir a matriz e esconder o botão para o manager. Verificado no backend
antes de mudar (não presumido): `PATCH /api/v1/clients/{client_id}` usa `EditClientDep`
e o próprio código traz o comentário "Matriz §4 … mudança de comportamento para o
manager, declarada no PRD" (`apps/api/app/modules/clients/routes.py:281-296`). Manter o
botão só produziria 403.

**Guardrail preservado.** O `manager` não perde nada do que o PRD protege: login e
carteira seguem iguais (lista global, entrar no cliente, criar/revisar/exportar
conciliação, sincronizar contas). O que saiu é uma ação que o servidor já nega.

## ADR-010-FE — Chrome compartilhado se valida por screenshot em TODOS os perfis e viewports (Sprint 5 / FRONT 05.7)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/web/src/app/(app)/layout.tsx`, `e2e/a11y-mocked.spec.ts`

**Contexto.** A conferência dos 4 perfis × 2 viewports expôs um defeito que **nenhum
teste existente pegava e que não era da sprint**: em 390px o header transbordava e o
botão **"Sair" ficava cortado fora da viewport**. O axe não reprova isso (o elemento
existe, tem nome acessível e contraste), o vitest em jsdom não tem layout, e o
screenshot só-desktop não mostrava. É o mesmo padrão do `scrollable-region-focusable`
da S4: defeito que só existe em viewport estreito.

**Decisão.** Duas coisas, juntas:
1. Correção: título com `min-w-0 truncate`, e-mail escondido abaixo de `sm` (não é
   acionável; o papel basta para o contexto), grupo da direita `shrink-0`.
2. **Trava comportamental**, não visual: asserção de `boundingBox` no e2e —
   `x + width <= viewport.width` para o botão "Sair", nos dois viewports e nos quatro
   perfis. Mutação confirmada: com o header antigo o teste falha em `mobile 390px` e
   passa em `desktop`.

**Regra que fica.** "Existe em algum lugar" (grep) não é "cabe em todos os contextos".
Chrome compartilhado (header, nav, hambúrguer, drawers) se valida por screenshot
**aberto** em cada perfil e cada viewport — e o que a imagem revelar vira asserção,
senão volta na próxima sprint.

<!-- ===== agent-infra ===== -->

---

## ADR-010-INFRA — Sprint 5 (multi-tenancy) não demanda mudança de infra

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `.github/**`, `scripts/**`, `docker/**`

**Contexto.** `get-tasks agent-infra` e `get-failed-tasks agent-infra` retornaram
`[]` na Sprint 5: as 8 tasks do board são BACK 05.1–05.5, FRONT 05.6–05.7 e
QA 05.8. A dúvida legítima era se alguma delas embute requisito de infra implícito
(secret novo, provisionamento de 1ª vez, mudança de CI) — o padrão do papel manda
entregar `scripts/setup-*.sh` ANTES do deploy que usa o recurso.

**Decisão.** Não escrever nada. Verificado contra os arquivos reais, não por
suposição:

| Requisito potencial | Verificação | Conclusão |
| --- | --- | --- |
| Migração da S5 (colunas + CHECK + backfill) | `deploy-dev.yml:141-193` — Job `auditoria-api-migrate-dev` roda `alembic upgrade head` antes de `deploy-api`, com digest re-resolvido e secrets espelhados | coberto |
| Secret/fila/bucket novo | PRD fecha onboarding com senha inicial pelo gerente (sem e-mail/SSO) | nenhum |
| Teste de constraint em Postgres real | `ci.yml:60-75` já sobe `postgres:16-alpine` + `DATABASE_URL` em `ubuntu-latest` | coberto |

`git diff --stat origin/main -- .github/ scripts/ docker/` → vazio.

**Consequência.** Sprint sem entrega de infra é resultado válido e explícito, não
omissão. O acoplamento que sobra é operacional: migração vermelha **para** o deploy
dev (`if: needs.migrate.result == 'success'`, linha 196) e o `deploy-web` tem guard
contra subir front novo sobre API velha (linhas 365-370) — logo o custo de um
`downgrade` mal escrito na BACK 05.1 é pipeline parado, e o QA deve exigir
`upgrade → downgrade → upgrade` limpo (constraint removida antes das colunas).

<!-- ===== agent-qa ===== -->

## ADR-010-QA — Suíte de integração se roda DENTRO de container, não no sandbox (Sprint 5)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** verificação do QA

**Contexto.** O sandbox do agent QA **não alcança Postgres a partir do processo
Python** — nem via socket do Docker (`PermissionError` no testcontainers) nem via
TCP local (`Connection refused` em `127.0.0.1:<porta>`, mesmo com a porta
publicada e respondendo ao `/dev/tcp` do bash). Resultado da primeira execução:
`463 passed, 426 skipped` — a suíte inteira de isolamento cross-tenant **pulada**,
com exit code **0**. Uma sprint de fundação de segurança teria sido aprovada sem
que um único teste de tenant rodasse.

**Decisão.** O QA roda a suíte **dentro de um container**, na mesma rede do
Postgres — o `docker run` funciona (fala com o daemon), o que falha é a rede do
processo Python no sandbox:

```bash
docker network create adl-qa-net && docker network connect adl-qa-net <pg>
docker run --rm --network adl-qa-net -v <worktree>/apps/api:/work -w /work \
  -e UV_PROJECT_ENVIRONMENT=/venv -e UV_CACHE_DIR=/cache \
  -e TEST_DATABASE_URL='postgresql+psycopg://...@<pg>:5432/<db>' \
  ghcr.io/astral-sh/uv:python3.12-bookworm \
  bash -c "uv sync --all-extras && uv run pytest -q --no-cov"
```

`UV_PROJECT_ENVIRONMENT=/venv` é obrigatório: sem ele o `uv sync` reescreve o
`.venv` do worktree do executor (que aqui era Python 3.14) dentro do container.

**Regra que fica.** `skipped` em massa **não** é verde. Antes de aprovar, o QA
confere a linha de resumo do pytest: se o número de skips é da ordem da suíte de
integração, o gate não mediu nada — reproduza em container e só então dê veredito.
Resultado real desta sprint depois da correção: **884 passed, 5 skipped**
(os 5 são fixtures Omie que exigem credencial real, pré-existentes).

## ADR-011-QA — Contrato cross-branch se prova REGENERANDO, não lendo (Sprint 5)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** verificação do QA

**Contexto.** Backend e frontend commitam em branches separadas. O `gen:types` do
front (`openapi-typescript http://localhost:8000/openapi.json`) roda contra a API
que estiver de pé — e o worktree do frontend contém o `apps/api` **de develop**,
sem as mudanças da sprint. "Rodei o `gen:types`" não prova, portanto, que o
`schema.ts` commitado corresponde ao backend commitado na OUTRA branch.

**Decisão.** O QA prova por regeneração, não por leitura:

```bash
# 1. OpenAPI do worktree do BACKEND (não do front)
uv run python -c "import json;from app.main import app;json.dump(app.openapi(),open('/tmp/openapi-real.json','w'),indent=2)"
# 2. Regenera o contrato e compara com o commitado no FRONT
npx openapi-typescript /tmp/openapi-real.json -o /tmp/schema-regen.ts
diff -u /tmp/schema-regen.ts apps/web/src/lib/contracts/schema.ts   # tem de ser 0
```

Resultado da Sprint 5: **diff 0** (0 linhas) — critério "`gen:types` diff 0"
fechado com evidência, e não com a palavra do executor.

**Regra que fica.** Sprint com backend e front em branches distintas: o critério
"contrato regenerado" só é verificável **cruzando as duas árvores**. Diff ≠ 0 é
reprovação do executor que ficou para trás, com a definição real citada.

## ADR-012-QA — `scope` precede `role`: a CHECK do banco não cobre essa dupla (Sprint 5)

**Data:** 2026-07-30 · **Status:** ativo · **Escopo:** `apps/api/tests/unit/test_authz_scope_precedence.py`

**Contexto.** A CHECK `ck_users_scope_client_id` (BACK 05.1) cruza `scope` com
`client_id`, mas **não** cruza `scope` com `role`: a linha `scope='client'` +
`role='admin'` é representável no Postgres. Nenhum endpoint a produz hoje
(`SystemUserRole`/`ClientUserRole` fecham os dois requests), e por isso **nenhum
teste da sprint tinha essa combinação** — a métrica 34/34 não a alcançava.

**Decisão.** Teste do QA que trava a **ordem dos ramos** de
`resolve_client_access` (escopo primeiro, papel depois). Com a ordem invertida, a
linha acima passaria pelo ramo "admin libera tudo" e alcançaria todos os tenants.
Verificado: 5 testes verdes contra o código da 05.3, ruff + mypy limpos.

**Regra que fica.** Quando uma invariante é garantida por **dois** campos e a
constraint só cobre um par, o par descoberto vira teste — a combinação
"impossível pela API hoje" é exatamente a que um backfill ou um `UPDATE` manual
cria amanhã.
