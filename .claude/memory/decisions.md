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

---

# Sprint 6 — Glossário e classificação por cliente

<!-- ===== agent-backend ===== -->

## ADR-010 — Dedup de `usage_events` vira **allow-list por evento** (Sprint 6 / BACK 06.1)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `app/db/models/usage_event.py`,
`app/modules/usage_events/{schemas,repository,service}.py`,
`app/modules/reconciliations/qualification/service.py`, migration `a1d7f36c9b52`

**Contexto (a landmine que a task mandou resolver).** A Sprint 4 (ADR-004) pôs a
idempotência do sink no banco: UNIQUE parcial `uq_usage_events_event_session`
`(event, session_id) WHERE session_id IS NOT NULL` + `ON CONFLICT DO NOTHING`.
Isso estava **certo** para os 4 eventos daquela sprint — o grão de todos é "no
máximo 1 por sessão". Os eventos da Sprint 6 quebram essa premissa:
`qualificacao_emitida` ocorre **uma vez por veredito** (até 50 por lote de IA) e
`flag_revisado` **uma vez por flag julgado**. Sem mudar nada, a 2ª emissão em
diante seria descartada em silêncio pelo `DO NOTHING` — e a razão do outcome
(`improcedentes ÷ emitidas`) sairia errada sem nenhum sinal.

**Decisão.**

1. **O predicado do índice passa a listar quem aceita dedup**, em vez de dedupar
   tudo que tem sessão:
   `WHERE session_id IS NOT NULL AND event IN ('autor_navegou_fora',
   'conciliacao_concluida', 'conciliacao_criada', 'notificacao_entregue')`.
   **Allow-list e não deny-list**: evento novo nasce SEM dedup. O pior caso vira
   "linha a mais" (visível na leitura, corrigível) em vez de "linha que sumiu"
   (invisível, e a métrica mente). Entrar na allow-list exige migration — é uma
   decisão consciente, não um default.
2. **Uma função monta a expressão** (`deduped_session_index_predicate()`), usada
   pelo `Index` do modelo E pelo `index_where` do `ON CONFLICT` do repository. A
   migration repete a string como **snapshot congelado** (migration não importa
   código de app — learning "job de migration sem as secrets do serviço"), e um
   teste unitário compara as duas. Se divergirem, o Postgres não infere o índice
   como árbitro e o INSERT morre com `42P10` — que o fail-soft engoliria,
   gravando NADA (a reprovação de QA da Sprint 4, item 2 da ADR-004).
3. **Grão de cada evento novo, explícito:**
   - `qualificacao_emitida` — 1 linha por veredito REAL da Camada 1 (inclusive
     `ok`, que é a base honesta da razão). Par que a IA **omitiu** não gera
     evento: o caller o trata como "ok" por omissão, e contá-lo inflaria a base
     com análise que não aconteceu.
   - `flag_revisado` — 1 linha por **transição de estado da marcação**, não por
     requisição. Quem garante isso é o call site (BACK 06.5): remarcar com o
     MESMO veredito não chama o emissor (não infla o denominador), e mudar de
     procedente↔improcedente chama (a mudança não some). Os `props` são fechados
     pelo PRD e não carregam id do flag; a **marcação vigente de cada flag** mora
     na coluna da anomalia (06.5) e é a fonte para reconciliar a série em caso de
     flip.
   - `glossario_editado` — sem `session_id`, logo fora do índice parcial por
     construção; toda edição é uma linha.
4. **`session_id` é COLUNA, não chave de `props`.** O PRD escreve
   `qualificacao_emitida {session_id, veredito, com_glossario}`; a sessão entra
   pela coluna exatamente como já acontece com `conciliacao_criada` (declarado
   `{session_id, client_id, n_arquivos, criado_por}`, gravado com a sessão na
   coluna). Sem isso haveria duas fontes para o mesmo id.
5. **Emissor monta `props` pelo modelo Pydantic**, nunca por `dict` solto —
   `extra="forbid"` + só `bool`/`int`/`Literal`/`UUID` passam a valer na
   **emissão**, não só na borda HTTP. Não existe caminho pelo qual o `motivo` da
   IA, a descrição de um lançamento ou uma razão social entre no sink.
6. **Nenhum dos 3 entra em `CLIENT_EMITTED_EVENTS`** — aceitar do browser
   deixaria forjar numerador E denominador (item 4 da ADR-004).
7. **`insert_many_ignore_duplicate`**: um `INSERT ... VALUES (…), (…)` para os N
   vereditos de uma qualificação, num único SAVEPOINT. 50 round-trips dentro da
   transação do job contrariariam o guardrail "a qualificação não pode ficar mais
   lenta".
8. **`com_glossario` vem do caller.** `qualify_session` ganhou o parâmetro com
   default `False` (o glossário só nasce na BACK 06.4). O emissor não decide o
   valor — hard-codar ali tornaria o "antes/depois" da métrica inverificável.

**Downgrade.** Reversível, com guarda: o `downgrade` **aborta** com mensagem
acionável (e a consulta a rodar) se já existir par `(event, session_id)`
duplicado — recriar o índice antigo apagaria linha de métrica, e escolher qual
morre é decisão de dado, não de migration.

**Verificação (output real, 03/08/2026).** `ruff check` + `ruff format --check` +
`mypy app/` limpos; `pytest -q --no-cov` = **927 passed, 5 skipped** (baseline
antes da task: 889 passed). Round-trip `upgrade head → downgrade -1 → upgrade
head` executado de verdade contra Postgres 16.2, com as linhas preservadas.
**Mutação obrigatória:** com o predicado revertido para o da Sprint 4
(`session_id IS NOT NULL`), 4 testes ficam vermelhos —
`test_duas_qualificacoes_da_mesma_sessao_geram_duas_linhas`,
`test_flag_revisado_aceita_duas_marcacoes_na_mesma_sessao`,
`test_razao_do_outcome_e_calculavel_por_sql` e
`test_predicado_bate_com_o_snapshot_da_migration`. Os testes de contagem batem no
`UsageEventRepository` DIRETO (sem o service), senão o fail-soft transformaria um
`42P10` em "gravou 0" silencioso.

## ADR-010-INFRA — Postgres real sem Docker: binários do wheel `pgserver` (Sprint 6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** ambiente do agent, não o repo

**Contexto.** Neste worktree não há Docker (`docker info` falha) nem Postgres do
sistema, então `testcontainers` **pula** toda a suíte de integração — e os
critérios desta sprint (contagem de linhas, round-trip de migration, isolamento
cross-tenant) só valem contra Postgres de verdade.

**Decisão.** Baixar o wheel `pgserver` (cp312 manylinux x86_64) do PyPI, extrair
em `$TMPDIR` e rodar os binários direto: é **PostgreSQL 16.2**, a mesma major do
projeto. **Nenhuma dependência foi adicionada ao `apps/api`** — o wheel vive fora
da árvore e não entra em `pyproject.toml`/`uv.lock`. A suíte usa o escape hatch
que já existia no `conftest.py` (`TEST_DATABASE_URL`), sem tocar em fixture.

**Duas pegadinhas do sandbox** (custaram tempo, ficam registradas): o servidor
não cria socket Unix (`Operation not permitted`) → subir com
`-c unix_socket_directories= -c listen_addresses=127.0.0.1`; e cada chamada de
Bash é um namespace de rede próprio → o Postgres tem de subir e os testes rodarem
**no mesmo comando** (um script que dá `pg_ctl start`, roda a suíte e derruba).

**Consequência.** Toda afirmação de teste desta sprint tem output real. Se o
harness sumir, o fallback honesto é declarar "não pôde ser medido", não afirmar
verde (CLAUDE.md §6.10).

## ADR-011 — Glossário por tenant: uma tabela com `kind`, texto CIFRADO, versão no cliente (Sprint 6 / BACK 06.2)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:**
`app/db/models/client_glossary_entry.py`, `app/db/models/client.py`,
`app/core/crypto_service.py`, `app/modules/glossary/`, migration `b3e6a91d4c78`

**Contexto.** Não existia estrutura para o vocabulário contábil do cliente. O PRD
(R1) pede categorias (código/nome + uso), fornecedores típicos e regras de
auditoria, **por tenant**, com um marcador que invalide o cache do prompt da
qualificação quando o conteúdo muda.

**Decisão.**

1. **UMA tabela com discriminador `kind`** (`categoria|fornecedor|regra`), não
   três quase idênticas. Mesmo formato (`code?` + `name` + `description?`), mesmo
   dono, mesmo ciclo de vida, mesma cripto — três tabelas triplicariam migration,
   repositório e caso negativo cross-tenant sem separar nada. `kind` fica em
   CLARO (enum do sistema, não dado do cliente) e é por ele que a leitura ordena.
2. **Os três campos textuais são CIFRADOS** com a DEK do cliente (envelope
   AES-256-GCM, `field_locator(AAD_GLOSSARY_*, <pk>)`, IV novo por operação) —
   "Moinho Prado Ltda" num `name` em claro é exatamente o que o CLAUDE.md §4.5
   proíbe. Três AADs novos e congelados em `crypto_service.py`.
   **Consequência aceita:** ordenação/paginação/busca só por coluna em claro
   (`kind`, `created_at`, `id`). Não existe índice sobre ciphertext — com IV novo
   por operação, dois ciphertexts do mesmo texto são diferentes.
   Falha de decrypt vira `[indecifrável]` + `decrypt_failed=True` + warning
   `glossary_decrypt_failed` (a mesma trilha oficial de review/export), nunca
   célula vazia silenciosa. A 06.4 OMITE a entrada marcada do bloco de prompt.
3. **Versão no TENANT: `clients.glossary_version`**, contador incrementado no
   PRÓPRIO `UPDATE` (`glossary_version + 1 RETURNING`), na MESMA transação de
   qualquer escrita — criação, edição **e remoção**. `MAX(updated_at)` das
   entradas não serve: um delete não mexe no MAX e o cache seguiria servindo
   conteúdo que já não existe. **Fonte única:** só
   `ClientGlossaryRepository.bump_version` escreve esse número (learning "valor
   derivado em 2 lugares diverge"). `+1` no UPDATE, e não `read → +1 → write`,
   para duas edições concorrentes não perderem incremento.
4. **Soft delete** (`deleted_at`) — padrão do repo, DELETE físico proibido; toda
   leitura filtra `IS NULL`.
5. **Isolamento em duas camadas dentro do SELECT:** `AND client_id = <alvo>`
   sempre, mais `scoped_by_tenant(...)` quando há `CurrentUser`. Usuário de
   cliente pedindo o tenant alheio recebe lista vazia / `None` (→ 404), inclusive
   por PK. O caminho do JOB (qualificação) passa `user=None` porque não há
   request — e ali o `client_id` vem da SESSÃO, nunca de payload.
6. **Ordem determinística `(kind, created_at, id)`** na leitura. O `id` desempata
   linhas do mesmo instante (a fixture de transação única tem `NOW()` constante,
   e import em lote empata em produção): sem desempate estável, o bloco de prompt
   da 06.4 mudaria de ordem entre chamadas e o prefixo nunca cachearia.
7. **Tetos de tamanho moram no modelo** (`MAX_CODE_CHARS=40`,
   `MAX_NAME_CHARS=120`, `MAX_DESCRIPTION_CHARS=500`,
   `MAX_ENTRIES_PER_CLIENT=200`). Não são cosméticos: são o teto que impede o
   bloco de prompt de crescer sem limite (guardrail S-2/R9). A validação da 06.3
   e o truncamento da 06.4 usam ESTES números, não uma segunda cópia.
8. **`build_entry` / `apply_entry_edit` são o ÚNICO lugar que cifra o glossário.**
   O `id` é gerado antes de cifrar porque entra no AAD. A edição **recifra** em
   vez de comparar ciphertext: com IV novo por operação, "comparar para pular"
   não funciona.

**Downgrade.** Reversível (dropa tabela + coluna). Como as entradas só existem
nesta tabela, o rollback DESCARTA o glossário — é perda de feature, não corrupção
de dado pré-existente, e por isso a migration cria (não altera) a estrutura.

**Verificação (output real, 03/08/2026).** `ruff` + `ruff format --check` +
`mypy app/` limpos; `pytest -q --no-cov` = **944 passed, 5 skipped** (antes da
task: 927). 13 testes de integração novos em `test_client_glossary.py` (as três
formas, nada em claro na tabela, IV novo por operação, ciphertext de A não
decifra em B, `[indecifrável]`, versão nos 3 casos incluindo delete, isolamento
por tenant e por PK, soft delete) + 4 de round-trip em `test_migrations.py`.
`git diff` em `pyproject.toml`/`uv.lock` **vazio** — nenhuma dependência nova.

**Correção de rota em teste existente.** `test_backfill_e_idempotente` descia por
`-2` e o round-trip da 06.1 por `-1`; com migrations novas por cima, alvo
relativo aponta para outra revisão e o teste deixa de exercitar o que alega.
Passaram a referenciar **IDs de revisão** (convenção que o próprio arquivo já
documentava).

## ADR-012 — CRUD do glossário: permissão nova na matriz, teto de entradas e versão+evento num efeito colateral só (Sprint 6 / BACK 06.3)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `app/core/authz.py`,
`app/core/dependencies.py`, `app/core/exceptions.py`,
`app/core/sensitive_endpoints.py`, `app/modules/glossary/{routes,schemas,service}.py`

**Contexto.** O glossário só serve se alguém o mantém (suposição S-1). Faltavam
os endpoints, com a matriz da Sprint 5: gerente do cliente e equipe do sistema
escrevem; **operador do cliente só lê** (ele precisa do glossário como
referência na revisão).

**Decisão.**

1. **UMA permissão nova na `PERMISSION_MATRIX`: `manage_glossary`** =
   `{admin, manager, client_manager}`. Note a diferença deliberada para
   `manage_client_users` (`{admin, client_manager}`): o PRD desta sprint diz
   "admin, **e manager dentro da carteira**". O "dentro da carteira" **não** é
   uma célula da matriz — é `resolve_client_access`, e há teste com `manager`
   dentro (201) e fora (403) da carteira.
   **Leitura não pede permissão**: `CurrentUserDep` + `AccessibleClientDep`
   bastam. Nenhuma rota compara `role`/`client_id` na mão (`grep` limpo).
2. **Quatro rotas, sem detalhe por PK.** `GET`/`POST` na coleção,
   `PATCH`/`DELETE` por `entry_id`. Um `GET /{entry_id}` seria superfície
   sensível a mais para nada — a gaveta do front usa a linha da listagem.
3. **PATCH substitui os campos textuais por completo**, não é patch parcial. O
   texto é cifrado: "mudar só a descrição" exigiria decifrar o resto para
   recompor o registro, e o formulário do front já envia tudo.
4. **Teto de entradas com código canônico PRÓPRIO**
   (`GLOSSARY_LIMIT_EXCEEDED`, 400), não `VALIDATION_ERROR` genérico nem 409: o
   pedido é válido em forma e nada conflita — acabou a cota. Código próprio
   porque o front precisa distinguir "campo inválido" de "cota cheia" para
   orientar o usuário. `MAX_ENTRIES_PER_CLIENT` é a MESMA constante da 06.2 que a
   06.4 assume como teto do bloco de prompt.
5. **Validação de tamanho no schema, usando as constantes de `app.db.models`** —
   não redigitadas. `extra="forbid"` no body (um `client_id` enviado ali nunca
   decide tenant). `name` só-espaços é recusado: `min_length=1` sozinho aceitaria
   `"   "` e uma entrada em branco entraria no bloco de prompt como ruído.
   Erros de campo saem como **400 VALIDATION_ERROR** — o handler global do repo
   converte o 422 do Pydantic (a task falava em 422; a convenção do repo é 400 e
   foi mantida para não criar um segundo formato de erro).
6. **`_after_write` concentra o efeito colateral obrigatório**: bump da versão +
   `glossario_editado`, chamado pelos TRÊS verbos. Espalhado, o dia em que
   alguém esquecer no `delete` o cache do prompt serviria glossário removido e a
   S-1 deixaria de ser medida. Evento é fail-soft (SAVEPOINT): sink fora do ar
   não impede o usuário de salvar — teste explícito.
7. **`n_categorias` conta TODAS as entradas ativas do tenant**, não só as de
   `kind='categoria'`. O nome vem literal do PRD e é anterior à decisão de tabela
   única (ADR-011); o sinal útil para a S-1 é "quanto glossário existe". Está
   documentado no HANDOFF para a leitura D+30 não interpretar errado.
8. **As 4 rotas entraram em `sensitive_endpoints.py`** com `_BODIES` VÁLIDOS no
   teste parametrizado (um 400 de validação passaria sem tocar a autorização — a
   armadilha que a própria lista documenta). Denominador **34 → 38, cobertura
   38/38**; `docs/endpoints-sensiveis-sprint5.md` regenerado pelo script.

**Verificação (output real, 03/08/2026).** `ruff` + `format --check` + `mypy`
limpos; `pytest -q --no-cov` = **974 passed, 5 skipped** (antes da task: 944).
22 testes novos em `test_glossary_endpoints.py` + 4 casos cross-tenant
parametrizados + 4 células novas na matriz.

**O guard da matriz funcionou contra mim.** `test_toda_celula_da_tabela_do_prd_foi_transcrita`
ficou VERMELHO ao acrescentar `MANAGE_GLOSSARY` sem transcrever as 4 células —
exatamente o desenho da Sprint 5 (permissão nova sem caso negativo não passa).

## ADR-013 — Glossário na QUALIFICAÇÃO: 3º bloco de system, ordem fixa, teto derivado da 06.3 (Sprint 6 / BACK 06.4)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:**
`app/modules/reconciliations/qualification/{semantic,service}.py`

**Contexto.** `_analyze_batch` já montava `system_blocks` = `_SYSTEM_PROMPT`
(com `cache_control: ephemeral`) + `_INVESTMENT_RULE` condicional "pra não
invalidar o cache do `_SYSTEM_PROMPT` comum". Esse é o precedente EXATO a
estender — e a extração (`integrations/anthropic/`) não entra nesta história.

**Decisão.**

1. **3º bloco, ordem FIXA:** `_SYSTEM_PROMPT` → `_INVESTMENT_RULE` (se conta de
   aplicação) → glossário do cliente. O 1º continua sendo o prefixo comum a
   TODOS os tenants (não pode ser contaminado por conteúdo de cliente, senão o
   cache deixa de ser compartilhado). O glossário leva
   `cache_control: ephemeral`, marcando o fim do prefixo cacheável.
2. **Threading por assinatura:** `analyze_pairs`/`_analyze_batch` ganharam
   `client_id` e `glossary_block`. `grep` de `ContextVar`/variável de módulo com
   cliente/glossário nesse caminho: vazio. O bloco chega **já renderizado** —
   `semantic.py` não toca banco nem cripto.
3. **`qualify_session` resolve o glossário** (`_resolve_glossary_block`): é o
   único ponto do fluxo que tem, ao mesmo tempo, a sessão, o `client_id` e o
   `ClientCipher` do tenant. Falha de leitura **não** derruba a qualificação —
   o cliente fica sem bloco e o pipeline volta a ser o de antes da sprint.
4. **⚠️ Supersede um detalhe da BACK 06.1:** o parâmetro `com_glossario` de
   `qualify_session` foi REMOVIDO; o valor passa a ser **derivado** de
   `glossary_block is not None`. Motivo: um booleano afirmado pelo caller pode
   mentir (default esquecido, caller enganado) e a leitura D+30 compararia
   "antes x depois" contra uma flag que não corresponde ao prompt que rodou.
   O emissor continua **recebendo** o valor por parâmetro (a invariante da 06.1:
   nada hard-coded no emissor). Teste parametrizado cobre os dois cenários.
5. **Renderização determinística:** seções em ordem fixa
   (categoria → fornecedor → regra), entradas na ordem do repository
   (`kind, created_at, id`), **sem** timestamp/versão/id no texto. A versão do
   glossário invalida o cache pelo CONTEÚDO das entradas — imprimir o número da
   versão faria qualquer escrita invalidar o prefixo mesmo sem mudar o texto.
6. **Teto do bloco derivado dos MESMOS limites da 06.3**:
   `GLOSSARY_BLOCK_MAX_CHARS = MAX_ENTRIES_PER_CLIENT * (MAX_CODE + MAX_NAME +
   MAX_DESCRIPTION + overhead)` = 135.200 chars. Consequência deliberada: um
   glossário DENTRO dos limites documentados **nunca** é truncado; o truncamento
   protege contra dado que passou por fora (linha legada, escrita direta no
   banco, teto reduzido no futuro). Corte no último `\n` antes do teto (metade de
   uma regra é pior que a regra ausente) + nota **dentro do prompt** avisando que
   a lista está incompleta + `log.warning("qualification_glossary_truncated")`.
7. **Entrada indecifrável é OMITIDA** do bloco (com
   `qualification_glossary_decrypt_skipped`): injetar `[indecifrável]` como se
   fosse vocabulário do cliente é pior que omitir. Glossário inteiro
   indecifrável → sem bloco.
8. **O bloco diz explicitamente que é CONTEXTO e não revoga as regras.** Sem
   isso, um glossário generoso vira "marque tudo ok" e a qualificação perde a
   função — o oposto do outcome (menos falso positivo, não menos detecção).

**Medição (03/08/2026, output real).**

| | chars | tokens (est. ÷4) |
| --- | --- | --- |
| `_SYSTEM_PROMPT` | 1.962 | ~490 |
| glossário realista (12 categorias + 8 fornecedores + 3 regras) | 1.900 | ~475 |
| pior caso dentro dos limites da 06.3 (200 entradas cheias) | 134.017 | ~33.504 |
| teto (`GLOSSARY_BLOCK_MAX_CHARS`) | 135.200 | ~33.800 |

**⚠️ `cached_input_tokens` real NÃO foi medido** — não há chave Anthropic válida
neste ambiente, e afirmar cache-hit sem output real seria alucinação
(CLAUDE.md §6.10/§6.14). O que foi provado por teste é a **condição necessária**:
duas análises seguidas do mesmo cliente produzem `system_blocks` byte a byte
idênticos (o cache da Anthropic é keyed pelo conteúdo do prefixo). A medição
real fica como pré-requisito de validação em dev.

**Risco de custo registrado.** O pior caso (~33k tokens de bloco) é caro no
cache-WRITE de cada cliente novo/editado. Com glossário realista o bloco é do
tamanho do próprio `_SYSTEM_PROMPT` e o guardrail do PRD é folgado. Se a medição
em dev mostrar custo inaceitável, o botão a girar é `MAX_ENTRIES_PER_CLIENT` /
`MAX_DESCRIPTION_CHARS` na 06.2 — o teto do bloco os segue automaticamente.

**Verificação.** `ruff` + `format --check` + `mypy` limpos; `pytest -q --no-cov`
= **1000 passed, 5 skipped** (antes da task: 974). `git diff` em
`app/integrations/anthropic/client.py` e `prompts.py`: **vazio** — e há teste
unitário que falha se a palavra "glossar" aparecer nesses arquivos.
**Mutação:** desligando a injeção (`if False`), **5 testes ficam vermelhos**
(injeção, ordem com a regra de aplicação, isolamento A×B, edição não afeta o
outro tenant, ordem das seções).

## ADR-014 — Veredito do revisor: 2º eixo no PATCH que já existia, tipo restrito ao denominador, selo persistido na sessão (Sprint 6 / BACK 06.5)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:**
`app/db/models/{reconciliation_anomaly,reconciliation_session}.py`,
`app/modules/reconciliations/review/{schemas,service,routes,repository}.py`,
`app/modules/reconciliations/{schemas,service}.py`,
`app/modules/reconciliations/qualification/service.py`, migration `c5a2f81b6d34`

**Contexto.** O numerador da métrica da sprint é "flag que a revisão marcou como
improcedente". Não existia esse dado: `reconciliation_anomalies` só tinha
`resolved` + `resolution_note_encrypted`.

**Decisão.**

1. **`review_verdict` é um EIXO DIFERENTE de `resolved`.** "Resolvida" = alguém
   agiu; "improcedente" = o flag não devia ter sido levantado. Um flag
   improcedente costuma ser fechado sem nenhuma ação no Omie — misturar os dois
   apagaria justamente o falso positivo que a sprint quer medir. Coluna nova
   `review_verdict` (nullable, `procedente|improcedente`), NULL = não julgado.
2. **ESTENDEU o `PATCH .../anomalies/{anomaly_id}`** em vez de criar rota
   paralela. Consequências boas: RBAC/tenant/`_load_session_for_rbac` já estão
   lá; a rota **já está** na lista canônica de `sensitive_endpoints.py` (nada a
   registrar, denominador segue 38/38) e já é coberta pelo caso negativo
   cross-tenant parametrizado. Duas rotas fazendo revisão de anomalia seria a
   segunda implementação da mesma regra.
3. **`resolved` virou opcional** (`bool | None`), junto com `review_verdict`:
   marcar improcedente sem resolver é o caminho comum. Omitir = não mexer.
   Corpo com os DOIS ausentes é 400 — PATCH que não muda nada é bug do cliente.
   O contrato antigo (`{resolved, resolution_note}`) continua válido byte a byte.
4. **Só flags da Camada 1 aceitam veredito** (`qualificacao_suspeita`,
   `qualificacao_incoerente`) — **não** `padrao_quebrado`/`valor_outlier` nem as
   estruturais. Razão de MÉTRICA, não de gosto: o denominador é
   `qualificacao_emitida` com veredito suspeita/incoerente; julgar um tipo que
   não entra no denominador infla só o numerador. Recusa = 400
   `VALIDATION_ERROR` com `userMessage` em PT-BR acionável.
5. **`flag_revisado` só na MUDANÇA de estado** — o grão decidido na ADR-010.
   Reenviar o mesmo veredito não emite (não infla o denominador); trocar emite
   (a mudança não some). Fail-soft com SAVEPOINT: sink fora do ar não desfaz o
   julgamento do usuário.
6. **O selo do glossário é `reconciliation_sessions.qualification_used_glossary`**
   (NOT NULL default `false`), escrito por `qualify_session` a partir do bloco
   REALMENTE injetado (mesma origem do `com_glossario` do evento — uma conta só,
   nunca duas paralelas). Exposto em `SessionDetailPayload`, que a tela de
   revisão já carrega. Alternativa descartada: derivar do sink `usage_events` na
   leitura — seria acoplar UI a tabela de métrica e pagar um agregado por render.
   Sessão antiga e cliente sem glossário → `false`, sem regressão.
7. **`ReviewRepository.session` virou propriedade pública** para o service
   compor o `UsageEventRepository` sobre a MESMA transação; sem isso o evento
   cairia noutra conexão e deixaria de ser atômico com a marcação.

**Verificação (output real, 03/08/2026).** `ruff` + `format --check` + `mypy`
limpos; `pytest -q --no-cov` = **1019 passed, 5 skipped** (antes da task: 1000).
Round-trip real da migration (`TestVereditoDoRevisorRoundTrip`): colunas criadas
com a nulabilidade certa, `downgrade` remove as duas, `upgrade` recria.
**Mutação:** emitindo `flag_revisado` sempre (em vez de só na mudança) **e** sem
a guarda de tipo, **2 testes ficam vermelhos** —
`test_remarcar_com_o_mesmo_valor_nao_emite_de_novo` e
`test_anomalia_que_nao_e_da_qualificacao_e_recusada`.


<!-- ===== agent-frontend · Sprint 6 ===== -->

---

## ADR-011-FE — Glossário: a ROTA é de leitura para todos; só a ESCRITA pede permissão (Sprint 6 / FRONT 06.6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `apps/web/src/lib/authz.ts`,
`components/features/glossary/glossary-screen.tsx`, `features/clients/client-shell.tsx`

**Contexto.** O padrão que a S5 deixou para tela de tenant é "não tem a permissão →
`AccessDenied` + item de menu oculto" (`ClientUsersScreen`, ADR-008-FE). Copiá-lo aqui
seria o defeito: o backend da BACK 06.3 põe `ManageGlossaryDep` **só nas escritas** —
o `GET` exige apenas `AccessibleClientDep`, porque "o operador precisa do glossário
como referência" (docstring de `modules/glossary/routes.py`, lida antes de decidir).
Bloquear a rota para o operador negaria na UI o que o servidor libera, que é o mesmo
tipo de erro do botão que devolve 403 — só invertido, e mais difícil de perceber.

**Decisão.**
- Permissão nova `manage_glossary` em `PERMISSION_MATRIX`, espelhando linha a linha o
  `PERMISSION_MATRIX` do backend: `admin`, `manager` **e** `client_manager`. O
  `manager` do SISTEMA entra aqui (diferente de `manage_client_users`) porque é o que
  a matriz do backend diz; o "dentro da carteira" é `resolve_client_access`, não esta
  linha.
- **Não** existe permissão `view_glossary`. Inventá-la criaria no front uma regra que o
  backend não tem — e a primeira divergência apareceria como tela negada sem 403.
- Na tela, `canManage` governa: botão "Nova entrada", a **coluna "Ações" inteira** (não
  só os botões: cabeçalho de coluna permanentemente vazio é ruído para leitor de tela),
  o texto do subtítulo, o empty-state (com CTA para quem escreve, sem CTA para quem lê)
  e a **montagem** da gaveta e do `AlertDialog` — esconder o gatilho e deixar o diálogo
  montado ainda o deixaria alcançável.
- O item "Glossário" na nav do cliente aparece para os QUATRO papéis.

**Consequência.** É a primeira tela do produto com dois níveis (ler ≠ escrever) no mesmo
destino. Quem copiar `ClientUsersScreen` para uma tela nova precisa checar ANTES qual
dependency o backend pendurou no `GET`: `AccessibleClientDep` sozinho = rota de leitura
liberada; `Manage*Dep` = rota inteira gated.

## ADR-012-FE — Sem filtro por tipo na lista do glossário: o endpoint não aceita (Sprint 6 / FRONT 06.6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `components/features/glossary/glossary-screen.tsx`

**Contexto.** A task sugeria "filtro/segmentação por tipo se couber". O contrato
gerado mostra que `GET /clients/{client_id}/glossary` aceita **só** `page`/`pageSize`
(`list_glossary_entries_...` em `schema.ts`; conferido também no router).

**Decisão.** Não implementar o filtro. Filtrar no cliente operaria sobre a PÁGINA
carregada: esconderia entradas das outras páginas e o rodapé continuaria anunciando o
total do servidor — uma tela que mente sobre quantas entradas existem. A leitura por
tipo é dada pela coluna "Tipo" com badge (`info`/`warning`/`muted`).

**Consequência.** Quando o backend expuser `kind` como query param, o filtro entra
aqui e vira estado na URL, como busca/paginação nas outras listas. Enquanto não
expuser, a ausência é deliberada — não é esquecimento.

## ADR-013-FE — "Cliente tipado" neste repo é o helper POR MÉTODO + tipos do contrato (Sprint 6 / FRONT 06.6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `apps/web/src/lib/api/*`

**Contexto.** O CLAUDE.md do papel manda usar `apiTyped.METHOD("/path", ...)`, para o
método errado virar erro de compilação. **`apiTyped` não existe neste repo** (grep
vazio): o que existe desde a S3 é `apiGet`/`apiPost`/`apiPatch`/`apiDelete` em
`lib/api/client.ts`, com o shape vindo do contrato gerado no genérico.

**Decisão.** Seguir o padrão existente (`lib/api/glossary.ts` espelha
`lib/api/client-users.ts`), e não introduzir uma segunda camada de cliente só nesta
feature — duas convenções de chamada conviveriam no mesmo diretório, que é pior que a
que já está lá.

**Por que o risco do learning não se aplica.** O learning "método HTTP como string é
opaco ao compilador" descreve `apiFetch(url, {method: "POST"})`: o método é um campo de
objeto, e trocar POST↔PATCH não muda tipo nenhum. Aqui o método está no **nome da
função** — `apiPatch` não é `apiPost`, a troca é visível na revisão e no diff. O que
continua NÃO travado pelo `tsc` é o par (rota, método): nenhum dos dois desenhos amarra
o path ao verbo. Fechar isso de verdade é um cliente derivado de `paths[...]` do
`schema.ts`, refactor transversal a todas as features — fora do escopo desta task, e
registrado aqui como dívida conhecida.

## ADR-014-FE — Shapes de anomalia migrados do `interface` manual para o contrato (Sprint 6 / FRONT 06.7)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `apps/web/src/lib/api/reconciliations.ts`,
`apps/web/src/lib/contracts/index.ts`

**Contexto.** `AnomalyItem`, `AnomalyTypeRef`, `AnomalyRelated*` e `PatchAnomalyPayload`
eram `interface`s redigitadas à mão no módulo de API — exatamente o "shape esperançoso
espelhando endpoint" que o CLAUDE.md proíbe, sobrevivente desde a S11/S12. A BACK 06.5
acrescentou `review_verdict` ao item e tornou `resolved` OPCIONAL no request.

**Decisão.** Trocar a origem em vez de acrescentar mais um campo à cópia. Os nomes
exportados foram mantidos (`export type AnomalyItem = Schemas['AnomalyItem']`), então
nenhum consumidor mudou — `tsc --noEmit` limpo na primeira execução após a troca, o que
também prova que a cópia estava fiel até aqui.

**O que isso destravou (não é cosmético).** Com `PatchAnomalyPayload.resolved: boolean`
obrigatório, mandar **só** o veredito **não compilava** — e o caminho comum da Sprint 6 é
justamente "marcar improcedente sem resolver". A `interface` manual não estava só
desatualizada: ela proibia a feature.

**Consequência.** Sobram `interface`s manuais no mesmo arquivo (movimentações, entradas
Omie, `OmieLancamentoItem`). Não foram tocadas — refactor não pedido, e a Sprint 6 não
mexe nesses endpoints. Ficam registradas aqui como a próxima dívida do mesmo tipo.

## ADR-015-FE — Selo do glossário é da SESSÃO e mora no cabeçalho, não na linha (Sprint 6 / FRONT 06.7)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `review/glossary-seal.tsx`,
`detail/session-detail-screen.tsx`

**Contexto.** `qualification_used_glossary` é um booleano do detalhe da SESSÃO, escrito
por `qualify_session` a partir do bloco realmente injetado no prompt. O lugar óbvio para
"o veredito considerou o glossário" seria junto do veredito — isto é, na célula de
qualificação de cada movimentação.

**Decisão.** Um selo só, no cabeçalho da conciliação, ao lado do badge de status.
Motivo: o dado não varia por linha. Renderizá-lo por linha repetiria a mesma informação
N vezes numa tabela **virtualizada** (nós extras no DOM, ruído para leitor de tela,
custo por linha renderizada) sem dizer nada que o topo não diga. O cabeçalho cobre as
quatro abas de uma vez, e a virtualização da aba Movimentações não foi tocada.

**`false` não ocupa espaço.** Cliente sem glossário e sessão anterior à sprint (default
`false`) não renderizam nada: sem placeholder, sem "não considerou". A tela fica
idêntica ao comportamento anterior — que é o critério de "sem regressão", e está coberto
nos dois lados (jsdom + e2e).

**Detalhe de layout que só apareceu no 390px:** o selo entrou num `flex-wrap` junto do
badge de status. Sem isso ele sairia da linha — a mesma classe de defeito do botão
"Sair" cortado na S5 (ADR-010-FE). O e2e assere `x + width <= viewport.width`.

## ADR-016-FE — Veredito do revisor é EIXO PRÓPRIO, com "Não avaliado" visível (Sprint 6 / FRONT 06.7)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `review/anomaly-verdict-control.tsx`,
`review/anomalies-tab.tsx`

**Contexto.** A aba Anomalias já tinha a coluna "Status" (Pendente/Resolvida). A
tentação é acrescentar "Improcedente" como um terceiro valor dela.

**Decisão.** Coluna separada ("O flag procedia?"). "Resolvida" = alguém agiu sobre a
anomalia; "procedente/improcedente" = ela *devia ter sido levantada*. São perguntas
diferentes, e o backend as separou de propósito (dois campos opcionais no mesmo PATCH,
com `flag_revisado` emitido só na MUDANÇA de veredito). Fundir os eixos apagaria a
métrica de outcome da sprint — um flag improcedente costuma ser fechado sem ação
nenhuma no Omie.

**Três estados, todos visíveis.** "Não avaliado" é um rótulo de verdade, não a ausência
dos outros dois: sem ele o revisor não distingue "ainda não olhei" de "olhei e achei
procedente" — e a métrica depende justamente dessa distinção. O estado vai em
`aria-pressed` **e** em texto; a variante do botão é reforço, nunca o único sinal.

**A ação só existe onde o servidor aceita.** `qualificacao_suspeita` e
`qualificacao_incoerente` — os mesmos `QUALIFICATION_FLAG_CODES` de `review/service.py`.
Nos demais tipos a célula mostra travessão. Oferecer o botão e receber 400 seria o
defeito de UX que a matriz do front existe para evitar; a autoridade continua sendo o
backend.

**Reenviar o mesmo veredito não dispara request.** É inócuo no servidor (o evento só sai
na mudança), mas gasta um round-trip e um refetch da lista — e o operador reclica por
reflexo.

## ADR-017-FE — O toast pinta pelos TOKENS do tema; `richColors` do Sonner saiu por reprovar contraste (Sprint 6 / FRONT 06.7 — retrabalho 1)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `apps/web/src/app/providers.tsx`,
`src/app/globals.css`, `tailwind.config.ts`, `src/app/__tests__/theme-contrast.test.ts`,
`e2e/a11y-mocked.spec.ts`

**Contexto.** O QA reprovou a FRONT 06.7 com o gate `web_a11y` VERMELHO:
`serious/color-contrast` em `div[data-title=""]` — o toast de sucesso do veredito,
`#008a2e` sobre `#ecfdf3` = **4.25:1**, abaixo do AA de 4.5:1 nos 13px do título. O
`<Toaster richColors>` (`providers.tsx`, pré-existente desde a S4) usa a paleta própria
do Sonner, que nunca passou pelo `theme-contrast.test.ts` — os tokens do produto passam,
mas o toast não os usava. A FRONT 06.7 foi a **primeira** entrega a colocar um toast na
tela enquanto o axe rodava; o defeito é antigo, o vermelho é deste commit.

**Decisão.**
1. **`richColors` fora, `toastOptions.classNames` dentro**, com os mesmos pares dos
   badges (`bg-success-muted`/`text-success` e família). A prop tinha de sair, não
   bastava acrescentar `classNames`: as regras do Sonner para rich colors são
   `[data-rich-colors=true][data-sonner-toast][data-type=…]` — especificidade (0,3,0),
   que ganha de um utilitário do Tailwind (0,1,0). Já a regra base é
   `:where([data-sonner-toast][data-styled="true"])`, e `:where()` tem especificidade
   **zero** — por isso a classe vence sem `!important`. Conferido em
   `node_modules/sonner/dist/styles.css`, não presumido.
2. **Um par por TIPO, nada de base comum.** O Sonner concatena `classNames.default` **e**
   `classNames[tipo]` no mesmo `<li>` (conferido em `dist/index.mjs`): pôr fundo/texto nos
   dois deixaria o vencedor por conta da ordem em que o Tailwind emite os utilitários.
   O toast neutro fica com o `--normal-bg`/`--normal-text` do Sonner (~18:1).
3. **Token novo `--destructive-muted`**, simétrico a `success/warning/info`, em vez de
   reusar `bg-destructive/10` no toast de erro. `/10` é TRANSLÚCIDO e o toast flutua sobre
   a página: ele comporia com card, tabela ou badge embaixo — o mesmo mecanismo da
   ADR-007-FE. O par entrou no `theme-contrast.test.ts` (claro e escuro).
4. **Correção no `<Toaster>`, não no call site.** Quem escolhe é o `toast.success(...)`;
   quem pinta é o provider. Isso cobre de uma vez os ~20 call sites — inclusive os três da
   FRONT 06.6 (`glossary-form-drawer.tsx:118,121`, `glossary-delete-confirm.tsx:59`), que
   o QA apontou como o mesmo objeto.

**O gate sozinho não bastava (achado desta correção).** O agent acrescentou o cenário do
toast de ERRO e, para provar que ele mede alguma coisa, mutou o par para
branco-sobre-quase-branco: o `analyze()` do axe passou **verde** — o toast de erro já tinha
saído da tela (4 s de `duration`) quando o axe rodou. Só a medição direta
(`measuredContrast`, o mesmo helper do badge da S4) reprovou, com **1.048**. Logo:
**cenário que depende de elemento efêmero leva asserção síncrona no elemento, não só
`analyze()` da página** — senão é teatro verde.

**Verificação (container `mcr.microsoft.com/playwright:v1.59.1-noble`, `--retries=0`,
build standalone).**

| Árvore | Resultado |
| --- | --- |
| com a correção | `expected=118 unexpected=0 skipped=0 flaky=0` |
| mutação 1 — `richColors` de volta | `expected=114 unexpected=4`, `#008a2e`/`#ecfdf3` = 4.25:1 (reproduz o achado do QA) |
| mutação 2 — par do erro para `text-destructive-foreground` | 4 cenários vermelhos, contraste medido **1.048** |

> **Confirmado pelo QA na re-revisão (03/08, ADR-017-QA):** a árvore corrigida deu
> `118 passed` no mesmo container, e a mutação `richColors` + `classNames` juntos
> reprovou os **dois** toasts (sucesso **4.259**, erro **4.347**) — o que também confirma
> por execução a afirmação de especificidade do item 1 (a prop precisava SAIR; manter
> `richColors` ao lado de `classNames` deixa o Sonner ganhar).

**Correção de fato registrada no repo:** o cabeçalho de `e2e/a11y-mocked.spec.ts` afirmava
que não havia `docker` nesta distro WSL e que os cenários da Sprint 6 não tinham sido
medidos. **`docker` existe e funciona aqui** (`docker ps` responde; a imagem do Playwright
está em cache) — foi como o QA reprovou e como esta correção foi verificada. Comentário
committed que declara "não verificado" onde há caminho de verificação faz o próximo agent
não medir; o aviso foi reescrito com o número real e a data.

<!-- ===== agent-review (QA) · Sprint 6 ===== -->

---

## ADR-013-QA — O gate de a11y só mede o que está montado: estado transitório entra no spec (Sprint 6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** `CLAUDE.md` do QA (checklist), veredito da FRONT 06.7

**Contexto.** Todos os cenários de `e2e/a11y-mocked.spec.ts` até a Sprint 5 analisavam a
tela **em repouso**. O 1º cenário que chamou `analyze()` com um toast na tela (Sprint 6 /
FRONT 06.7, "operador marca o flag como improcedente") ficou **vermelho**:
`serious/color-contrast` em `div[data-title=""]`, `#008a2e` sobre `#ecfdf3` = 4.25:1
(< 4.5:1). O `<Toaster ... richColors />` está em `app/providers.tsx` desde a S4 — o
defeito é antigo, o que faltava era medi-lo.

**Decisão.**
1. **Estado transitório é tela.** A checklist de a11y do QA passa a exigir ao menos um
   cenário com toast/tooltip/popover **montado** no instante do `analyze()`.
2. **Componente de terceiro com paleta própria é ponto cego duplo.** Escapa do audit de
   token por `grep` (não há hex no nosso CSS — a cor vem do CSS da lib) e escapa do axe
   (nunca está na árvore quando se mede). Os dois gates verdes não implicam AA.
3. **A correção mora no `<Toaster>` global**, não no call site: os mesmos `toast.success`
   saem da tela de Glossário (FRONT 06.6, aprovada) e de qualquer feature futura.
4. **Não se relaxa o `analyze()`** para passar (CLAUDE.md §6.16): o gate está certo, o
   contraste é que está errado.

**Consequência.** A FRONT 06.7 volta como FAILED com o comando de execução do gate no
comentário. A FRONT 06.6 foi aprovada: o defeito existe nos toasts dela também, mas
nenhum cenário os mede e a correção é a MESMA linha — espalhar a reprovação pediria dois
retrabalhos para uma correção só.

## ADR-014-QA — O gate de contrato protege o TIPO; a CHAVE no fio precisa de asserção própria (Sprint 6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:**
`apps/api/tests/integration/test_glossary_contract_qa.py`, `CLAUDE.md` do QA

**Contexto.** A ADR-011-QA fechou o drift de contrato regenerando `schema.ts` a partir da
OpenAPI do backend commitado e exigindo `git diff` = 0 (feito de novo nesta sprint: **diff
0**, `openapi-typescript 7.13.0` sobre `app.openapi()` de `d499a09` vs. `schema.ts` de
`d2e9d86`). Isso protege o **tipo** — não o comportamento em runtime.

**Problema encontrado.** `GlossaryEntryResponse.decrypt_failed` tem
`alias="decryptFailed"` e o front lê `entry.decryptFailed`. Removendo o alias, os **22
testes de rota da BACK 06.3 continuam verdes** (todos leem o VALOR: `data[...]["name"]`)
e o gate de contrato também, porque a OpenAPI é montada pelo mesmo `by_alias` da
serialização — **os dois regridem juntos e em silêncio**. Na tela, a badge "Indecifrável"
sumiria e a entrada corrompida viraria linha aparentemente normal: a célula silenciosa que
o CLAUDE.md §4.1 proíbe.

**Decisão.** Teste de QA que assere as **chaves literais** dos 4 verbos do glossário:
`decryptFailed` presente / `decrypt_failed` ausente, o conjunto exato de chaves da entrada,
e o **número de chaves do envelope** (1 no POST/PATCH/DELETE, 2 na lista — o `rawFetch` do
front só desembrulha quando `data` é a única). Regra na checklist: **campo com alias exige
asserção da chave, não só do valor**.

**Verificação (output real, 03/08/2026).** 4 passed. **Mutação:** removendo o alias do
schema, **3 dos 4 ficam vermelhos** e os 22 da BACK 06.3 seguem verdes — que é exatamente
o buraco que o teste existe para tapar.

## ADR-015-QA — Isolamento por tenant do glossário: provado por MUTAÇÃO, e a cripto é a 2ª barreira (Sprint 6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** veredito das BACK 06.2/06.3/06.4

**Contexto.** O risco nº 1 da sprint é glossário de um cliente vazar para outro — na
leitura HTTP e, pior, **dentro do prompt** de qualificação.

**O que foi medido.** Removendo o filtro de tenant de
`ClientGlossaryRepository._scoped` (`AND client_id = <alvo>` + `scoped_by_tenant`),
**5 testes ficam vermelhos**: `test_snapshot_de_um_tenant_nunca_traz_entrada_do_outro`,
`test_usuario_de_cliente_forjando_client_id_nao_le_o_outro_tenant`,
`test_detalhe_por_pk_de_outro_tenant_devolve_none`,
`test_admin_le_o_tenant_alvo_e_nada_alem_dele` e
`test_entrada_de_outro_tenant_por_pk_devolve_404`. Não são vacuosos.

**Achado que vale registrar.** Com a MESMA mutação, os testes de isolamento **do prompt**
(`TestIsolamentoEntreClientes`) continuam **verdes** — porque a 2ª barreira segura: o AAD
amarra cada ciphertext ao par (cliente, linha), o cipher de B não decifra a entrada de A,
a entrada volta `decrypt_failed=True` e `render_glossary_block` a **omite**. Ou seja: uma
regressão no `WHERE` **não** vaza texto do outro tenant no prompt — vira bloco menor +
`qualification_glossary_decrypt_skipped` no log.
**Consequência para o próximo QA:** o teste de isolamento do prompt sozinho **não** prova o
filtro SQL (dois mecanismos independentes o sustentam). Quem prova o `WHERE` são os 5
acima — mantenha-os.

## ADR-016-QA — `cached_input_tokens` NÃO foi medido: a condição necessária foi, o cache-hit não (Sprint 6)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** veredito da BACK 06.4

**Declaração explícita** (o critério de aceite da task de QA permite o número medido **ou**
a limitação declarada — e afirmar cache-hit sem output real seria alucinação,
CLAUDE.md §6.10):

- **Não há chave Anthropic válida neste ambiente**, então `cached_input_tokens > 0` em duas
  análises consecutivas do mesmo cliente **não foi observado contra a API real**. Fica como
  pré-requisito de validação em dev, com o `client_id` e o `glossary_block_chars` já no log
  de `qualification_semantic_batch` para a leitura D+30 separar por tenant.
- **O que foi provado por execução** é a condição NECESSÁRIA do cache-hit (o cache da
  Anthropic é keyed pelo conteúdo do prefixo): duas montagens seguidas do mesmo cliente
  produzem `system_blocks` byte a byte idênticos; a ordem das seções e das entradas é fixa;
  nem versão nem timestamp entram no texto; editar o glossário de A não muda o bloco de B.
- **Tamanho medido:** `_SYSTEM_PROMPT` 1.962 chars; glossário realista ~1.900 chars; pior
  caso dentro dos limites da BACK 06.3 134.017 chars (~33,5k tokens estimados), abaixo do
  teto `GLOSSARY_BLOCK_MAX_CHARS = 135.200`. Não há teto de tokens de ENTRADA no código
  (`grep` de `MAX_INPUT`/`TOKEN_LIMIT`: só caps de SAÍDA) — o guardrail de tamanho é o teto
  de entradas da 06.3 mais o truncamento determinístico da 06.4, e ambos têm teste.

## ADR-017-QA — Correção de contraste se re-verifica com a mutação DUPLA, não só com o verde (Sprint 6 / re-revisão 1)

**Data:** 2026-08-03 · **Status:** ativo · **Escopo:** veredito da FRONT 06.7 (re-revisão),
`CLAUDE.md` do QA (checklist de a11y)

**Contexto.** A FRONT 06.7 voltou de FAILED com o commit `7a7062e`, trocando o
`<Toaster richColors>` por `toastOptions.classNames` sobre os tokens do tema, mais um
cenário novo para o toast de ERRO. Aceitar "agora dá 118 passed" seria o mesmo erro que a
1ª revisão pegou: número verde não prova que o gate MEDE — prova que ele não reclamou.

**Decisão (o que a re-revisão executou, no container
`mcr.microsoft.com/playwright:v1.59.1-noble`, build standalone, `--retries=0`).**
1. **Árvore corrigida:** `118 passed (53.2s)`, `unexpected=0 skipped=0 flaky=0` — o número
   que o agent declarou, reproduzido de forma independente.
2. **Mutação `richColors` de volta AO LADO do `classNames`** (não em vez dele — assim o
   `TOAST_CLASSNAMES` continua referenciado e o `next build` não morre no
   `no-unused-vars`, e de quebra a mutação testa a afirmação de especificidade da
   ADR-017-FE): **os dois** pares reprovam — sucesso **4.259:1**, erro **4.347:1**, 4+4
   cenários vermelhos. Confirma (a) que a prop precisava SAIR, não bastava acrescentar
   `classNames`; (b) que os dois cenários novos são sensíveis, não decorativos.

**Por que a mutação dupla é o formato certo aqui.** Um `expect(...).toBeGreaterThanOrEqual`
sobre elemento efêmero falha por DOIS motivos indistinguíveis no output: cor ruim ou
elemento ausente. Mutar a cor mantendo o elemento na tela separa os dois — o teste que
sobrevive é o que reprova a COR. Foi assim que a re-revisão descartou a hipótese "o
cenário do toast de erro só passa porque o `$eval` acha o seletor".

**Consequência para o próximo QA.** Reprovação de contraste re-revisada exige o par
(árvore corrigida verde, árvore mutada vermelha) com os dois números citados. O comando
completo está no cabeçalho de `apps/web/e2e/a11y-mocked.spec.ts` — que a partir desta
sprint é a fonte da verdade de COMO rodar o gate, e está correto (foi ele que a re-revisão
seguiu, sem adaptação).


---

# Sprint 7 — Lançamento de faturas de cartão no Omie

<!-- ===== agent-backend ===== -->
## ADR-018-BE — A captura de escrita do `IncluirLancCC` é opt-in e cria lançamento REAL (Sprint 7 / BACK 07.1)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `scripts/capture_omie_fixtures.py`,
`tests/fixtures/omie/README.md`, `tests/unit/test_omie_fixtures.py`,
`app/integrations/omie/schemas.py`

**Conflito de fato, registrado e NÃO resolvido por conta própria.** O DoD do PRD da Sprint 7
pede "idempotência provada **no sandbox real**". O `CLAUDE.md` §10 diz que **a Omie não tem
sandbox** (decisão já fechada: testes contra conta real, ex.: Quial). Os dois não podem ser
verdade ao mesmo tempo. A leitura adotada — e o que a task pode fazer sem inventar — é:
a captura roda contra **conta real**, e por isso é tratada como operação perigosa.

**Decisão.** A captura de escrita é **opt-in explícito** (`OMIE_CAPTURE_ALLOW_WRITE` em
`1`/`true`/`yes`; qualquer outro valor, inclusive ausente e vazio, significa NÃO).
Sem a variável o script avisa e **não faz POST algum** — travado por teste
(`test_no_write_post_without_opt_in`), não por convenção. Complementos: valor default de
`R$ 0,01`, `cObs` que identifica a origem, `OMIE_CAPTURE_COD_INT_LANC` obrigatório (é por
ele que o operador localiza o lançamento) e o README manda **excluir manualmente** o(s)
lançamento(s) — o ADL não implementa `ExcluirLancCC` (fora do escopo da sprint).

**Por que não bastou "documentar com cuidado".** Diferente de toda a integração até aqui,
esta chamada **grava dinheiro na contabilidade de um cliente**. Um default permissivo aqui
não é bug de conveniência, é dano difícil de desfazer — o mesmo motivo pelo qual o
critério de rollback da sprint é "um único duplicado desliga o recurso".

**O que fica BLOQUEADO até a captura existir.** As BACK 07.2–07.5 podem ser construídas (a
dedup primária é do ADL e não depende do fornecedor), mas **nenhum lançamento real** pode
ser feito em dev/prod antes de a fixture existir e o gate ficar verde. Sem fixture, os
testes de escrita **SKIPAM citando S-1** — nunca passam verde em silêncio.

## ADR-019-BE — Nenhum campo do contrato de escrita é apresentado como fato (Sprint 7 / BACK 07.1)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `app/integrations/omie/schemas.py`
(`IncluirLancCCRequest`, `IncluirLancCCResponse`), `tests/unit/test_omie_fixtures.py`

**Contexto.** É o defeito P11 da Sprint 1 se repetindo de forma idêntica: um contrato
plausível vindo da doc, implementado contra um mock que repete os mesmos nomes inventados.
O PRD da Sprint 7 traz a tabela do `IncluirLancCC` — e ele próprio marca o contrato como
não-verificado (S-1).

**Decisão.** Os DTOs de escrita existem, mas cada suposição está marcada **NÃO-VERIFICADA**
na docstring, com a origem declarada:
- **Sinal** (`nValorLanc` absoluto + `cNatureza`): confirmado só no lado de **LEITURA**
  (`ListarExtrato`, cabeçalho de `omie/schemas.py`). Seguimos a convenção de leitura porque
  assumir o oposto seria fabricar contra o que o repositório já sabe — mas marcado.
- **Unicidade de `cCodIntLanc`**: NÃO confirmada. Não é a defesa primária (ver BACK 07.2).
- **`cTipo`**: `"DIN"` é palpite do PRD; o campo análogo lido é `string3` `PAG`/`ATR`. O
  campo é opcional e a captura **não o envia** — mandar um valor inventado poderia fazer a
  Omie recusar a chamada e transformar a captura numa prova falsa ("o contrato está errado")
  quando o errado seria só este campo.
- **`nCodLanc` na resposta é obrigatório de propósito**: é o dado que o ADL persiste. Se o
  nome real for outro, o teste FALHA em vez de gravar `None` calado — que foi exatamente o
  modo de falha do `ListarExtrato` v1.

**Como o gate fecha o laço.** O script de captura monta o request **a partir do DTO**
(`model_dump(by_alias=True)`), não de um dict solto; o teste compara as chaves da fixture
com `IncluirLancCCRequest.omie_param_aliases()`. Renomear campo no DTO sem recapturar uma
chamada real acusa divergência. E, como um request gravado só mostra o que **mandamos**,
a captura relê o extrato do dia (`incluir_lanc_cc.readback.json`): é ele que responde se a
Omie **entendeu** `cNatureza='D'` + valor absoluto como débito — a única evidência possível
da convenção de sinal.

## ADR-020-BE — Divergência PRD (CA) × código (CR = cartão): vale o código (Sprint 7 / BACK 07.1)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** todas as tasks da Sprint 7

**Fato.** O PRD da Sprint 7 chama a conta de cartão de **`CA`** ("a conta `CA` já selecionada
na conciliação", "lançamento na própria conta corrente do cartão, `CA`"). O repositório diz o
**oposto**, e por incidente corrigido: `OmieAccountType`
(`app/integrations/omie/schemas.py`) documenta `CA = Conta Aplicação (investimento, não
cartão!)` e `CR = Cartão de Crédito` — a v1 do enum mapeava `CREDIT_CARD = "CA"` e foi
corrigida na auditoria M-1 (20/05/2026), justamente porque o front renderizava Conta
Aplicação como "Cartão".

**Decisão.** Vale o **código**. Elegibilidade do fluxo de lançamento é
`session.account_type == "credit_card"` (o valor do domínio do ADL), que corresponde ao
Omie **`CR`**. Nenhuma task da Sprint 7 deve filtrar por `CA`.

**Consequência.** Quem ler o PRD sem este ADR vai escrever o filtro errado e o lançamento
sairia para contas de investimento — ou, mais provável, para nenhuma conta. Este é o tipo
de erro que passa em teste (o mock diria o mesmo) e só aparece com dado real.

## ADR-021-BE — Tabela própria para a intenção de lançamento, não colunas na `file_entry` (Sprint 7 / BACK 07.2)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`app/db/models/reconciliation_omie_posting.py`,
`alembic/versions/d7c2b9f14a86_s7_reconciliation_omie_postings.py`,
`app/modules/reconciliations/omie_posting/`

**Alternativa considerada e recusada:** acrescentar `cod_int_lanc`, `posting_status`,
`attempts`, `error_*` em `reconciliation_file_entries`. Recusada porque a `file_entry`
carrega o **resultado** da conciliação (situação, lançamento vinculado), enquanto a
intenção tem ciclo de vida próprio: nasce **antes** do POST, sobrevive a timeout, acumula
tentativas, guarda o erro do fornecedor e pode terminar em `failed` **sem que a linha mude
de estado**. Misturar as duas deixaria o caminho de falha — o que mais importa, porque é
dinheiro na contabilidade do cliente — sem lugar para morar, e obrigaria a `file_entry` a
crescer 5 colunas que 99% das linhas nunca usam.

**Decisão.** Tabela `reconciliation_omie_postings` com **duas garantias no BANCO**:

- `UNIQUE(file_entry_id)` — uma intenção por linha. É o que torna `register_intent`
  idempotente **sob concorrência**: a garantia é do banco (`INSERT ... ON CONFLICT DO
  NOTHING`), não de um "SELECT antes do INSERT", que tem janela de corrida exatamente
  onde o erro custa dinheiro (duplo-clique, retry, dois workers).
- `UNIQUE(client_id, cod_int_lanc)` — a chave enviada à Omie não se repete no tenant.

`client_id` é desnormalizado de propósito: toda query filtra por ele (S5/R3) e, sem a
coluna, o filtro exigiria JOIN com `sessions` em todo acesso — no dia em que alguém
esquecer o JOIN, o isolamento some sem aviso.

**Convivência com o índice que já existia.** `ix_recon_file_entry_session_omie_unique`
(`session_id`, `omie_lancamento_id`, parcial) impede duas linhas da mesma sessão de
apontarem para o mesmo lançamento (§5.4). A confirmação **não o contorna**: checa antes e
levanta `OmieLancamentoAlreadyLinkedError` (409 tratado) em vez de estourar `IntegrityError`
cru como 500.

**Verificado contra Postgres 16 real** (`docker exec`, banco descartável): upgrade →
downgrade → upgrade limpo; as duas UNIQUEs recusam a 2ª intenção da mesma linha e a mesma
chave em outra linha do tenant; duas linhas distintas com chaves distintas passam; e o
índice parcial recusa o mesmo `nCodLanc` em duas linhas da sessão.

## ADR-022-BE — `cCodIntLanc` é derivado da IDENTIDADE da linha, com 85 bits (Sprint 7 / BACK 07.2)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`app/modules/reconciliations/omie_posting/keys.py`

**O modo de falha que a decisão evita não é o duplicado — é o FALTANTE.** Uma chave
derivada do conteúdo (data + valor + descrição) colapsaria duas compras reais idênticas na
mesma fatura — dois cafés de R$ 12,00 no mesmo dia e no mesmo estabelecimento — numa chave
só, e a **segunda nunca seria lançada**. Isso é pior que duplicado: o critério de rollback
da sprint ("um único duplicado desliga o recurso") vigia o duplicado e **não vigia o
faltante**, então o erro passaria despercebido.

**Decisão.** `cCodIntLanc = "ADL" + base32(85 bits de blake2s(file_entry_id))` = 20 chars
exatos, o teto `string20` da Omie. Detalhes com motivo:

- **Digest e não os bytes do UUID:** os dois seriam igualmente únicos (UUIDv4 já é
  aleatório); o digest evita entregar a PK do ADL a um sistema de terceiros.
- **Prefixo `ADL` consome 3 dos 20 caracteres de propósito:** o operador vê essa chave na
  tela do Omie e precisa saber de onde ela veio (é por ela que localiza o lançamento).
- **85 bits, e a colisão NÃO é tratada como impossível:** com 1 milhão de linhas de um
  cliente a chance é ~1,3e-14, mas o `UNIQUE(client_id, cod_int_lanc)` existe e converte o
  caso em `OmiePostingKeyCollisionError` — erro tratado, nunca uma linha silenciosamente
  não lançada. Truncar um UUID "porque não vai colidir" seria escolher uma taxa sem medir.
- **Determinística:** no caminho de timeout (07.4) o ADL reconsulta a Omie pela MESMA
  chave sem depender de ter conseguido gravar algo entre o envio e a falha.

## ADR-023-BE — A mensagem de erro do provedor é PERSISTIDA e NUNCA logada (Sprint 7 / BACK 07.2)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`reconciliation_omie_postings.error_message`, `omie_posting/repository.py`

**A pergunta que a task manda decidir e registrar:** `faultstring` do Omie é texto livre de
terceiro. É PII do cliente final?

**Decisão: tratar como se pudesse ser.** A mensagem é **guardada** (o operador precisa
vê-la inline para agir — sem ela, "erro no Omie" é inacionável) e **nunca é logada**. O
log do caminho de falha registra apenas o código e os IDs. Motivo: o CLAUDE.md §3.3 proíbe
logar conteúdo não controlado, e a Omie pode ecoar no `faultstring` o valor que
enviamos — inclusive o `cObs`, que carrega a **descrição da compra** (§4.5: nenhum dado
identificável do cliente final persiste em claro fora do que o §4.1 autoriza). Persistir na
coluna é o mesmo tratamento que a `file_entry` já dá ao dado do lançamento; **logar** é que
espalharia o texto para Loki/Sentry, fora do alcance da cripto por cliente.

**Consequência para a 07.4/07.5:** a mensagem volta ao usuário na resposta do lote (é
requisito de R5) e **não** entra em `usage_events` — o evento carrega um código
categórico, nunca o texto (ver a decisão da 07.5).

## ADR-024-BE — Categorias: cache EM MEMÓRIA e lista completa sem paginação (Sprint 7 / BACK 07.3)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`app/integrations/omie/categorias_cache.py`, `app/modules/omie_data/`

**Duas decisões, cada uma com uma alternativa recusada.**

**1. Cache in-memory (`cachetools.TTLCache`), NÃO uma tabela como
`omie_accounts_cache`.** O CLAUDE.md §4.5 nomeia **`categorias`** na lista do que
"nunca persiste em claro — sempre buscado do Omie em tempo real e mantido apenas em cache
com TTL". Descrição de categoria é vocabulário contábil do cliente final. Existem dois
padrões de cache no repo e só um serve: o de contas correntes persiste (decisão anterior,
para dado que o §4.5 trata à parte), o de lançamentos (`omie/lancamento_cache.py`) é
TTLCache em processo, criado exatamente para o dado que não pode encostar no disco.
Seguimos o segundo — sem dependência nova, sem camada nova. TTL 6 h + `refresh=true`
explícito para quem acabou de criar categoria no Omie (melhor que TTL curto, que faria
toda tela pagar a latência da Omie).

**2. Lista COMPLETA, sem paginação.** A task pede "a menor solução, e justifique". O
consumidor é um combobox com busca; paginar significaria uma ida ao servidor por tecla
digitada — exatamente o gargalo que a suposição S-3 do PRD teme. A lista inteira já vem de
memória. Envelope `{data, total}`. **Consequência:** o alias `pageSize` (§7 do CLAUDE.md)
não se aplica aqui porque não há paginação — se um dia houver, o alias é obrigatório.

**3. Tenant vem da SESSÃO, nunca da query.** `GET /api/v1/omie/categorias?session_id=…`
usa `require_client_for_session`, o mesmo mecanismo do `/omie/lancamentos` já existente:
o `SELECT` da sessão já sai com o filtro de tenant, então sessão alheia é 404 antes de
qualquer coisa. A rota está em `app/core/sensitive_endpoints.py` (39 endpoints) e o teste
parametrizado cross-tenant a cobre automaticamente.

**4. Erro do fornecedor NUNCA vira lista vazia.** A Omie responde HTTP 200 com
`faultstring`; tratar isso como `[]` faria o operador concluir que o cadastro de categorias
dele está vazio, quando o que houve foi credencial inválida ou instabilidade. Cada família
de erro vira mensagem própria, e a falha **não é cacheada** (a tentativa seguinte
reconsulta).

**5. `conta_inativa` desconhecido = categoria ATIVA.** O nome do campo é NÃO-VERIFICADO
(veio da doc). Se ele divergir, o filtro simplesmente não remove nada. O contrário —
default "inativa" — sumiria com o catálogo inteiro do cliente e travaria o lançamento.
Falhar para o lado de mostrar demais é recuperável.

## ADR-025-BE — O contrato OpenAPI gerado é obrigação do backend, mesmo morando em `apps/web/` (Sprint 7)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`apps/web/src/lib/contracts/schema.ts`

**Conflito real de regras, resolvido e declarado.** O `CLAUDE.md` do agent backend diz
"escreve só em `apps/api/`; não toca em frontend". O `CLAUDE.md` do PROJETO (§7) diz
"regenerar e commitar o contrato (OpenAPI) ao mexer em rota/schema/`response_model`/
docstring — é a fonte que o frontend consome". O artefato gerado vive em
`apps/web/src/lib/contracts/schema.ts`.

**Decisão: o backend regenera.** O arquivo não é código de frontend — é a projeção
mecânica do OpenAPI que o backend produz, e **só o backend consegue gerá-lo** (a geração
exige a app FastAPI para emitir `openapi.json`). Deixar para o agent de frontend
significaria ou um arquivo escrito à mão (o pior resultado possível para um contrato) ou a
sprint travada.

**Como foi gerado, sem subir servidor:** `app.openapi()` dumpado para JSON +
`npx openapi-typescript@7.4.1 <json> -o <arquivo>` — a mesma versão pinada em
`apps/web/package.json`, o mesmo resultado do script `gen:types`, sem depender de uma API
em `localhost:8000`.

**Verificado:** antes de regenerar, o contrato commitado estava sincronizado com a `main`
(47 paths, nenhum a mais nem a menos além das rotas novas desta sprint) — então o diff é
**aditivo e mínimo**, sem ruído de outra sprint.

## ADR-026-BE — Lançar usa a permissão `review_export`, sem chave nova (Sprint 7 / BACK 07.4)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `app/core/authz.py` (não alterado),
`app/modules/reconciliations/routes.py`

**Decisão:** o endpoint de lançamento usa `ReviewExportDep` — a permissão
`Permission.REVIEW_EXPORT` que já governa revisar/exportar. **Nenhuma chave nova** foi
adicionada à `PERMISSION_MATRIX`.

**Por quê:** lançar é o desfecho da revisão, não uma capacidade separada. Uma chave nova
nasceria com exatamente as mesmas 4 células preenchidas (`client_manager`,
`client_operator`, `admin`, `manager`) — uma linha a mais na matriz para manter, sem
distinguir ninguém de ninguém. A matriz é declarativa e ÚNICA justamente para não crescer
com sinônimos.

**Quando isso deixa de valer:** se o BPO decidir que lançar exige aprovação de um papel
superior (algo plausível para escrita em contabilidade), aí sim nasce uma chave nova — e
ela precisa de célula por papel, bloqueio no backend E ação oculta na tela (§4.9: mostrar
ação que o servidor nega é defeito). Registrado aqui para que a decisão seja revisada em
vez de herdada em silêncio.

## ADR-027-BE — Kill-switch de escrita nasce DESLIGADO (Sprint 7 / BACK 07.4)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `app/core/config.py`

**Decisão:** `OMIE_POSTING_ENABLED` tem default **`False`** — ao contrário do
`QUALIFICATION_ENABLED`, que a task cita como padrão e cujo default é `True`.

**Por que divergir do padrão citado:** a qualificação é leitura/análise; ligada por engano,
gasta token. Esta feature **grava movimento financeiro na contabilidade do cliente**, sobre
um contrato ainda NÃO-VERIFICADO contra a API real (S-1), e o critério de rollback da
sprint é "um único lançamento duplicado desliga o recurso". Um default `True` faria a
feature nascer ligada em todo ambiente que subisse o código — **inclusive antes de a
fixture da BACK 07.1 existir**. Ligar é decisão explícita, por ambiente.

**Operação:** `--update-env-vars OMIE_POSTING_ENABLED=true` no Cloud Run (que preserva as
vars manuais), sem deploy. Desligado, a rota devolve **409** com mensagem acionável —
4xx e não 5xx porque o recurso não falhou, está desligado (§7: erro de negócio é 4xx;
5xx mentiria "tente de novo" e poluiria o alerting com um estado esperado). 409 e não 403
porque não é sobre o papel do usuário.

**Teto de lote:** `OMIE_POSTING_MAX_BATCH=50`, validado NO SERVIDOR. O lote vai à Omie em
**sequência** (`X-Omie-ParallelRateLimit: 1/4` — paralelizar é punido com `1880`/`6`), e
cada linha carrega ~30s de budget de retry no pior caso; 50 é o que mantém o request
dentro do timeout do Cloud Run. Acima do teto: **400 VALIDATION_ERROR**, e não 422 como o
enunciado da task sugere — o handler global do repo mapeia validação para 400 (§9 do
PLANO), e inventar 422 só nesta rota quebraria o tratamento de erro do front, que é único.

## ADR-028-BE — Commit por linha: a memória do lançamento tem de sobreviver ao lote (Sprint 7 / BACK 07.4)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`app/modules/reconciliations/omie_posting/service.py`

**Defeito real, encontrado por teste de integração e não por leitura.** O padrão do repo é
UMA transação por request (`get_db_session` commita no fim, faz rollback em qualquer
exceção). Para toda rota anterior isso é correto — todas são reversíveis. Esta não é: no
meio do lote existe um **efeito externo irreversível** (o lançamento no Omie, e
`ExcluirLancCC` está fora de escopo).

Com uma transação só, qualquer exceção depois de um POST bem-sucedido — outra linha do
lote, o recálculo de contadores, um erro de rede — daria **rollback na memória de "esta
linha foi lançada"**. O Omie ficaria com o lançamento, o ADL não saberia, e a próxima
execução criaria **exatamente o duplicado que a sprint inteira existe para evitar**.

Foi assim que apareceu: `test_line_is_posted_and_reflected_on_the_reconciliation` deu 500
com `CryptoError: DEK do cliente ausente` ao cifrar a nota de resolução da anomalia — um
passo cosmético, depois do dinheiro já ter entrado.

**Decisão — duas barreiras de durabilidade por linha:**
1. **Antes do POST**, commit da intenção + `attempts`. "Eu tentei" vira fato antes de haver
   efeito externo; é isso que dispara a reconciliação na execução seguinte.
2. **Depois da confirmação** (e depois de cada desfecho terminal: `failed` por
   `faultstring`, vínculo em conflito), commit. Cada linha é durável por si.

O lote deixa de ser atômico — **de propósito**: os efeitos externos também não são.

**Decisão complementar — resolver a anomalia é fail-soft.** Cifrar a nota exige a DEK do
cliente (o `decrypt` ainda lê o legado bare, o `encrypt` não tem esse caminho). Cliente sem
DEK provisionada derrubaria a request DEPOIS do lançamento. A anomalia passa a ser
resolvida **sem a nota**, com `omie_posting_resolution_note_encrypt_failed` no log: perder
a nota é incomparavelmente mais barato que um duplicado, e continuar afirmando "não existe
no Omie" passou a ser falso.

## ADR-029-BE — Reconciliação pós-timeout tem TRÊS estados, e o terceiro nunca reenvia (Sprint 7 / BACK 07.4)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`omie_posting/service.py`, `LancamentoExtrato.c_cod_int_lanc`

**O problema.** O `OmieClient` retenta 5xx/timeout com backoff. Se a Omie aceitou o POST
que expirou, retentar duplica. O PRD manda "reconciliar via `ListarExtrato` pelo
`cCodIntLanc` e só reenviar se confirmar que não entrou".

**O obstáculo que o PRD não viu:** o `LancamentoExtrato` do repo **não declarava**
`cCodIntLanc`, e que o `ListarExtrato` devolva esse campo é parte de S-1 — não verificado.
Implementar "procura pela chave e, se não achar, reenvia" seria transformar uma suposição
não testada em autorização para gravar dinheiro duas vezes.

**Decisão.** O campo foi declarado como **opcional, default `None`, marcado
NÃO-VERIFICADO**, e a reconciliação devolve **três** estados:
- **found** → confirma pelo `nCodLanc` do extrato, sem novo POST (a linha volta como
  `lancada` com motivo `reconciliada`);
- **absent** → alguma linha do extrato trouxe `cCodIntLanc`, logo o campo existe, logo a
  ausência é informação: pode reenviar;
- **inconclusive** → **nenhuma** linha trouxe o campo. "Não achei" e "não sei olhar" são
  indistinguíveis → **não reenvia**, bloqueia com
  `envio_anterior_sem_confirmacao` e manda o operador conferir no Omie.

**Consequência.** Se a Omie não devolver `cCodIntLanc` no extrato, o recurso fica mais
conservador (uma linha travada exige conferência manual) — e **nunca** duplica. A alternativa
(casar por data+valor) quebraria exatamente no caso das duas compras idênticas, que é o
caso que a ADR-022-BE existe para proteger.

## ADR-030-BE — Falha de dependência no lote: per-linha, mas 5xx quando ninguém entrou (Sprint 7 / BACK 07.4)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `omie_posting/service.py`

**Tensão real entre duas regras.** O R5 pede resumo **por linha** (200 com o detalhe de
cada uma). A regra de engenharia diz "falha de dependência/fila/integração = 5xx, nunca
4xx/200 — 4xx é invisível ao alerting e mente ao usuário".

**Decisão.** Timeout/5xx/auth da Omie: (a) a linha volta como `erro` /
`omie_indisponivel`; (b) o lote **para** de bater na Omie (as restantes voltam com o mesmo
motivo — continuar só acionaria o rate limit e prolongaria a punição do `6 - Consumo
redundante`); (c) se **nenhuma** linha foi lançada, a exceção de dependência é
**relançada** e a resposta vira 5xx. Se pelo menos uma entrou, é 200 com o resumo — porque
aí houve resultado de negócio de verdade, e engolir isso num 5xx faria o operador achar que
nada foi lançado quando algo foi (e ele tentaria de novo).

`faultstring` **não** entra nessa regra: é erro de NEGÓCIO do provedor (categoria
inexistente, conta errada), falha definitiva daquela linha, e a mensagem verbatim volta ao
usuário na resposta — nunca no log (ADR-023-BE).

## ADR-031-BE — `faultstring` vira FAMÍLIA no sink; o texto integral fica fora (Sprint 7 / BACK 07.5)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`app/modules/usage_events/schemas.py`, `app/modules/usage_events/omie_rejection.py`

**O conflito, declarado.** O PRD instrumenta `omie_lancamento_rejeitado {codigo,
faultstring}`. Mas a whitelist de `props` deste módulo proíbe texto livre (`extra="forbid"`;
todo campo é `int`, `Literal` ou UUID) — e essa proibição é **a única coisa** que impede PII
de entrar na tabela de métrica. O `faultstring` é texto do fornecedor, e a Omie **ecoa
valores que enviamos**, inclusive o `cObs`, que carrega a descrição da compra (§4.5).

**Decisão: nem enfraquecer a whitelist, nem descartar a informação.**
- O texto integral **não entra no sink**. Ele já volta ao usuário na resposta do lote (é o
  que o torna acionável) e fica persistido em
  `reconciliation_omie_postings.error_message` (ADR-023-BE), que é onde é útil e está sob a
  cripto por cliente.
- No evento entra `categoria`: um `Literal` FECHADO
  (`categoria_invalida | conta_invalida | duplicidade | campo_invalido | credencial |
  indisponibilidade | outro`), derivado por `classify_omie_rejection` — função pura, com
  teste que injeta razão social e CNPJ na mensagem e prova que **nada** disso sobrevive na
  saída.
- `codigo` também é `Literal` (`OMIE_FAULT | OMIE_AUTH_ERROR | OMIE_TIMEOUT`), não `str`.

**Por que a família e não só o código.** O código diz "a Omie recusou"; a família responde
a pergunta que a leitura D+30 precisa fazer: se 80% das recusas forem `categoria_invalida`,
o gargalo é o passo de classificação (a suposição S-3), não o lançamento. `outro` crescendo
é sinal de que falta uma família — **não** um convite a gravar o texto.

**Dedup: nenhum dos dois entra em `DEDUPED_EVENT_NAMES`** (ADR-010 — evento novo nasce sem
dedup). O mesmo operador manda vários lotes na mesma sessão e **cada lote é um fato**;
deduplicar por `(event, session_id)` apagaria o 2º lote em silêncio e a métrica ficaria
menor que a realidade. Entrar na lista exigiria migration + a string batendo byte-a-byte em
3 lugares — e não há motivo para isso.

**Ambos ficam FORA de `CLIENT_EMITTED_EVENTS`** (que é allow-list, então já nascem
recusados pelo `POST /api/v1/usage-events`): `omie_lancamento_enviado` carrega numerador
(`sucesso`) **e** denominador (`linhas`) da métrica da sprint — aceitá-lo do browser
tornaria o resultado inverificável. Há teste negativo para os dois.

<!-- ===== agent-frontend ===== -->

## ADR-018-FE — Elegibilidade do lançamento é UM espelho declarado do servidor (Sprint 7 / FRONT 07.6)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`apps/web/src/components/features/reconciliations/review/omie-posting-eligibility.ts`

**Contexto.** Duas telas (Movimentações e Anomalias) precisam decidir se uma linha pode
ser lançada. A regra existe no backend (`_eligibility_block` +
`OmiePostingNotEligibleError`), e a tentação era escrever `if (situation === 'sem_omie')`
em cada aba — que é como a UI passa a oferecer uma ação que o servidor nega.

**Decisão.** Uma função (`getPostingBlock`), consultada pelas duas abas, com:

1. **A mesma ORDEM de precedência do servidor** (ignorada → já lançada → não é
   `sem_omie`). A ordem não é detalhe: quando duas condições valem, é ela que decide qual
   motivo o operador lê, e um motivo diferente do que voltaria no resumo do lote faz a
   tela contradizer o backend. Travado por teste.
2. **Motivos vindos do CONTRATO** (`Extract<OmiePostingLineReason, ...>`): renomear um
   motivo no backend para de compilar aqui em vez de virar copy divergente.
3. **Mensagens VERBATIM do backend.** Duas frases diferentes para o mesmo motivo fariam o
   operador achar que são coisas distintas.

O único motivo local é `sessao_nao_e_cartao` — no servidor ele não é motivo de LINHA, é
erro do lote inteiro (400); na tela é o que apaga a coluna em conta corrente.

**Consequência.** A UI continua não sendo barreira (o backend recusa de qualquer forma),
mas o par "ação oferecida" × "ação aceita" passa a ter uma fonte só.

## ADR-019-FE — "Lançada no Omie" é FATO OBSERVADO no lote, nunca inferido da listagem (Sprint 7 / FRONT 07.6)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `review/situation-badge.tsx`,
`review/movements-tab.tsx`

**Contexto.** A task pede badge de "lançada" na linha. O contrato da linha
(`ListedFileEntry`) **não tem** campo de "lançada pelo ADL": depois do envio o backend faz
`situation='conciliado'` + `omie_lancamento_id`, que é exatamente o estado de uma linha
que o MATCHER conciliou. Não há endpoint de leitura das intenções
(`reconciliation_omie_postings` só é escrito pelo POST do lote).

**Decisão.** O badge é alimentado pelo **resumo do lote** (`status='lancada'`, ou
`bloqueada/ja_lancada` com id) e vale enquanto a tela está aberta. Recarregou, a linha
volta a ser uma `conciliado` como outra qualquer.

**A alternativa recusada.** Inferir "lançada" de `conciliado + omie_lancamento_id`, ou do
par "anomalia `missing_in_omie` resolvida + id preenchido". Os dois marcam de "lançada"
linhas que o ADL nunca escreveu (o segundo basta o operador trocar o lançamento e resolver
a anomalia à mão). Num recurso cujo critério de rollback é "um único duplicado desliga",
dizer "o ADL escreveu no ERP" quando não escreveu é pior do que não dizer nada.

**O que fica persistente e verdadeiro:** a ação indisponível com o motivo "já está
vinculada a um lançamento do Omie" — que é o que o `omie_lancamento_id` de fato prova.

**Dívida declarada (para a próxima sprint):** um campo no `ListedFileEntry` (ex.:
`omie_posting_status`) fecharia o buraco. Está no HANDOFF para o backend.

## ADR-020-FE — Botão de ação bloqueada usa `aria-disabled`; `disabled` real fica para o envio (Sprint 7 / FRONT 07.6)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `review/lancar-no-omie-controls.tsx`

**Contexto.** O critério é "ação indisponível **com motivo acessível**
(`title`/`aria-describedby`)". Botão com `disabled` real **sai da ordem de foco**: quem
navega por teclado/leitor de tela nunca alcança o elemento e portanto nunca lê o
`aria-describedby` — o motivo vira decoração para quem enxerga o layout.

**Decisão.** Dois mecanismos, um por natureza de bloqueio:

- **Elegibilidade** (estado persistente da linha): `aria-disabled="true"` +
  `aria-describedby` apontando para o motivo, `onClick` inerte por guarda explícita
  (`aria-disabled` não impede o clique) e opacidade/cursor sinalizando visualmente.
- **Envio em andamento** (transitório): `disabled` de verdade. É ele que garante
  "duplo-clique não dispara duas requisições" — e o foco volta ao fim do envio, então
  ninguém fica preso fora da ordem de tabulação.

**Travado por teste:** o bloqueado continua `toBeEnabled()` (isto é, focável) e não chama
o handler; o `pending` está `toBeDisabled()` e dois cliques resultam em zero chamadas.

## ADR-021-FE — Não existe `AsyncButton` neste repo: o padrão é inline, e não se cria um segundo (Sprint 7 / FRONT 07.6)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `apps/web/src/components/**`

**Contexto.** A task manda "reusar o componente async já existente no projeto em vez de
criar outro". **Ele não existe** (grep vazio por `AsyncButton`): o que existe desde a S14 é
o PADRÃO `<Button disabled={isPending}>` + `<Loader2 className="animate-spin">`
(`detail/export-report-button.tsx`, `create-reconciliation-drawer`, ~10 call sites).

**Decisão.** Seguir o padrão existente. Extrair um `AsyncButton` agora criaria a segunda
convenção que a instrução quer evitar, e o refactor dos ~10 call sites não foi pedido.
Mesmo raciocínio da ADR-013-FE (`apiTyped` que não existe).

**Dívida declarada:** se um dia o padrão virar componente, o lugar é `ui/`, e a migração é
transversal — não numa feature só.

## ADR-022-FE — A aba de Anomalias lê a lista real de `sem_omie`; não deduz estado do código da anomalia (Sprint 7 / FRONT 07.6)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `review/anomalies-tab.tsx`,
`hooks/use-reconciliations.ts` (`useAllSemOmieEntries`)

**Contexto.** A ação também vive na aba de Anomalias, mas `AnomalyItem.related_file_entry`
traz só `{id, data, descrição, valor}` — **sem** `situation` e **sem**
`omie_lancamento_id`. O atalho seria "anomalia `missing_in_omie` não resolvida ⇒ linha
lançável". É falso: a anomalia continua aberta depois de o operador **ignorar** a linha, e
a tela ofereceria lançar o que o backend recusa.

**Decisão.** Buscar as linhas `sem_omie` da sessão pelo endpoint que já existe
(`file-entries?situation=sem_omie`, paginando internamente como
`useAllSessionAnomalies` faz) e cruzar por id. Só sessão de CARTÃO paga esses requests
(`enabled: isCard`), e a key vive sob o prefixo `['review', id, 'file-entries']`, então
qualquer PATCH ou lançamento já a invalida.

**Armadilha fechada no caminho:** duas anomalias podem apontar para a MESMA linha (valor
divergente + sem correspondente). O backend recusa o lote inteiro com 422 quando um
`file_entry_id` se repete, então a deduplicação acontece na montagem do lote, na tela.

## ADR-023-FE — Combobox próprio: o campo de busca mora DENTRO do popover (Sprint 7 / FRONT 07.7)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `apps/web/src/components/ui/combobox.tsx`,
`apps/web/src/components/ui/popover.tsx`

**Contexto.** O `<Select>` do Radix não filtra, e a classificação é sobre ~300 categorias do
Omie — a task pede busca por digitação, altura máxima com scroll e o padrão **APG de
combobox**. Não há `cmdk` no projeto e o `npx shadcn add` não alcança `ui.shadcn.com`
daqui; o `@radix-ui/react-popover` já estava nas dependências (e sem uso).

**Decisão.** Componente próprio em `ui/`, com o arranjo do shadcn: **gatilho é um botão**
(`aria-haspopup="listbox"` + `aria-expanded`) e o **campo de busca (`role="combobox"`) fica
DENTRO do popover**, junto da lista.

**Por que não o input fora do popover** (que seria o APG "ao pé da letra"): o popover é
`modal` — obrigatório, senão o `react-remove-scroll` da gaveta pai engole o `wheel` e a
lista não rola (learning do design-system) — e `modal` prende o foco no conteúdo. Com o
input fora, o `FocusScope` do Radix puxaria o foco de volta a cada tecla. Dentro, o padrão
APG vale inteiro: foco no campo, seleção por `aria-activedescendant`, opções não focáveis,
`Enter`/setas/`Home`/`End`.

**Dois achados MEDIDOS (não supostos), que valem para qualquer popover futuro:**

1. **O conteúdo do Popover do Radix é `role="dialog"`** — sem `aria-label` reprova
   `aria-dialog-name` (SERIOUS). O axe acusou na primeira execução da suíte da gaveta; a
   correção mora no combobox, que passa o próprio rótulo.
2. **A lista rolável NÃO precisa de `tabIndex`** aqui: o `scrollable-region-focusable`
   ignora popup de combobox por construção (`_isComboboxPopup` no matcher do axe-core
   4.12, lido no `node_modules`). É a exceção da regra, e existe justamente porque o
   teclado chega pelo `aria-activedescendant`.

**Consequência.** `Popover` nasce com `modal` como DEFAULT (não como opção que cada tela
lembra de ligar) — a decisão fica no componente, como manda o mesmo princípio do
`ScrollRegion`.

## ADR-024-FE — A gaveta não fecha no lote parcial: ela vira o resumo (Sprint 7 / FRONT 07.7)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `review/lancar-no-omie-drawer.tsx`

**Contexto.** O POST devolve **200 mesmo com falha parcial** — o desfecho por linha vem em
`lines[]`. Fechar a gaveta no sucesso, como fazem as gavetas de cadastro do projeto,
levaria embora exatamente o que o operador precisa ler.

**Decisão.** Uma gaveta, duas fases: antes do envio ela classifica; depois vira o **resumo
por linha** (status + motivo, com a mensagem VERBATIM do provedor em `erro_omie`) e o botão
primário passa a "Tentar novamente N de N". A gaveta só fecha quando **nada** ficou
pendente — e aí o botão da esquerda é "Concluir", nunca "Fechar" (o `X` do canto já se
chama Fechar; dois controles com o mesmo nome acessível na mesma gaveta são ambíguos).

**Reexecução sem duplicar:** a linha com `status='lancada'` sai do corpo do request
seguinte. A dedup do backend já a bloquearia, mas devolveria "bloqueada: já lançada" para
uma linha que o operador não pediu de novo — resumo poluído por construção nossa.

**Toast por desfecho, não por HTTP:** tudo lançado → `success`; nada lançado → `error`;
parcial → `warning`. Um `toast.success` num parcial diria "deu tudo certo" com dinheiro
faltando no ERP. Os três pares são os do `<Toaster>` global (tokens do tema, ADR-017-FE).

## ADR-025-FE — Sem sugestão automática de categoria: a UI não simula o que não existe (Sprint 7 / FRONT 07.7)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `review/lancar-no-omie-drawer.tsx`

**Contexto.** O PRD prevê reusar a qualificação (S19) ou o glossário (S6) para sugerir a
categoria, e a task pede "se houver sugestão automática, indicar a origem e mantê-la
editável; **se não houver, não simular sugestão**".

**Decisão.** Não há sugestão. Conferido no contrato gerado: nenhum endpoint da sprint
devolve categoria por linha — `ListedFileEntry` não tem campo de categoria sugerida, e
`AnomalyItem` (qualificação) carrega texto de contexto, não `cCodCateg`. A gaveta então
oferece **classificação em lote em 1 clique** como o acelerador real (é o que ataca o risco
S-3, "tempo por fatura"), e nenhuma linha nasce pré-preenchida.

**O que NÃO foi feito e por quê:** derivar a categoria do texto da qualificação seria
inventar um palpite e apresentá-lo com a autoridade de uma sugestão do sistema, num campo
que decide onde o dinheiro entra na contabilidade do cliente. Quando o backend expuser
sugestão de verdade, o lugar dela é o mesmo combobox, com a origem indicada.

<!-- ===== agent-review (QA) ===== -->

## ADR-014-QA — A prova de "zero duplicado" é a contagem de POSTs, não o número de linhas no banco (Sprint 7 / QA 07.8)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:**
`apps/api/tests/integration/test_omie_posting_qa_gate.py`

**O que a revisão encontrou.** A suíte do executor prova a dedup pelo **estado do ADL**
(`_posting_count(...) == 1`) e pelo desfecho da linha (`bloqueada/ja_lancada`). Os dois são
verdadeiros e nenhum dos dois vê o fio. `UNIQUE(file_entry_id)` garante **uma linha na
tabela** aconteça o que acontecer com a Omie — então um refactor que passasse a reenviar e
apenas mantivesse a linha intacta deixaria a suíte inteira verde e criaria o **duplicado no
ERP do cliente**, que é o único evento que desliga o recurso (critério de rollback do PRD).

**Decisão.** O gate do QA conta as chamadas a `incluir_lanc_cc` com um `monkeypatch` que
acumula os `cCodIntLanc` enviados, e assere sobre a LISTA: 3 envios da mesma linha → 1 POST;
reexecução de lote parcial → só a linha pendente vai ao fio; duas compras idênticas → 2
POSTs com 2 chaves distintas. A asserção que importa é `len(sent)`, não `count(*)`.

**Mutação que sustenta a afirmação.** Desligar `_decide_from_own_state` deixa
`test_timeout_then_reexecution_reconciles_instead_of_duplicating` e
`test_inconclusive_reconciliation_never_resends` **vermelhos** — o reenvio simples continua
verde porque `_eligibility_block` (a linha já virou `conciliado` com `omie_lancamento_id`) é
uma **segunda** barreira independente. Isso é defense-in-depth de verdade, não redundância:
as duas precisam cair para haver duplicado.

**Limite declarado.** Concorrência real (duas requisições simultâneas) **não** é testável
com a fixture `client_with_db`: ela compartilha UMA `AsyncSession` entre as requisições e um
`asyncio.gather` sobre ela quebra a conexão antes de exercitar o servidor (`InvalidRequestError:
this session is in 'prepared' state`). A garantia contra o duplo-clique **simultâneo** é o
`INSERT ... ON CONFLICT DO NOTHING` sobre `uq_recon_omie_postings_file_entry`, provado contra
Postgres real em `test_omie_postings.py::TestDatabaseEnforcesUniqueness`. Quem for reabrir
este ponto: o caminho é um teste com duas sessions/engines, não um `gather`.

## ADR-015-QA — Suíte "16 vermelhos" que some no 2º run é banco de teste sujo, não regressão (Sprint 7 / QA 07.8)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** operação do gate de integração local

**O quase-erro.** A 1ª execução da suíte de integração desta sprint deu **16 failed / 574
passed**, e os 16 eram todos de arquivos PRÉ-EXISTENTES (`test_clients`, `test_notifications`,
`test_qualification_*`). A leitura imediata — "as tasks novas poluem estado compartilhado e
deixam o CI vermelho" — era plausível, e teria reprovado a sprint inteira por um defeito que
não existe.

**O que provou o contrário.** Um controle equivalente ao `develop` (ignorando os 4 arquivos
NOVOS + `--deselect` da classe nova em `test_migrations.py`) deu **538 passed**; e a
**re-execução da suíte completa, sem mudar uma linha de código**, deu **590 passed**. O
banco `adl_pytest` é reusado entre execuções (`TEST_DATABASE_URL`), e a 1ª rodada do dia
carregava resíduo de sprints anteriores; ela mesma limpou o estado.

**Regra para o próximo QA.** Falha de integração local **só vira achado depois de
reproduzir**: rode 2x, e o controle contra a base é `--ignore`/`--deselect` do que a sprint
ADICIONOU (conferir com `git diff --name-status`: arquivo *modificado* ignorado por inteiro
falsifica o controle — foi o que quase aconteceu com `test_migrations.py`, que já existia).
No CI o Postgres é um service container novo a cada run, então este modo de falha é local.

## ADR-016-QA — Contrato regenerado se prova gerando, não comparando arquivos entre worktrees (Sprint 7 / QA 07.8)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** gate "contrato/tipos regenerados,
`git diff` = 0"

**Contexto.** `pnpm gen:types` aponta para `http://localhost:8000/openapi.json` e exige a API
no ar — o que não acontece dentro do sandbox do QA. A tentação é comparar o `schema.ts`
commitado pelo frontend com a cópia que sobrou no worktree do backend e chamar isso de prova;
não é: os dois podem estar igualmente desatualizados.

**Como o gate foi fechado de fato.** `app.openapi()` dumpado direto do app do backend
(sem servidor) → `pnpm exec openapi-typescript` no worktree do frontend → `diff` contra o
`schema.ts` commitado: **vazio**. É o mesmo pipeline do script, sem a dependência de rede.

**Consequência.** Este é o caminho a usar sempre que o gate de contrato precisar rodar num
ambiente sem a API no ar.

## ADR-026-FE — Rodapé de gaveta com três elementos quebra em vez de clipar; e o teste que mede isso espera a animação (Sprint 7 / FRONT 07.7, retrabalho)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** `review/lancar-no-omie-drawer.tsx`,
`e2e/a11y-mocked.spec.ts`

**Contexto.** O QA reprovou a 07.7 por um defeito **visual** que nenhum gate viu: em 390px o
botão primário da gaveta — o que grava na contabilidade do cliente — saía cortado na borda
direita. Causa: `SheetFooter` é `flex … justify-between` **sem `flex-wrap`**, e esta foi a
primeira gaveta a pôr TRÊS elementos lá (Cancelar + texto auxiliar "N compras ficam de
fora" + ação primária). Botão é `whitespace-nowrap`, então seu `min-content` é o texto
inteiro: ele não cede largura, e quem cedia era a viewport. O estado só existe quando há
compra sem categoria — ou seja, na abertura da gaveta.

**Decisão.**

1. `flex-wrap` **local**, na chamada do `SheetFooter` desta gaveta, e não no componente
   compartilhado: o `SheetFooter` rege as gavetas de conciliação e do glossário, que têm
   dois botões e não têm o problema — mudar o componente para consertar uma tela é trocar
   um defeito medido por um risco não medido em telas que ninguém pediu para tocar.
2. O grupo da direita ganha `min-w-0 flex-wrap justify-end`: o texto auxiliar sobe para a
   linha de cima e o botão desce inteiro, alinhado à direita. `shrink-0` no Cancelar para
   ele não ser espremido no lugar do botão.
3. O caso fica travado por **medição de caixa contra a viewport** no cenário mobile do
   `a11y-mocked.spec.ts`, antes do envio (`x + width ≤ viewport.width`, para as duas ações).

**Por que os gates não pegavam (e continuam não pegando sozinhos):** `axe-core` não mede
transbordo de layout — o `web_a11y` passou 154/154 com o botão cortado — e o teste de
componente roda em jsdom, que não tem layout. Só a caixa medida no browser reprova isso.

**A armadilha da medição.** A 1ª versão da assertiva reprovou os **quatro** cenários,
inclusive em 1440px (`1597 > 1440`): `toBeVisible()` resolve quando o nó entra na árvore,
mas a gaveta do Radix entra **deslizando**, e `boundingBox()` no meio do trajeto devolve
coordenada fora da tela que não é defeito nenhum. Daí o helper `aguardarAnimacao()`
(`getAnimations()` do próprio elemento — **sem** `subtree`, senão um spinner de
carregamento, que é animação infinita, faria o `finished` nunca resolver). Qualquer medida
geométrica dentro de gaveta/diálogo neste spec precisa passar por ele.

**Prova de que o teste morde (reproduzida pelo QA na re-revisão 1):** revertendo só os
`className` do rodapé e reconstruindo, o gate fecha **152 passed / 2 failed**, as duas
falhas com a mensagem `ação primária da gaveta cortada pela borda da viewport`; com o fix,
**154 passed, 0 critical/serious**.

## ADR-017-QA — Artefato gerado pelo gate é reprovação, mesmo com o defeito de origem corrigido (Sprint 7 / re-revisão 1)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** revisão de qualquer branch;
`apps/web/a11y-report.json`

**Contexto.** O commit de retrabalho da FRONT 07.7 (`4daefb4`) corrigiu o transbordo **e**
trouxe junto `apps/web/a11y-report.json` — 189 KB / 5441 linhas, a saída do reporter JSON do
próprio `scripts/a11y-gate.sh`, com 6 ocorrências do caminho absoluto do worktree do agent.
O arquivo não existe em `origin/develop` nem em `origin/main`; entrou nesta task. O CI produz
o mesmo arquivo e o **sobe como artifact** (`ci.yml:300-304`), nunca como fonte.

**Decisão.** Reprovar, e não aprovar-com-follow-up. O critério é a **irreversibilidade**:
aprovar significa pushar a branch, e o que entra no histórico da `develop`/`main` não sai
com `git rm` depois. Um `git rm --cached` + uma linha no `.gitignore` custam um commit ao
dono da task; limpar histórico custa reescrita de branch compartilhada.

**Causa-raiz que a correção tem de fechar.** O `.gitignore` cobre `playwright-report/` e
`test-results/` (linhas 93-94) mas **não** o `apps/web/a11y-report.json` que o gate escreve
na raiz do app — sem a regra, o arquivo volta no próximo `git add -A` de qualquer um.

**Verificação mecânica (o comando que decide o veredito):**
`git diff --name-only develop..HEAD` — qualquer `.json` de relatório na lista reprova.
Regra encodada no primer, §7 · Frontend.

## ADR-027-FE — O relatório do gate de a11y é artefato, não fonte (Sprint 7 / FRONT 07.7, retrabalho #2)

**Data:** 2026-08-18 · **Status:** ativo (com ressalva do QA, abaixo) · **Escopo:**
`.gitignore`, `apps/web/a11y-report.json`

**Contexto.** A 2ª reprovação da 07.7 não foi de UI: `apps/web/a11y-report.json` (189 KB,
5441 linhas) entrou no commit `4daefb4`. É a saída do reporter JSON do próprio gate
(`scripts/a11y-gate.sh` → `PLAYWRIGHT_JSON_OUTPUT_NAME="a11y-report.json"`), o mesmo arquivo
que o `ci.yml` publica **como artifact**. Não existe em `origin/develop` nem em `origin/main`
— nasceu nesta task, junto com a corrida do gate que provou a ADR-026-FE.

**Por que é defeito e não sujeira inofensiva:** (a) carrega 6 ocorrências do caminho absoluto
do worktree do agent — dado de máquina local vazando para o histórico; (b) é reescrito a cada
execução do gate, então todo dev que rodar `scripts/a11y-gate.sh` fica com a árvore suja e
conflito garantido nesse arquivo; (c) uma vez pushado, remover depois não tira do histórico.

**Decisão.** Duas partes: (1) tirar do índice e do disco (`git rm --cached` + `rm`) — como
entra e sai na MESMA branch, o par add+delete se anula e `git diff --name-only develop..HEAD`
volta a listar só código-fonte; (2) fechar a classe no `.gitignore`, junto de
`playwright-report/`, `test-results/` e `blob-report/`, porque o gate escreve na RAIZ do app,
fora dos diretórios já ignorados.

**Regra que fica:** artefato produzido por gate/CI nunca é fonte. Rodou gate, confira
`git status --short` antes de entregar — o de a11y, em particular, escreve DENTRO de
`apps/web/`, onde o olho espera só código.

**Ressalva do QA na consolidação (re-revisão 2).** A parte (1) está entregue e provada
(`7f89f3e`, 5441 deleções). A parte (2) **não entra no commit**: o `.gitignore` da raiz está
fora do `gitPaths` de todos os papéis, então a linha vive apenas no worktree descartável.
Ver **ADR-018-QA** e a task **`86e2w8xpv`**.

## ADR-018-QA — Prevenção fora do `gitPaths` não é prevenção: aprovar o commit, rastrear a classe (Sprint 7 / re-revisão 2)

**Data:** 2026-08-18 · **Status:** ativo · **Escopo:** vereditos do QA; `.gitignore` da raiz
e demais arquivos de raiz do monorepo

**Contexto.** A reprovação da re-revisão 1 (ADR-017-QA) pediu duas coisas ao dono da
FRONT 07.7: remover o artefato do commit **e** acrescentar a linha no `.gitignore`. O
retrabalho `7f89f3e` fez as duas. Só que `git diff develop..HEAD -- .gitignore` é **vazio**:
a linha aparece apenas como modificação não-commitada. O `commitOnBranch` do orquestrador só
faz `git add` nos `gitPaths` do papel (`orchestrate.js:648-712`), e o mapa
(`.agents-hub/config.env:81-84`) é `apps/api/` · `apps/web/` ·
`apps/api/tests/ apps/web/e2e/ apps/web/src/` · `docker/ .github/ scripts/ .env.example`.
O `.gitignore` da raiz não pertence a papel nenhum.

**Decisão.** (a) **Aprovar** a FRONT 07.7: o defeito que bloqueava o push — artefato no
histórico — está resolvido e verificado. Manter FAILED seria exigir do dono da task algo que
o pipeline não deixa entregar: rework infinito. (b) Não declarar a classe fechada: a
prevenção virou a task de infra **`86e2w8xpv`**, com as duas saídas commitáveis
(`scripts/a11y-gate.sh` escrevendo em `test-results/`, que já é ignorado, **ou**
`apps/web/.gitignore`, que está no `gitPaths` do frontend). (c) O veredito diz em voz alta
que a classe segue aberta, em vez de deixar o leitor supor que o `.gitignore` protege.

**Regra que fica (encodada em `.claude/agents/qa.md`).** Antes de escrever "edite o arquivo
X" numa reprovação — ou "Encodado em: X" numa lição —, confira que X cai no `gitPaths` do
papel dono: `grep AGENT_PATHS .agents-hub/config.env`. Zona de risco: a raiz do monorepo
(`.gitignore`, `package.json`, `pnpm-lock.yaml`). Prova depois do rework:
`git diff <base>..HEAD -- <arquivo>` **não** pode ser vazio.

**Nota de método.** O arquivo `.claude/agents/qa.md` do REPOSITÓRIO é a fonte do prompt do
papel — o orquestrador o copia para o `CLAUDE.md` do worktree a cada run
(`orchestrate.js:2325`) e nunca copia de volta. Encodar regra no `CLAUDE.md` do worktree é
cometer o mesmo erro que esta ADR descreve.
