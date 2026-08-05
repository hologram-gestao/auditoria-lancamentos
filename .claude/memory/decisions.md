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
