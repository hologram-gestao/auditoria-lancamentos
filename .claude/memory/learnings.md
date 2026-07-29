# Learnings — log de aprendizados (append-only)

> Log de incidentes/erros cometidos pelos agents (ou na operação) e a prevenção que
> ficou no lugar. **Blameless**: descreva o sistema/processo, nunca a pessoa/agent.
> Inspirado em post-mortem do Google SRE + Reflexion (lição em linguagem natural) +
> ADR (append-only, supersede em vez de editar).
>
> **Como usar:** rode `/retro` ao corrigir um erro — ele padroniza e roteia a entrada.
> Não edite entradas antigas; para revisar uma decisão, adicione nova e marque a
> anterior como `superseded-by`.
>
> ### Formato de cada entrada
> ```
> ## YYYY-MM-DD — <título curto> [escopo: <agent> | path/env]
> **Sintoma:** o que se observou (mensagem de erro, comportamento)
> **Causa-raiz:** por que aconteceu (sistema/processo, blameless)
> **Correção:** o que foi feito
> **Encodado em:** onde a prevenção vive (arquivo/hook/checklist/doc) — sem isto a
>   lição "morre em 2 semanas"
> **Status:** ativo | superseded-by <link/título>
> ```
> O campo **escopo** evita a overgeneralização do Reflexion (lição estreita virando
> regra global). O campo **Encodado em** garante o follow-through.

---

<!-- ▼ SEED — lições destiladas de produtos anteriores (genéricas). Já valem do dia 1;
     o QA deve cobrá-las. Substitua/expanda com as do seu produto via /retro. ▼ -->

## SEED — Contrato gerado pode falhar em silêncio [escopo: frontend | contrato]
**Sintoma:** front consumindo shape/campo que não existe mais; tela quebra em runtime.
**Causa-raiz:** o gerador de tipos (ex.: `gen:types`) rodou sem a ferramenta/contrato e
saiu com exit 0 → tipos desatualizados sem ninguém perceber (drift de contrato).
**Correção:** mudou schema → regerar o contrato **na mesma task**; CI/QA confere
`git diff` do arquivo de tipos = 0.
**Encodado em:** CLAUDE.md (Contrato é fonte única) + checklist do QA.
**Status:** ativo

## SEED — Schema de API externa "de memória" costuma estar errado [escopo: backend | integrações]
**Sintoma:** chamada a provedor externo falha/retorna vazio; payload/rota inventados.
**Causa-raiz:** o agent montou endpoint/campos pela "memória", não pela doc real.
**Correção:** usar a **doc/contrato oficial** do provedor; validar em **sandbox**;
inspecionar a **resposta real** uma vez para mapear as chaves; chave **idempotente** por entidade.
**Encodado em:** CLAUDE.md (Integrações externas) + DoD do QA.
**Status:** ativo

## SEED — Valor derivado calculado em 2 lugares diverge [escopo: backend | dados]
**Sintoma:** o mesmo valor (ex.: líquido, total) aparece diferente em telas/relatórios.
**Causa-raiz:** a fórmula foi reimplementada em paralelo em camadas diferentes.
**Correção:** **uma única fonte** calcula cada valor derivado; demais consomem.
**Encodado em:** CLAUDE.md (Contrato e consistência).
**Status:** ativo

## SEED — `cursor-pointer`/affordance some em novos componentes [escopo: frontend | design-system]
**Sintoma:** dropdowns/botões novos sem `cursor-pointer`; secundário cinza não parece clicável.
**Causa-raiz:** cada tela reaplicava o estilo; sem garantia no componente base.
**Correção:** `cursor-pointer` e estados no **componente base**; secundário com cor da paleta.
**Encodado em:** design-system.md (Componentes base) + DoD visual/a11y do QA.
**Status:** ativo

## SEED — Mudanças "não aparecem" durante migração/deploy [escopo: infra | operação]
**Sintoma:** feature mergeada não reflete no ambiente; dado novo ausente.
**Causa-raiz:** migração/deploy ainda em execução, ou migração não rodou no deploy.
**Correção:** confirmar deploy + migrations concluídos antes de validar; correção de
dados via migration idempotente.
**Encodado em:** CLAUDE.md (Banco, migrations e correções) + preflight/CI.
**Status:** ativo

<!-- ▼ SEEDs adicionais — gotchas destilados de projetos anteriores. Genéricos; o QA cobra. ▼ -->

## SEED — Método HTTP como string é opaco ao compilador [escopo: frontend | contrato]
**Sintoma:** chamada vira `405 Method Not Allowed` em runtime; troca POST↔PUT passa batida na revisão.
**Causa-raiz:** `apiFetch(url, {method:"POST"})` com URL crua não faz o `tsc` enxergar o método/rota.
**Correção:** cliente tipado (`apiTyped.METHOD("/path", ...)`) — método e shape viram parte do contrato compilado; método errado = erro de build.
**Encodado em:** .claude/agents/frontend.md (cliente tipado) + checklist do QA.
**Status:** ativo

## SEED — Label de enum "morto" só quebra no Postgres real [escopo: backend | dados]
**Sintoma:** `500 InvalidTextRepresentationError` em runtime após renomear labels de enum numa migration.
**Causa-raiz:** valor antigo sobreviveu em router/service/repo; `tsc`/mock/`grep` não exercitam o cast `::enum` — só o Postgres valida o texto contra os labels vivos.
**Correção:** após renomear labels, `grep` do label antigo e teste de integração em Postgres real.
**Encodado em:** .claude/agents/qa.md (teste de integração quando a lógica vive no banco).
**Status:** ativo

## SEED — Job de migration sem as secrets do serviço não sobe [escopo: infra | migrations]
**Sintoma:** o job de migration falha no boot (`ValidationError` do Settings) mesmo o SQL sendo trivial.
**Causa-raiz:** o `upgrade()` importa código de app (crypto/serviços) → importar o módulo constrói o Settings inteiro, exigindo todo campo obrigatório (ex.: `JWT_SECRET`, pepper, encryption key).
**Correção:** dar ao job de migration as MESMAS secrets do serviço; OU importar deps pesadas DENTRO de `upgrade()` (lazy). A encryption key do job DEVE ser a mesma do runtime (senão corrompe dado cifrado).
**Encodado em:** .claude/agents/infra.md (paridade de secrets serviço×jobs).
**Status:** ativo

## SEED — `export type { X }` em módulo server vira runtime error [escopo: frontend | Next.js]
**Sintoma:** `ReferenceError` no SSR; a página quebra em produção, não em dev.
**Causa-raiz:** em módulo `"use server"`, o transform SWC registra cada export specifier como server action → re-export nomeado de tipo vira referência a símbolo inexistente. `tsc` não pega.
**Correção:** usar `export type X = Y` (inline) ou manter tipos num módulo de contratos separado.
**Encodado em:** .claude/agents/frontend.md (Server Actions).
**Status:** ativo

## SEED — Secret em header HTTP com `\n` dá 401 [escopo: infra | segredos]
**Sintoma:** `401` intermitente/total numa integração que usa secret em header; "esperado ≠ recebido".
**Causa-raiz:** Secret Manager populado via `echo` deixa `\n` final e HTTP não transporta newline em header.
**Correção:** `.strip()` no secret nas DUAS pontas.
**Encodado em:** .claude/agents/infra.md (IAM/segredos).
**Status:** ativo

## SEED — Falha de dependência externa reportada como 4xx some do alerting [escopo: backend | resiliência]
**Sintoma:** integração/fila/dependência cai, usuário vê "tente de novo" que não resolve, e o alerting não dispara.
**Causa-raiz:** o handler devolveu 4xx (culpa do cliente) para uma falha do servidor; o alerting ignora 4xx.
**Correção:** falha de dependência = 5xx (503 + `Retry-After`). Erro de negócio do provedor = FALHA definitiva com a mensagem; só rate-limit/timeout retentam.
**Encodado em:** .claude/agents/backend.md (integrações/erro).
**Status:** ativo

## SEED — `.pyc` root-owned trava git depois [escopo: infra | Docker/WSL]
**Sintoma:** `git worktree remove`/`rm`/`checkout` falha com permissão negada em arquivos que ninguém criou à mão.
**Causa-raiz:** container com bind-mount rodando como root gravou `.pyc`/artefatos root-owned no path montado.
**Correção:** rodar o serviço como usuário não-root (`user: "1000:1000"`) + `PYTHONDONTWRITEBYTECODE=1`; `.gitignore` de artefatos ANTES do 1º commit.
**Encodado em:** .claude/agents/infra.md (container não escreve como root).
**Status:** ativo

## SEED — Merge ingênuo de branch defasada reintroduz regressão [escopo: infra | integração]
**Sintoma:** um bug já corrigido no trunk "volta" após integrar a branch de um executor.
**Causa-raiz:** merge arquivo-a-arquivo sobre base defasada; o conflito mascara qual lado é o correto.
**Correção:** rebase da branch do executor sobre o trunk (o trunk vence o drift); conflito real irreconciliável → escalar, nunca adivinhar. Log append-only usa `.gitattributes merge=union`.
**Encodado em:** orchestrate.js (mergeBranchInto resiliente) + templates `.gitattributes`.
**Status:** ativo

## SEED — CI que assume raiz quebra em projeto api/+web/ [escopo: infra | CI]
**Sintoma:** no 1º PR, jobs de CI falham em ~7s: `docker build` → "open Dockerfile: no such file or directory"; `setup-node` → "Dependencies lock file is not found" na raiz.
**Causa-raiz:** o workflow assume projeto Node + Dockerfile na RAIZ, mas o repo é `api/`+`web/` — e o Dockerfile que o CI builda nem foi commitado (deliverable de infra incompleto). Testes locais verdes não exercem o CI real, então passou no QA.
**Correção:** workflow aponta p/ as pastas reais (`working-directory`/`context`/`-f <dir>/Dockerfile`, `cache-dependency-path: <dir>/package-lock.json`); todo Dockerfile/artefato que o CI referencia é commitado na MESMA sprint; QA valida `gh pr checks` verde (não só teste local).
**Encodado em:** .claude/agents/infra.md (CI espelha o layout real) + .claude/agents/qa.md (CI é parte do DoD).
**Status:** ativo

## SEED — Cloud SQL: tier shared-core/custom exige `--edition=ENTERPRISE` [escopo: infra | GCP]
**Sintoma:** `setup-gcp.sh` falha no `sql instances create`: `Invalid Tier (db-g1-small / db-custom-2-7680) for (ENTERPRISE_PLUS) Edition`.
**Causa-raiz:** o Cloud SQL passou a **default para ENTERPRISE_PLUS**, que NÃO aceita tiers shared-core (`db-g1-small`) nem os `db-custom-*` antigos.
**Correção:** passar `--edition=ENTERPRISE` no create (mantém os tiers baratos/custom) OU usar tiers `db-perf-optimized-N-*` (ENTERPRISE_PLUS).
**Encodado em:** scripts/setup-gcp.sh (+ scaffold-infra dos hubs).
**Status:** ativo

## SEED — Cloud Run roda como a SA DEFAULT do compute (sem acesso a secrets) [escopo: infra | GCP]
**Sintoma:** deploy passa no build, mas o Job de migrations falha: `Permission denied on secret PII_ENC_KEY_dev ... service account 316...-compute@developer.gserviceaccount.com must be granted secretAccessor`.
**Causa-raiz:** sem `--service-account`, o serviço/job Cloud Run roda como a **default do compute**, que não tem `secretAccessor`. Dar o role só à SA de **deploy** não basta (deploy ≠ runtime).
**Correção:** SA de **runtime** dedicada (`<app>-run-<env>`) com `secretAccessor` **por secret** do env + `cloudsql.client`; passar `--service-account=<run-sa>` no migrate/api/web. Least-privilege e isola dev/prod.
**Encodado em:** scripts/setup-gcp.sh (SA runtime) + .github/workflows/_deploy.yml (`--service-account`).
**Status:** ativo

## SEED — IAM binding logo após criar SA dá "does not exist" [escopo: infra | GCP]
**Sintoma:** `add-iam-policy-binding` falha com `INVALID_ARGUMENT: Service account ... does not exist` logo após `iam service-accounts create` (na 1ª execução; re-rodar resolve).
**Causa-raiz:** criação de SA tem **consistência eventual** (~segundos); o binding imediato não enxerga a SA ainda.
**Correção:** `sleep ~10s` (ou retry) após criar a SA, antes dos bindings. Rodar de novo (idempotente) também completa.
**Encodado em:** scripts/setup-gcp.sh (sleep de propagação).
**Status:** ativo

## SEED — dev/prod no MESMO projeto GCP exige sufixo `<env>` em TUDO [escopo: infra | GCP]
**Sintoma:** risco de o banco/chaves/serviço de dev mesclar com prod (isolamento quebrado) num projeto só.
**Causa-raiz:** `setup-gcp.sh`/`_deploy.yml` escritos p/ "projetos separados" reusam os MESMOS nomes de secret/serviço → colidem num projeto só.
**Correção:** sufixar por `<env>`: Cloud SQL `<app>-<env>`, secrets `<NOME>_<env>`, serviços/job `<app>-{api,web,migrate}-<env>`, SAs `<app>-{deploy,run}-<env>`. Só o Artifact Registry e o WIF pool/provider são por-projeto.
**Encodado em:** scripts/setup-gcp.sh + _deploy.yml (+ scaffold-infra).
**Status:** ativo

## SEED — Cor de marca do brief descartada em favor do neutro shadcn [escopo: frontend | design-system]
**Sintoma:** UI sobe sem identidade — `--primary` cinza/preto (base shadcn neutra); botões/ações/sidebar sem a cor de marca que o produto definiu, mesmo o brief/PRD tendo a paleta.
**Causa-raiz:** o placeholder neutro do shadcn foi copiado para `design-system.md` e rotulado "fonte única da verdade das cores"; diante do conflito (neutro do design-system.md vs. cor de marca do `CONTEXT.md`/PRD/protótipo), o frontend seguiu o neutro e **descartou a marca**.
**Correção:** a cor de MARCA do projeto tem **precedência** e vai para `--primary`/tokens de marca (botões, ações, sidebar); transcreva a paleta de marca para `design-system.md` **e** `globals.css`. O neutro shadcn é só fallback quando NÃO há marca — nunca enshrine o placeholder neutro como "a verdade". Conflito resolve a favor da marca.
**Encodado em:** design-system.md (Cor e tema — precedência da marca) + .claude/agents/frontend.md (cor de marca manda no --primary).
**Status:** ativo

## SEED — Trabalho assíncrono validado só por teste, quebra no runtime de deploy [escopo: infra | resiliência]
**Sintoma:** feature com fila/worker/background passa nos testes de unidade/integração, mas no ambiente real não processa (ou trava eterno "processando").
**Causa-raiz:** o teste não exercita o RUNTIME de deploy. Ex.: no Cloud Run, task de fundo iniciada após a resposta HTTP é estrangulada pelo CPU throttling padrão; e o sweep de recuperação (heartbeat/timeout) "existe e é testado" mas não tem AGENDADOR real (Cloud Scheduler/startup) → nunca dispara.
**Correção:** verificar trabalho assíncrono contra o ALVO DE DEPLOY, não só testes — fila gerenciada + handler HTTP OU `--no-cpu-throttling` + `--min-instances>=1`; o recovery precisa de agendador real (não só existir). QA cobra "quem dispara isto em produção?".
**Encodado em:** .claude/agents/qa.md (gate de async contra o deploy) + .claude/agents/infra.md (modelo async real do backend).
**Status:** ativo

## SEED — Tarefa do QA com tag errada (`agent-qa`) fica órfã [escopo: infra | orquestração]
**Sintoma:** follow-up/checklist que o QA cria (ex.: gate de aprendizado) nunca é executado — fica OPEN sem ninguém agir, e o orquestrador não a reconhece.
**Causa-raiz:** o agent do QA se chama `agent-qa`, mas a TAG canônica das suas tasks é `agent-review`. Ao criar a task "com a tag do agent", o QA usa `agent-qa` → nenhum papel (backend/frontend/infra/review) a reconhece.
**Correção:** `create-followup` normaliza `agent-qa`→`agent-review`; o `qa.md` manda usar `agent-review` e encodar o aprendizado INLINE na sprint (não deferir a backlog órfão).
**Encodado em:** clickup-bridge.js (alias de tag no create-followup) + .claude/agents/qa.md (tag canônica + encode inline).
**Status:** ativo

## SEED — GitHub Free não tem required reviewers / proteção de Environment [escopo: infra | CI]
**Sintoma:** `gh api ... environments/prod ... reviewers` não aplica o gate; prod deploya sem aprovação.
**Causa-raiz:** required reviewers / regras de proteção de Environment (e branch protection em repo privado) exigem **plano pago** (Team/Enterprise).
**Correção:** gate de prod por **processo** — deploy de prod só `workflow_dispatch` manual (sem trigger automático), e só o dev responsável faz merge para `main` e dispara o prod. O Environment segue útil só p/ as **vars**.
**Encodado em:** .github/workflows/deploy-<app>-prod.yml (só workflow_dispatch) + runbook + infra.md.
**Status:** ativo

---

## 2026-07-28 — Memória do agent (`.claude/memory/`) apagada de novo: 3ª reconstituição das ADRs da Sprint 4 [escopo: infra | orquestração]
**Sintoma:** `.claude/memory/decisions.md` chegou nesta run **pela terceira vez** só com o stub `ADR-000`, e o `learnings.md` sem as entradas de 26/07 e 27/07 — apesar de o `HANDOFF.md` apontar o QA para as ADRs **por nome** (`ADR-006`). Quem lesse o handoff cairia num arquivo vazio.
**Causa-raiz:** `.claude/` é gitignored (`.gitignore:69`) e o worktree do agent é re-semeado a cada run do orquestrador — nada escrito lá entra no commit nem sobrevive. Não é erro do agent: ele não tem caminho para persistir.
**Correção (paliativo, dentro do meu escopo):** ADR-004 … ADR-007 reescritas a partir do **código commitado em `3e9fbaa`**, conferindo cada afirmação contra as docstrings de `usage_events/repository.py`, `reconciliations/totals.py`, `db/models/reconciliation_file.py`, `db/models/notification.py` e as migrations `a3c7e1f95d24`/`b8e2d4a71f36`/`c4f1a8b62e93` — nunca a partir da memória da conversa. Nada se perdeu de fato: as decisões vivem nas docstrings desses módulos, que **são** versionadas.
**Correção estrutural (fora do meu escopo de escrita):** não re-semear `.claude/memory/` quando já existe conteúdo, OU versionar a memória em `Docs/`. Já existe task de QA aberta para isto ("Memória dos agents (.claude/memory) é APAGADA a cada run").
**Encodado em:** este log + `decisions.md` (nota de procedência no topo do bloco reconstituído) + task de QA no board. **A prevenção real depende do orquestrador** — enquanto ela não existir, o padrão a seguir é: decisão que precisa sobreviver vai na **docstring do módulo** (versionada), e a ADR é o índice.
**Status:** ativo

---

## 2026-07-27 — Gate novo nasceu num arquivo que nenhuma esteira executa [escopo: qa | CI]
**Sintoma:** três vezes na mesma sprint um teste "existia" e não media nada: (1) o gate de a11y rodava só `Desktop Chrome` e o defeito `scrollable-region-focusable` (SERIOUS) só aparece em 390px; (2) `apps/web/e2e/` está fora do `AGENT_PATHS_QA`, então o spec que o job mede não entra no commit; (3) o cenário de teclado do modal foi acrescentado a `e2e/a11y.spec.ts`, que o CI **não** roda (`A11Y_SPEC: e2e/a11y-mocked.spec.ts`, `ci.yml:177`).
**Causa-raiz:** "escrevi o teste" e "alguma esteira executa o teste" são coisas diferentes, e nada no fluxo obrigava a segunda. O autor do teste não precisava nomear quem o roda.
**Correção:** cenário portado para `e2e/a11y-mocked.spec.ts` (o arquivo que o job mede), nos dois viewports, e provado **vermelho** contra o código antigo por mutação.
**Encodado em:** `CLAUDE.md` do QA (item novo na checklist de aprovação) + `ADR-008-QA` + cabeçalho do próprio `a11y-mocked.spec.ts` (sobrevive ao re-seed assim que `apps/web/e2e/` entrar no commit — task `86e2gjpbe`).
**Status:** ativo

## 2026-07-27 — Mock genérico de paginação derrubou a página e o axe passou a medir a tela de erro [escopo: qa | harness de teste]
**Sintoma:** "Detalhe da conciliação (R3)" reprovava com `document-title` e `html-has-lang` — violações ancoradas em `#__next_error__`.
**Causa-raiz:** o fallback genérico do `fulfillApi` devolvia `{data,pagination}` para toda rota, mas `/api/v1/omie/lancamentos` devolve **array puro**. `omieLookupQuery.data?.forEach` estourava (`TypeError: e.forEach is not a function`), a página caía no error boundary do Next e o axe media a **tela de erro**, não a tela sob teste.
**Correção:** rota explícita para `/omie/lancamentos` no mock.
**Encodado em:** este log + comentário no `a11y-mocked.spec.ts`. **Regra de leitura:** violação ancorada em `#__next_error__` = a página crashou; é bug de harness/produto, **nunca** defeito de a11y — investigar o crash antes de reprovar por a11y.
**Status:** ativo

## 2026-07-27 — CI vermelho que só existe contra a ÁRVORE COMMITADA [escopo: qa | CI]
**Sintoma:** dois vermelhos garantidos no 1º PR da Sprint 4, invisíveis em qualquer verificação local: `pnpm install --frozen-lockfile` abortando com `ERR_PNPM_OUTDATED_LOCKFILE` (o `pnpm-lock.yaml` da raiz ficou fora do commit) e `test -f apps/web/$A11Y_SPEC` falhando (o spec do gate não está versionado).
**Causa-raiz:** no worktree os dois arquivos EXISTEM — só que untracked. Todo comando rodado no worktree passa; o CI faz `checkout` e vê outra árvore. O `gitPaths` por papel cria o buraco: o `pnpm-lock.yaml` mora na raiz e o `apps/web/e2e/` está fora do escopo do QA, então nenhum agent consegue commitá-los.
**Correção:** o lockfile entrou em `eb1d713` (verificado: `d2bc76b` → exit 1 com `ERR_PNPM_OUTDATED_LOCKFILE`; `eb1d713` → exit 0, e os 3 comandos do job `web` passam — 189 testes). O spec segue pendente de `git add` (task `86e2gjpbe`, OPEN).
**Encodado em:** `ADR-009-QA` + `CLAUDE.md` do QA — revisão de CI começa por `git archive <commit> | tar -x` e reproduz o job em container contra ESSA árvore, com mutação contra o commit anterior.
**Status:** ativo
